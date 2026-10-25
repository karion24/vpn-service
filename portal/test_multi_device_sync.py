from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from portal.models import (
    Client,
    Device,
    Plan,
    RemnawaveServer,
    Subscription,
)


class MultiDeviceSyncTests(TestCase):

    def test_remote_change_updates_both_devices(self):
        user = get_user_model().objects.create_user(
            username="multi-device-sync-owner",
        )

        plan = Plan.objects.create(
            name="Multi Device Sync",
            code="multi-device-sync",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        server = RemnawaveServer.objects.create(
            name="Multi Device Server",
            code="multi-device-server",
            base_url="https://example.invalid",
            api_token="test-token",
        )

        old_expire = timezone.now() + timedelta(days=10)
        new_expire = old_expire + timedelta(days=20)

        client = Client.objects.create(
            username="multi-device-client",
            owner=user,
            remnawave_server=server,
            is_active=True,
            expires_at=old_expire,
        )

        subscription = Subscription.objects.create(
            client=client,
            plan=plan,
            status=Subscription.STATUS_ACTIVE,
            expires_at=old_expire,
            expires_synced_value=old_expire,
        )

        for number in (1, 2):
            Device.objects.create(
                client=client,
                name=f"Device {number}",
                remnawave_username=f"multi-device-{number}",
                remnawave_user_id=1000 + number,
                is_active=True,
            )

        remote_dates = {
            "multi-device-1": new_expire,
            "multi-device-2": old_expire,
        }

        def get_remote(username):
            return {
                "id": username,
                "expireAt": remote_dates[username].isoformat(),
                "updatedAt": timezone.now().isoformat(),
            }

        def update_remote(user_id, expires_at):
            remote_dates[user_id] = expires_at
            return {
                "id": user_id,
                "expireAt": expires_at.isoformat(),
                "status": "ACTIVE",
            }

        with patch(
            "portal.management.commands."
            "sync_expire_bidirectional.RemnawaveClient"
        ) as remnawave:
            remote = remnawave.return_value
            remote.get_user_by_username.side_effect = get_remote
            remote.update_user_expire_at.side_effect = update_remote

            call_command(
                "sync_expire_bidirectional",
                verbosity=0,
            )

        client.refresh_from_db()
        subscription.refresh_from_db()

        self.assertEqual(client.expires_at, new_expire)
        self.assertEqual(subscription.expires_at, new_expire)
        self.assertEqual(
            subscription.expires_synced_value,
            new_expire,
        )
        self.assertEqual(
            remote_dates["multi-device-1"],
            new_expire,
        )
        self.assertEqual(
            remote_dates["multi-device-2"],
            new_expire,
        )

    def test_payment_during_second_device_update(self):
        user = get_user_model().objects.create_user(
            username="multi-device-sync-owner",
        )

        plan = Plan.objects.create(
            name="Multi Device Sync",
            code="multi-device-sync",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        server = RemnawaveServer.objects.create(
            name="Multi Device Server",
            code="multi-device-server",
            base_url="https://example.invalid",
            api_token="test-token",
        )

        old_expire = timezone.now() + timedelta(days=10)
        new_expire = old_expire + timedelta(days=20)
        paid_expire = new_expire + timedelta(days=30)

        client = Client.objects.create(
            username="multi-device-client",
            owner=user,
            remnawave_server=server,
            is_active=True,
            expires_at=old_expire,
        )

        subscription = Subscription.objects.create(
            client=client,
            plan=plan,
            status=Subscription.STATUS_ACTIVE,
            expires_at=old_expire,
            expires_synced_value=old_expire,
        )

        for number in (1, 2):
            Device.objects.create(
                client=client,
                name=f"Device {number}",
                remnawave_username=f"multi-device-{number}",
                remnawave_user_id=1000 + number,
                is_active=True,
            )

        remote_dates = {
            "multi-device-1": new_expire,
            "multi-device-2": old_expire,
        }

        def get_remote(username):
            return {
                "id": username,
                "expireAt": remote_dates[username].isoformat(),
                "updatedAt": timezone.now().isoformat(),
            }

        payment_triggered = [False]

        def update_remote(user_id, expires_at):
            remote_dates[user_id] = expires_at

            if (
                user_id == "multi-device-2"
                and not payment_triggered[0]
            ):
                payment_triggered[0] = True

                Client.objects.filter(pk=client.pk).update(
                    expires_at=paid_expire,
                )
                Subscription.objects.filter(
                    pk=subscription.pk
                ).update(
                    expires_at=paid_expire,
                )

            return {
                "id": user_id,
                "expireAt": expires_at.isoformat(),
                "status": "ACTIVE",
            }

        with patch(
            "portal.management.commands."
            "sync_expire_bidirectional.RemnawaveClient"
        ) as remnawave:
            remote = remnawave.return_value
            remote.get_user_by_username.side_effect = get_remote
            remote.update_user_expire_at.side_effect = update_remote

            call_command(
                "sync_expire_bidirectional",
                verbosity=0,
            )

        client.refresh_from_db()
        subscription.refresh_from_db()

        self.assertTrue(payment_triggered[0])
        self.assertEqual(client.expires_at, paid_expire)
        self.assertEqual(subscription.expires_at, paid_expire)
        self.assertEqual(
            remote_dates["multi-device-1"],
            paid_expire,
        )
        self.assertEqual(
            remote_dates["multi-device-2"],
            paid_expire,
        )
