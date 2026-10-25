from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from portal.models import (
    Client, Device, Order, Plan,
    RemnawaveServer, Subscription,
)


class PaidSyncRaceTests(TestCase):

    def test_no_stale_remnawave_update_after_payment(self):
        user = get_user_model().objects.create_user(
            username="paid-network-race-user",
        )

        plan = Plan.objects.create(
            name="Network Race",
            code="paid-network-race",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        server = RemnawaveServer.objects.create(
            name="Network Race Server",
            code="paid-network-race-server",
            base_url="https://example.invalid",
            api_token="test-token",
        )

        old_expire = timezone.now() + timedelta(days=10)
        new_expire = old_expire + timedelta(days=30)
        synced_expire = old_expire - timedelta(days=1)

        client = Client.objects.create(
            username="paid-network-race-client",
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
            expires_synced_value=synced_expire,
        )

        Device.objects.create(
            client=client,
            name="Network Race Device",
            remnawave_username=client.username,
            remnawave_user_id=12345,
            is_active=True,
        )

        order = Order.objects.create(
            user=user,
            plan=plan,
            client=client,
            order_type=Order.TYPE_RENEWAL,
            status=Order.STATUS_PENDING,
            fulfillment_status=(
                Order.FULFILLMENT_ACTIVATION_PENDING
            ),
            amount=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        def remote_read(username):
            Order.objects.filter(pk=order.pk).update(
                status=Order.STATUS_PAID,
            )
            Client.objects.filter(pk=client.pk).update(
                expires_at=new_expire,
            )
            Subscription.objects.filter(
                pk=subscription.pk
            ).update(
                expires_at=new_expire,
            )

            return {
                "id": "network-race-user",
                "expireAt": synced_expire.isoformat(),
                "updatedAt": timezone.now().isoformat(),
            }

        with patch(
            "portal.management.commands."
            "sync_expire_bidirectional.RemnawaveClient"
        ) as remnawave:
            remote = remnawave.return_value
            remote.get_user_by_username.side_effect = remote_read

            call_command(
                "sync_expire_bidirectional",
                verbosity=0,
            )

            remote.update_user_expire_at.assert_not_called()

        client.refresh_from_db()
        subscription.refresh_from_db()

        self.assertEqual(client.expires_at, new_expire)
        self.assertEqual(subscription.expires_at, new_expire)

    def test_payment_during_remnawave_http_update(self):
        user = get_user_model().objects.create_user(
            username="paid-http-race-user",
        )

        plan = Plan.objects.create(
            name="HTTP Race",
            code="paid-http-race",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        server = RemnawaveServer.objects.create(
            name="HTTP Race Server",
            code="paid-http-race-server",
            base_url="https://example.invalid",
            api_token="test-token",
        )

        old_expire = timezone.now() + timedelta(days=10)
        new_expire = old_expire + timedelta(days=30)
        synced_expire = old_expire - timedelta(days=1)

        client = Client.objects.create(
            username="paid-http-race-client",
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
            expires_synced_value=synced_expire,
        )

        Device.objects.create(
            client=client,
            name="HTTP Race Device",
            remnawave_username=client.username,
            remnawave_user_id=12345,
            is_active=True,
        )

        order = Order.objects.create(
            user=user,
            plan=plan,
            client=client,
            order_type=Order.TYPE_RENEWAL,
            status=Order.STATUS_PENDING,
            fulfillment_status=Order.FULFILLMENT_ACTIVATION_PENDING,
            amount=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        remote_expire = [synced_expire]

        def remote_update(user_id, expires_at):
            # HTTP-запрос записал старый срок в Remnawave.
            remote_expire[0] = expires_at

            # Пока запрос выполнялся, поступила оплата.
            Order.objects.filter(pk=order.pk).update(
                status=Order.STATUS_PAID,
            )
            Client.objects.filter(pk=client.pk).update(
                expires_at=new_expire,
            )
            Subscription.objects.filter(
                pk=subscription.pk
            ).update(
                expires_at=new_expire,
            )

        with patch(
            "portal.management.commands."
            "sync_expire_bidirectional.RemnawaveClient"
        ) as remnawave:
            remote = remnawave.return_value

            remote.get_user_by_username.return_value = {
                "id": "http-race-user",
                "expireAt": synced_expire.isoformat(),
                "updatedAt": timezone.now().isoformat(),
            }

            remote.update_user_expire_at.side_effect = remote_update

            call_command(
                "sync_expire_bidirectional",
                verbosity=0,
            )

        client.refresh_from_db()
        subscription.refresh_from_db()

        self.assertEqual(client.expires_at, new_expire)
        self.assertEqual(subscription.expires_at, new_expire)

        # После гонки удалённый срок не должен
        # оставаться устаревшим.
        self.assertEqual(remote_expire[0], new_expire)

    def test_second_payment_during_remote_recovery(self):
        user = get_user_model().objects.create_user(
            username="paid-double-race-user",
        )

        plan = Plan.objects.create(
            name="Double Race",
            code="paid-double-race",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        server = RemnawaveServer.objects.create(
            name="Double Race Server",
            code="paid-double-race-server",
            base_url="https://example.invalid",
            api_token="test-token",
        )

        old_expire = timezone.now() + timedelta(days=10)
        first_expire = old_expire + timedelta(days=30)
        second_expire = first_expire + timedelta(days=30)
        synced_expire = old_expire - timedelta(days=1)

        client = Client.objects.create(
            username="paid-double-race-client",
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
            expires_synced_value=synced_expire,
        )

        Device.objects.create(
            client=client,
            name="Double Race Device",
            remnawave_username=client.username,
            remnawave_user_id=12345,
            is_active=True,
        )

        first_order = Order.objects.create(
            user=user,
            plan=plan,
            client=client,
            order_type=Order.TYPE_RENEWAL,
            status=Order.STATUS_PENDING,
            fulfillment_status=Order.FULFILLMENT_ACTIVATION_PENDING,
            amount=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        second_order = Order.objects.create(
            user=user,
            plan=plan,
            client=client,
            order_type=Order.TYPE_RENEWAL,
            status=Order.STATUS_PENDING,
            fulfillment_status=Order.FULFILLMENT_ACTIVATION_PENDING,
            amount=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        remote_expire = [synced_expire]
        update_count = [0]

        def remote_update(user_id, expires_at):
            update_count[0] += 1
            remote_expire[0] = expires_at

            if update_count[0] == 1:
                Order.objects.filter(pk=first_order.pk).update(
                    status=Order.STATUS_PAID,
                )
                Client.objects.filter(pk=client.pk).update(
                    expires_at=first_expire,
                )
                Subscription.objects.filter(
                    pk=subscription.pk
                ).update(
                    expires_at=first_expire,
                )

            elif update_count[0] == 2:
                Order.objects.filter(pk=second_order.pk).update(
                    status=Order.STATUS_PAID,
                )
                Client.objects.filter(pk=client.pk).update(
                    expires_at=second_expire,
                )
                Subscription.objects.filter(
                    pk=subscription.pk
                ).update(
                    expires_at=second_expire,
                )

        with patch(
            "portal.management.commands."
            "sync_expire_bidirectional.RemnawaveClient"
        ) as remnawave:
            remote = remnawave.return_value

            remote.get_user_by_username.return_value = {
                "id": "double-race-user",
                "expireAt": synced_expire.isoformat(),
                "updatedAt": timezone.now().isoformat(),
            }

            remote.update_user_expire_at.side_effect = remote_update

            call_command(
                "sync_expire_bidirectional",
                verbosity=0,
            )

        client.refresh_from_db()
        subscription.refresh_from_db()

        self.assertEqual(client.expires_at, second_expire)
        self.assertEqual(subscription.expires_at, second_expire)
        self.assertEqual(remote_expire[0], second_expire)

    def test_payment_during_remote_read_preserves_days(self):
        user = get_user_model().objects.create_user(
            username="paid-sync-race-user",
        )

        plan = Plan.objects.create(
            name="Paid Sync Race",
            code="paid-sync-race",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        server = RemnawaveServer.objects.create(
            name="Race Test Server",
            code="paid-race-server",
            base_url="https://example.invalid",
            api_token="test-token",
        )

        old_expire = timezone.now() + timedelta(days=10)
        new_expire = old_expire + timedelta(days=30)

        client = Client.objects.create(
            username="paid-sync-race-client",
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

        Device.objects.create(
            client=client,
            name="Race Device",
            remnawave_username=client.username,
            remnawave_user_id=12345,
            is_active=True,
        )

        order = Order.objects.create(
            user=user,
            plan=plan,
            client=client,
            order_type=Order.TYPE_RENEWAL,
            status=Order.STATUS_PENDING,
            fulfillment_status=(
                Order.FULFILLMENT_ACTIVATION_PENDING
            ),
            amount=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        def remote_read(username):
            # Оплата происходит после предварительной
            # проверки синхронизатора, но до записи в БД.
            Order.objects.filter(pk=order.pk).update(
                status=Order.STATUS_PAID,
            )

            Client.objects.filter(pk=client.pk).update(
                expires_at=new_expire,
            )

            Subscription.objects.filter(
                pk=subscription.pk
            ).update(
                expires_at=new_expire,
            )

            return {
                "id": "test-race-user",
                "expireAt": (
                    old_expire - timedelta(days=1)
                ).isoformat(),
                "updatedAt": timezone.now().isoformat(),
            }

        with patch(
            "portal.management.commands."
            "sync_expire_bidirectional.RemnawaveClient"
        ) as remnawave:
            remote = remnawave.return_value
            remote.get_user_by_username.side_effect = remote_read

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
            old_expire,
        )
