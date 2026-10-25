from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from portal.models import Client as VPNClient, Order, Payment, Plan
from portal.payment_confirmation import confirm_succeeded_payment


class PaymentReconciliationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="reconciliation-test-user",
        )
        cls.plan = Plan.objects.create(
            name="Reconciliation Test Plan",
            code="reconciliation-test-plan",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )
        cls.vpn_client = VPNClient.objects.create(
            username="reconciliation-test-client",
            owner=cls.user,
        )

    def make_order(self, status=Order.STATUS_PENDING):
        return Order.objects.create(
            user=self.user,
            plan=self.plan,
            client=self.vpn_client,
            order_type=Order.TYPE_RENEWAL,
            status=status,
            amount=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

    def make_payment(self, order, key):
        return Payment.objects.create(
            order=order,
            amount=Decimal("100.00"),
            status=Payment.STATUS_PENDING,
            provider="karion-payments",
            idempotency_key=key,
        )

    def report(self):
        output = StringIO()
        call_command(
            "check_payment_reconciliation",
            stdout=output,
        )
        return output.getvalue()

    def test_cancelled_order_payment_is_reported(self):
        order = self.make_order(Order.STATUS_CANCELLED)
        payment = self.make_payment(order, "reconciliation-cancelled")

        confirm_succeeded_payment(
            payment_id=payment.pk,
            confirmed_amount=Decimal("100.00"),
        )

        report = self.report()

        self.assertIn(f"Payment #{payment.pk}", report)
        self.assertIn(f"Order #{order.pk}", report)
        self.assertIn(
            "Успешных платежей отменённых заказов: 1",
            report,
        )

    def test_duplicate_successful_payments_are_reported(self):
        order = self.make_order()
        first = self.make_payment(order, "reconciliation-first")
        second = self.make_payment(order, "reconciliation-second")

        for payment in (first, second):
            confirm_succeeded_payment(
                payment_id=payment.pk,
                confirmed_amount=Decimal("100.00"),
            )

        report = self.report()

        self.assertIn(f"Order #{order.pk}", report)
        self.assertIn("successful_payments=2", report)
        self.assertIn(
            "Заказов с несколькими успешными платежами: 1",
            report,
        )
