from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from portal.expiration_sync import (
    ExpirationSyncChanged,
    sync_expired_subscription,
)
from django.test import TestCase
from django.utils import timezone

from portal.models import Client, Device, Plan, Subscription


class ExpirationHttpRaceTests(TestCase):

    def test_renewal_during_disable_restores_all_devices(self):
        user = get_user_model().objects.create_user(
            username="expiration-race-owner",
        )

        plan = Plan.objects.create(
            name="Expiration Race",
            code="expiration-race",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        expired_at = timezone.now() - timedelta(minutes=5)
        renewed_at = timezone.now() + timedelta(days=30)

        client = Client.objects.create(
            username="expiration-race-client",
            owner=user,
            is_active=True,
            expires_at=expired_at,
        )

        subscription = Subscription.objects.create(
            client=client,
            plan=plan,
            status=Subscription.STATUS_ACTIVE,
            expires_at=expired_at,
        )

        devices = []

        for index in (1, 2):
            devices.append(
                Device.objects.create(
                    client=client,
                    name=f"Race Device {index}",
                    is_active=True,
                    remnawave_username=f"race-device-{index}",
                    remnawave_user_id=1000 + index,
                )
            )

        remote = {
            1000 + index: {
                "status": "ACTIVE",
                "expire_at": expired_at,
            }
            for index in (1, 2)
        }

        class FakeRemnawave:
            disable_calls = 0

            def get_user_by_username(self, username):
                user_id = 1000 + int(
                    username.removeprefix("race-device-")
                )
                return {
                    "id": user_id,
                    "status": remote[user_id]["status"],
                }

            def disable_user(self, user_id):
                self.disable_calls += 1
                remote[user_id]["status"] = "DISABLED"

                if self.disable_calls == 1:
                    # Имитация продления во время HTTP-запроса.
                    Client.objects.filter(pk=client.pk).update(
                        expires_at=renewed_at,
                        is_active=True,
                    )
                    Subscription.objects.filter(
                        pk=subscription.pk
                    ).update(
                        expires_at=renewed_at,
                        status=Subscription.STATUS_ACTIVE,
                    )

                return {
                    "id": user_id,
                    "status": "DISABLED",
                }

            def update_user_expire_at(self, user_id, expires_at):
                remote[user_id]["expire_at"] = expires_at
                return {
                    "id": user_id,
                    "status": remote[user_id]["status"],
                }

            def enable_user(self, user_id):
                remote[user_id]["status"] = "ACTIVE"
                return {
                    "id": user_id,
                    "status": "ACTIVE",
                }

        fake = FakeRemnawave()

        # Имитируем состояние после фиксации истечения
        # подписки в базе данных.
        Subscription.objects.filter(
            pk=subscription.pk,
        ).update(status=Subscription.STATUS_EXPIRED)

        Client.objects.filter(
            pk=client.pk,
        ).update(is_active=False)

        with patch(
            "portal.expiration_sync._get_remnawave_client",
            return_value=fake,
        ):
            with self.assertRaises(ExpirationSyncChanged):
                sync_expired_subscription(client.pk)

        client.refresh_from_db()
        subscription.refresh_from_db()

        self.assertEqual(
            subscription.expires_at,
            renewed_at,
        )
        self.assertEqual(
            client.expires_at,
            renewed_at,
        )
        self.assertEqual(
            subscription.status,
            Subscription.STATUS_ACTIVE,
        )
        self.assertTrue(client.is_active)

        for device in devices:
            user_id = device.remnawave_user_id
            self.assertEqual(
                remote[user_id]["status"],
                "ACTIVE",
            )
            self.assertEqual(
                remote[user_id]["expire_at"],
                renewed_at,
            )

    def test_second_renewal_during_recovery(self):
        user = get_user_model().objects.create_user(
            username="expiration-double-owner",
        )

        plan = Plan.objects.create(
            name="Expiration Double Renewal",
            code="expiration-double-renewal",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        expired_at = timezone.now() - timedelta(minutes=5)
        first_expire = timezone.now() + timedelta(days=30)
        second_expire = first_expire + timedelta(days=30)

        client = Client.objects.create(
            username="expiration-double-client",
            owner=user,
            is_active=False,
            expires_at=expired_at,
        )

        subscription = Subscription.objects.create(
            client=client,
            plan=plan,
            status=Subscription.STATUS_EXPIRED,
            expires_at=expired_at,
        )

        for index in (1, 2):
            Device.objects.create(
                client=client,
                name=f"Double Device {index}",
                is_active=True,
                remnawave_username=f"double-device-{index}",
                remnawave_user_id=2000 + index,
            )

        remote = {
            2001: {"status": "ACTIVE", "expire_at": expired_at},
            2002: {"status": "ACTIVE", "expire_at": expired_at},
        }

        class FakeRemnawave:
            second_payment_done = False

            def get_user_by_username(self, username):
                user_id = 2000 + int(
                    username.removeprefix("double-device-")
                )
                return {
                    "id": user_id,
                    "status": remote[user_id]["status"],
                }

            def disable_user(self, user_id):
                remote[user_id]["status"] = "DISABLED"

                Client.objects.filter(pk=client.pk).update(
                    expires_at=first_expire,
                    is_active=True,
                )
                Subscription.objects.filter(
                    pk=subscription.pk
                ).update(
                    expires_at=first_expire,
                    status=Subscription.STATUS_ACTIVE,
                )

                return {
                    "id": user_id,
                    "status": "DISABLED",
                }

            def update_user_expire_at(self, user_id, expires_at):
                remote[user_id]["expire_at"] = expires_at

                if not self.second_payment_done:
                    self.second_payment_done = True

                    Client.objects.filter(pk=client.pk).update(
                        expires_at=second_expire,
                        is_active=True,
                    )
                    Subscription.objects.filter(
                        pk=subscription.pk
                    ).update(
                        expires_at=second_expire,
                        status=Subscription.STATUS_ACTIVE,
                    )

                return {
                    "id": user_id,
                    "status": remote[user_id]["status"],
                }

            def enable_user(self, user_id):
                remote[user_id]["status"] = "ACTIVE"
                return {
                    "id": user_id,
                    "status": "ACTIVE",
                }

        with patch(
            "portal.expiration_sync._get_remnawave_client",
            return_value=FakeRemnawave(),
        ):
            with self.assertRaises(ExpirationSyncChanged):
                sync_expired_subscription(client.pk)

        client.refresh_from_db()
        subscription.refresh_from_db()

        self.assertEqual(client.expires_at, second_expire)
        self.assertEqual(subscription.expires_at, second_expire)

        for user_id in (2001, 2002):
            self.assertEqual(
                remote[user_id]["status"],
                "ACTIVE",
            )
            self.assertEqual(
                remote[user_id]["expire_at"],
                second_expire,
            )

    def test_unlinked_device_is_not_restored(self):
        user = get_user_model().objects.create_user(
            username="expiration-unlink-owner",
        )

        plan = Plan.objects.create(
            name="Expiration Unlink",
            code="expiration-unlink",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        expired_at = timezone.now() - timedelta(minutes=5)
        renewed_at = timezone.now() + timedelta(days=30)

        client = Client.objects.create(
            username="expiration-unlink-client",
            owner=user,
            is_active=False,
            expires_at=expired_at,
        )

        subscription = Subscription.objects.create(
            client=client,
            plan=plan,
            status=Subscription.STATUS_EXPIRED,
            expires_at=expired_at,
        )

        device1 = Device.objects.create(
            client=client,
            name="Active Device",
            is_active=True,
            remnawave_username="unlink-active",
            remnawave_user_id=3001,
        )

        device2 = Device.objects.create(
            client=client,
            name="Unlinked Device",
            is_active=True,
            remnawave_username="unlink-second",
            remnawave_user_id=3002,
        )

        remote = {
            3001: {"status": "ACTIVE", "expire_at": expired_at},
            3002: {"status": "DISABLED", "expire_at": expired_at},
        }

        class FakeRemnawave:
            def get_user_by_username(self, username):
                user_id = (
                    3001 if username == "unlink-active" else 3002
                )
                return {
                    "id": user_id,
                    "status": remote[user_id]["status"],
                }

            def disable_user(self, user_id):
                remote[user_id]["status"] = "DISABLED"

                Client.objects.filter(pk=client.pk).update(
                    expires_at=renewed_at,
                    is_active=True,
                )
                Subscription.objects.filter(
                    pk=subscription.pk
                ).update(
                    expires_at=renewed_at,
                    status=Subscription.STATUS_ACTIVE,
                )

                # Во время HTTP-запроса пользователь
                # окончательно отвязал второе устройство.
                Device.objects.filter(pk=device2.pk).update(
                    is_active=False,
                    unlinked_at=timezone.now(),
                )

                return {
                    "id": user_id,
                    "status": "DISABLED",
                }

            def update_user_expire_at(self, user_id, expires_at):
                remote[user_id]["expire_at"] = expires_at
                return {
                    "id": user_id,
                    "status": remote[user_id]["status"],
                }

            def enable_user(self, user_id):
                remote[user_id]["status"] = "ACTIVE"
                return {
                    "id": user_id,
                    "status": "ACTIVE",
                }

        with patch(
            "portal.expiration_sync._get_remnawave_client",
            return_value=FakeRemnawave(),
        ):
            with self.assertRaises(ExpirationSyncChanged):
                sync_expired_subscription(client.pk)

        device1.refresh_from_db()
        device2.refresh_from_db()

        self.assertTrue(device1.is_active)
        self.assertFalse(device2.is_active)
        self.assertIsNotNone(device2.unlinked_at)

        self.assertEqual(remote[3001]["status"], "ACTIVE")
        self.assertEqual(remote[3001]["expire_at"], renewed_at)

        self.assertEqual(remote[3002]["status"], "DISABLED")
        self.assertEqual(remote[3002]["expire_at"], expired_at)

    def test_partial_remnawave_failure(self):
        from portal.remnawave import RemnawaveAPIError

        user = get_user_model().objects.create_user(
            username="expiration-failure-owner",
        )

        plan = Plan.objects.create(
            name="Expiration Failure",
            code="expiration-failure",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        expired_at = timezone.now() - timedelta(minutes=5)

        client = Client.objects.create(
            username="expiration-failure-client",
            owner=user,
            is_active=False,
            expires_at=expired_at,
        )

        subscription = Subscription.objects.create(
            client=client,
            plan=plan,
            status=Subscription.STATUS_EXPIRED,
            expires_at=expired_at,
        )

        for index in (1, 2):
            Device.objects.create(
                client=client,
                name=f"Failure Device {index}",
                is_active=True,
                remnawave_username=f"failure-device-{index}",
                remnawave_user_id=4000 + index,
            )

        remote = {
            4001: {"status": "ACTIVE"},
            4002: {"status": "ACTIVE"},
        }

        class FakeRemnawave:
            def get_user_by_username(self, username):
                user_id = 4000 + int(
                    username.removeprefix("failure-device-")
                )
                return {
                    "id": user_id,
                    "status": remote[user_id]["status"],
                }

            def disable_user(self, user_id):
                if user_id == 4002:
                    raise RemnawaveAPIError(
                        "HTTP 500: simulated Remnawave failure"
                    )

                remote[user_id]["status"] = "DISABLED"
                return {
                    "id": user_id,
                    "status": "DISABLED",
                }

        with patch(
            "portal.expiration_sync._get_remnawave_client",
            return_value=FakeRemnawave(),
        ):
            with self.assertRaises(RemnawaveAPIError):
                sync_expired_subscription(client.pk)

        client.refresh_from_db()
        subscription.refresh_from_db()

        self.assertFalse(client.is_active)
        self.assertEqual(
            subscription.status,
            Subscription.STATUS_EXPIRED,
        )

        self.assertEqual(
            remote[4001]["status"],
            "DISABLED",
        )
        self.assertEqual(
            remote[4002]["status"],
            "ACTIVE",
        )
