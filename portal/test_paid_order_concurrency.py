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


class PaidOrderConcurrencyTests(TransactionTestCase):

    def test_same_order_concurrent_execution(self):
        user = get_user_model().objects.create_user(
            username="paid-concurrent-user",
        )

        plan = Plan.objects.create(
            name="Concurrency",
            code="paid-concurrent",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        old_expire = timezone.now() + timedelta(days=10)

        client = Client.objects.create(
            username="paid-concurrent-client",
            owner=user,
            is_active=True,
            expires_at=old_expire,
        )

        Subscription.objects.create(
            client=client,
            plan=plan,
            status=Subscription.STATUS_ACTIVE,
            expires_at=old_expire,
            expires_synced_value=old_expire,
        )

        order = Order.objects.create(
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

        barrier = Barrier(2)

        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    apply_paid_order_entitlement(order.pk)
                    return "ok"
                except Exception as exc:
                    return type(exc).__name__
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(worker)
                for _ in range(2)
            ]
            results = [
                future.result(timeout=20)
                for future in futures
            ]

        client.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(
            client.expires_at,
            old_expire + timedelta(days=30),
        )

        self.assertEqual(
            order.fulfillment_status,
            Order.FULFILLMENT_SYNC_PENDING,
        )

        self.assertEqual(
            results.count("ok"),
            2,
            f"Оба вызова должны безопасно завершиться: {results}",
        )
