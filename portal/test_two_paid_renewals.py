from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone

from portal.models import Client, Order, Plan, Subscription
from portal.paid_order_execution import (
    apply_paid_order_entitlement,
)


class TwoPaidRenewalsTests(TransactionTestCase):

    def test_two_orders_for_same_subscription(self):
        user = get_user_model().objects.create_user(
            username="two-renewals-user",
        )

        plan = Plan.objects.create(
            name="Two Renewals",
            code="two-renewals",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        old_expire = timezone.now() + timedelta(days=10)

        client = Client.objects.create(
            username="two-renewals-client",
            owner=user,
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

        orders = [
            Order.objects.create(
                user=user,
                plan=plan,
                client=client,
                order_type=Order.TYPE_RENEWAL,
                status=Order.STATUS_PAID,
                fulfillment_status=(
                    Order.FULFILLMENT_ACTIVATION_PENDING
                ),
                amount=Decimal("100.00"),
                duration_days=30,
                device_limit=2,
            )
            for _ in range(2)
        ]

        barrier = Barrier(2)

        def worker(order_id):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                apply_paid_order_entitlement(order_id)
                return "ok"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(worker, order.pk)
                for order in orders
            ]
            results = [
                future.result(timeout=20)
                for future in futures
            ]

        client.refresh_from_db()
        subscription.refresh_from_db()

        expected = old_expire + timedelta(days=60)

        self.assertEqual(results, ["ok", "ok"])
        self.assertEqual(client.expires_at, expected)
        self.assertEqual(subscription.expires_at, expected)

        for order in orders:
            order.refresh_from_db()
            self.assertEqual(
                order.fulfillment_status,
                Order.FULFILLMENT_SYNC_PENDING,
            )
