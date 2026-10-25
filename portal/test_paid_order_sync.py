from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from portal.models import Client, Device, Order, Plan, Subscription


class PaidOrderSyncTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="paid-sync-test-user",
        )
        cls.plan = Plan.objects.create(
            name="Paid Sync Test",
            code="paid-sync-test",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

    def setUp(self):
        self.expiry = timezone.now() + timedelta(days=30)

        self.vpn_client = Client.objects.create(
            username="paid-sync-test-client",
            owner=self.user,
            expires_at=self.expiry,
            is_active=True,
        )

        self.subscription = Subscription.objects.create(
            client=self.vpn_client,
            plan=self.plan,
            status=Subscription.STATUS_ACTIVE,
            expires_at=self.expiry,
        )

        self.devices = [
            Device.objects.create(
                client=self.vpn_client,
                name=f"Device {number}",
                remnawave_username=f"paid-sync-device-{number}",
                remnawave_user_id=number + 1000,
                is_active=True,
            )
            for number in (1, 2)
        ]

        self.order = Order.objects.create(
            user=self.user,
            plan=self.plan,
            client=self.vpn_client,
            order_type=Order.TYPE_RENEWAL,
            status=Order.STATUS_PAID,
            fulfillment_status=Order.FULFILLMENT_SYNC_PENDING,
            amount=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

    def mock_rw(self):
        rw = MagicMock()
        rw.get_user_by_username.side_effect = (
            lambda username: {
                "id": (
                    1001 if username.endswith("-1") else 1002
                ),
                "status": "ACTIVE",
            }
        )
        rw.update_user_expire_at.return_value = {
            "status": "ACTIVE",
        }
        return rw

    def test_two_devices_receive_current_expiry(self):
        from portal.paid_order_sync import sync_paid_order

        rw = self.mock_rw()

        with patch(
            "portal.paid_order_sync._get_remnawave_client",
            return_value=rw,
        ):
            sync_paid_order(self.order.pk)

        calls = rw.update_user_expire_at.call_args_list

        self.assertEqual(len(calls), 2)
        self.assertEqual(
            {call.args[0] for call in calls},
            {1001, 1002},
        )
        self.assertTrue(
            all(call.args[1] == self.expiry for call in calls)
        )

        self.order.refresh_from_db()
        self.assertEqual(
            self.order.fulfillment_status,
            Order.FULFILLMENT_COMPLETED,
        )

    def test_remnawave_failure_keeps_order_pending(self):
        from portal.paid_order_sync import sync_paid_order

        rw = self.mock_rw()
        rw.update_user_expire_at.side_effect = RuntimeError(
            "Remnawave временно недоступен"
        )

        with patch(
            "portal.paid_order_sync._get_remnawave_client",
            return_value=rw,
        ):
            with self.assertRaises(RuntimeError):
                sync_paid_order(self.order.pk)

        self.order.refresh_from_db()
        self.assertEqual(
            self.order.fulfillment_status,
            Order.FULFILLMENT_SYNC_PENDING,
        )

    def test_unlinked_device_is_not_enabled(self):
        from portal.paid_order_sync import sync_paid_order

        rw = self.mock_rw()
        rw.get_user_by_username.side_effect = None
        rw.get_user_by_username.return_value = {
            "id": 1001,
            "status": "DISABLED",
        }

        unlinked = self.devices[1]
        unlinked.is_active = False
        unlinked.unlinked_at = timezone.now()
        unlinked.save(
            update_fields=[
                "is_active",
                "unlinked_at",
                "updated_at",
            ]
        )

        with patch(
            "portal.paid_order_sync._get_remnawave_client",
            return_value=rw,
        ):
            sync_paid_order(self.order.pk)

        rw.enable_user.assert_called_once_with(1001)

        queried_usernames = [
            call.args[0]
            for call in rw.get_user_by_username.call_args_list
        ]

        self.assertEqual(
            queried_usernames,
            ["paid-sync-device-1"],
        )

    def test_device_unlinked_during_http_is_not_reenabled(self):
        from portal.paid_order_sync import sync_paid_order

        rw = self.mock_rw()
        first = self.devices[0]
        second = self.devices[1]

        def remote_user(username):
            return {
                "id": 1001 if username.endswith("-1") else 1002,
                "status": (
                    "DISABLED" if username.endswith("-1")
                    else "ACTIVE"
                ),
            }

        rw.get_user_by_username.side_effect = remote_user

        updated_ids = []

        def update_expiry(user_id, expires_at):
            updated_ids.append(user_id)

            if user_id == 1001:
                first.is_active = False
                first.unlinked_at = timezone.now()
                first.save(
                    update_fields=[
                        "is_active",
                        "unlinked_at",
                        "updated_at",
                    ]
                )

            return {"status": "ACTIVE"}

        rw.update_user_expire_at.side_effect = update_expiry

        with patch(
            "portal.paid_order_sync._get_remnawave_client",
            return_value=rw,
        ):
            sync_paid_order(self.order.pk)

        self.order.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()

        self.assertFalse(first.is_active)
        self.assertIsNotNone(first.unlinked_at)
        self.assertTrue(second.is_active)

        rw.enable_user.assert_not_called()

        self.assertIn(1002, updated_ids)
        self.assertEqual(
            updated_ids.count(1001),
            1,
        )
        self.assertGreaterEqual(
            updated_ids.count(1002),
            1,
        )

        self.assertEqual(
            self.order.fulfillment_status,
            Order.FULFILLMENT_COMPLETED,
        )


    def test_sync_waits_for_subscription_lock(self):
        import threading
        import time

        from portal.paid_order_sync import sync_paid_order
        from portal.subscription_lock import subscription_lock

        rw = self.mock_rw()
        started = threading.Event()
        release = threading.Event()
        errors = []

        def hold_lock():
            try:
                with subscription_lock(self.order.client_id, timeout=3):
                    started.set()
                    release.wait(timeout=3)
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=hold_lock)
        thread.start()

        try:
            self.assertTrue(started.wait(timeout=3))

            with patch(
                "portal.paid_order_sync._get_remnawave_client",
                return_value=rw,
            ):
                # Пока блокировка занята, HTTP не выполняется.
                # Проверяем через отдельный поток освобождения.
                release.set()
                sync_paid_order(self.order.pk)

            self.assertFalse(errors)
            self.assertTrue(rw.update_user_expire_at.called)

            self.order.refresh_from_db()
            self.assertEqual(
                self.order.fulfillment_status,
                Order.FULFILLMENT_COMPLETED,
            )

        finally:
            release.set()
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())

    def test_lock_released_after_remnawave_error(self):
        from portal.paid_order_sync import sync_paid_order
        from portal.subscription_lock import subscription_lock

        rw = self.mock_rw()
        rw.update_user_expire_at.side_effect = RuntimeError(
            "Тестовая ошибка Remnawave"
        )

        with patch(
            "portal.paid_order_sync._get_remnawave_client",
            return_value=rw,
        ):
            with self.assertRaises(RuntimeError):
                sync_paid_order(self.order.pk)

        # После ошибки блокировка должна быть доступна.
        with subscription_lock(self.order.client_id, timeout=1):
            pass

        self.order.refresh_from_db()
        self.assertEqual(
            self.order.fulfillment_status,
            Order.FULFILLMENT_SYNC_PENDING,
        )

    def test_completed_order_is_idempotent(self):
        from portal.paid_order_sync import sync_paid_order

        self.order.fulfillment_status = Order.FULFILLMENT_COMPLETED
        self.order.save(
            update_fields=[
                "fulfillment_status",
                "updated_at",
            ]
        )

        with patch(
            "portal.paid_order_sync._get_remnawave_client"
        ) as get_rw:
            result = sync_paid_order(self.order.pk)

        self.assertEqual(
            result.fulfillment_status,
            Order.FULFILLMENT_COMPLETED,
        )
        get_rw.assert_not_called()

    def test_second_renewal_during_sync_is_not_lost(self):
        from portal.paid_order_sync import sync_paid_order

        rw = self.mock_rw()
        updated_expiry = self.expiry + timedelta(days=30)
        actual_remote_expiry = {}
        triggered = False

        def update_expiry(user_id, expires_at):
            nonlocal triggered

            actual_remote_expiry[user_id] = expires_at

            if not triggered:
                triggered = True

                self.vpn_client.expires_at = updated_expiry
                self.vpn_client.save(
                    update_fields=["expires_at", "updated_at"]
                )

                self.subscription.expires_at = updated_expiry
                self.subscription.save(
                    update_fields=["expires_at", "updated_at"]
                )

            return {"status": "ACTIVE"}

        rw.update_user_expire_at.side_effect = update_expiry

        with patch(
            "portal.paid_order_sync._get_remnawave_client",
            return_value=rw,
        ):
            sync_paid_order(self.order.pk)

        self.assertEqual(
            actual_remote_expiry,
            {
                1001: updated_expiry,
                1002: updated_expiry,
            },
        )

        self.order.refresh_from_db()
        self.assertEqual(
            self.order.fulfillment_status,
            Order.FULFILLMENT_COMPLETED,
        )
