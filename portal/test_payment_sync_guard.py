from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from portal.models import (
    Client as VPNClient,
    Device,
    Order,
    Plan,
    RemnawaveServer,
    Subscription,
)


class PaymentSyncGuardTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="sync_guard_user",
            password="test-password",
        )
        cls.plan = Plan.objects.create(
            name="Тест синхронизации",
            code="sync-guard-test",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )
        cls.server = RemnawaveServer.objects.create(
            name="Тестовый Remnawave",
            code="sync-guard-server",
            base_url="https://example.invalid",
            api_token="test-token",
        )

    def make_subscription(self, username):
        expires = timezone.now() + timedelta(days=30)

        client = VPNClient.objects.create(
            username=username,
            owner=self.user,
            remnawave_server=self.server,
            expires_at=expires,
        )

        Subscription.objects.create(
            client=client,
            plan=self.plan,
            status=Subscription.STATUS_ACTIVE,
            starts_at=timezone.now(),
            expires_at=expires,
            expires_synced_value=expires,
            price=Decimal("100.00"),
        )

        Device.objects.create(
            client=client,
            name="Тестовое устройство",
            remnawave_username=username,
            remnawave_user_id=12345,
            is_active=True,
        )

        return client

    def test_paid_renewal_skips_remnawave(self):
        client = self.make_subscription(
            "sync-guard-protected"
        )

        Order.objects.create(
            user=self.user,
            plan=self.plan,
            client=client,
            order_type=Order.TYPE_RENEWAL,
            status=Order.STATUS_PAID,
            fulfillment_status=Order.FULFILLMENT_SYNC_PENDING,
            amount=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        with patch(
            "portal.management.commands."
            "sync_expire_bidirectional.RemnawaveClient"
        ) as remnawave:
            call_command(
                "sync_expire_bidirectional",
                verbosity=0,
            )

        remnawave.assert_not_called()

    def test_concurrent_extension_must_not_be_overwritten(self):
        client = self.make_subscription(
            "sync-guard-concurrent"
        )

        subscription = Subscription.objects.get(client=client)
        old_expire = subscription.expires_at
        new_expire = old_expire + timedelta(days=30)

        # Локальный срок изменён, удалённый ещё старый.
        initial_synced = old_expire - timedelta(days=1)

        Subscription.objects.filter(pk=subscription.pk).update(
            expires_synced_value=initial_synced,
        )

        def remote_update(user_id, expires_at):
            # Имитируем оплату во время сетевого запроса.
            Subscription.objects.filter(
                pk=subscription.pk
            ).update(expires_at=new_expire)

            VPNClient.objects.filter(
                pk=client.pk
            ).update(expires_at=new_expire)

        with patch(
            "portal.management.commands."
            "sync_expire_bidirectional.RemnawaveClient"
        ) as remnawave:
            remote = remnawave.return_value
            remote.get_user_by_username.return_value = {
                "id": "test-user-id",
                "expireAt": initial_synced.isoformat(),
                "updatedAt": timezone.now().isoformat(),
            }
            remote.update_user_expire_at.side_effect = remote_update

            call_command(
                "sync_expire_bidirectional",
                verbosity=0,
            )

        subscription.refresh_from_db()
        client.refresh_from_db()

        self.assertEqual(client.expires_at, new_expire)
        self.assertEqual(subscription.expires_at, new_expire)

        # Старый срок нельзя отмечать синхронизированным,
        # когда локальный срок уже изменился.
        self.assertNotEqual(
            subscription.expires_synced_value,
            old_expire,
        )

    def test_regular_subscription_reaches_remnawave(self):
        self.make_subscription("sync-guard-regular")

        with patch(
            "portal.management.commands."
            "sync_expire_bidirectional.RemnawaveClient"
        ) as remnawave:
            remnawave.return_value.get_user_by_username.return_value = {
                "id": "test-user-id",
                "expireAt": (
                    timezone.now() + timedelta(days=30)
                ).isoformat(),
                "updatedAt": timezone.now().isoformat(),
            }

            call_command(
                "sync_expire_bidirectional",
                verbosity=0,
            )

        remnawave.assert_called()
        remnawave.return_value.get_user_by_username.assert_called()
