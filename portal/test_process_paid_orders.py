from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from portal.models import Client, Order, Plan, Subscription


class ProcessPaidOrdersTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="paid-queue-test-user",
        )
        cls.plan = Plan.objects.create(
            name="Paid Queue Test",
            code="paid-queue-test",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

    def setUp(self):
        self.initial_expiry = timezone.now() + timedelta(days=10)
        self.vpn_client = Client.objects.create(
            username="paid-queue-test-client",
            owner=self.user,
            expires_at=self.initial_expiry,
            is_active=True,
        )
        self.subscription = Subscription.objects.create(
            client=self.vpn_client,
            plan=self.plan,
            status=Subscription.STATUS_ACTIVE,
            expires_at=self.initial_expiry,
        )

    def make_order(self, status=None, fulfillment=None):
        return Order.objects.create(
            user=self.user,
            plan=self.plan,
            client=self.vpn_client,
            order_type=Order.TYPE_RENEWAL,
            status=status or Order.STATUS_PAID,
            fulfillment_status=(
                fulfillment or Order.FULFILLMENT_ACTIVATION_PENDING
            ),
            amount=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

    def run_queue(self, *args):
        output = StringIO()
        errors = StringIO()
        call_command(
            "process_paid_orders",
            *args,
            stdout=output,
            stderr=errors,
        )
        return output.getvalue(), errors.getvalue()

    def test_dry_run_does_not_extend_subscription(self):
        order = self.make_order()
        output, errors = self.run_queue()

        self.vpn_client.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(
            self.vpn_client.expires_at,
            self.initial_expiry,
        )
        self.assertEqual(
            order.fulfillment_status,
            Order.FULFILLMENT_ACTIVATION_PENDING,
        )
        self.assertIn("DRY-RUN", output)
        self.assertEqual(errors, "")

    def test_apply_extends_subscription_once(self):
        order = self.make_order()

        self.run_queue("--apply")
        self.run_queue("--apply")

        self.vpn_client.refresh_from_db()
        self.subscription.refresh_from_db()
        order.refresh_from_db()

        expected = self.initial_expiry + timedelta(days=30)

        self.assertEqual(self.vpn_client.expires_at, expected)
        self.assertEqual(self.subscription.expires_at, expected)
        self.assertEqual(
            order.fulfillment_status,
            Order.FULFILLMENT_SYNC_PENDING,
        )

    def test_cancelled_order_is_not_processed(self):
        order = self.make_order(status=Order.STATUS_CANCELLED)

        self.run_queue("--apply")

        self.vpn_client.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(
            self.vpn_client.expires_at,
            self.initial_expiry,
        )
        self.assertEqual(
            order.fulfillment_status,
            Order.FULFILLMENT_ACTIVATION_PENDING,
        )

    def test_failure_does_not_stop_next_order(self):
        first = self.make_order()
        second = self.make_order()

        from portal.management.commands import process_paid_orders

        real_apply = process_paid_orders.apply_paid_order_entitlement

        def selective_apply(order_id):
            if order_id == first.pk:
                raise RuntimeError("Искусственная ошибка теста")
            return real_apply(order_id)

        with patch(
            "portal.management.commands.process_paid_orders."
            "apply_paid_order_entitlement",
            side_effect=selective_apply,
        ):
            output, errors = self.run_queue("--apply")

        first.refresh_from_db()
        second.refresh_from_db()
        self.vpn_client.refresh_from_db()

        self.assertEqual(
            first.fulfillment_status,
            Order.FULFILLMENT_ACTIVATION_PENDING,
        )
        self.assertEqual(
            second.fulfillment_status,
            Order.FULFILLMENT_SYNC_PENDING,
        )
        self.assertEqual(
            self.vpn_client.expires_at,
            self.initial_expiry + timedelta(days=30),
        )
        self.assertIn("Ошибок: 1", output)
        self.assertIn("Искусственная ошибка теста", errors)
