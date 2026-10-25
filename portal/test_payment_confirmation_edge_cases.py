from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from portal.models import Client as VPNClient, Order, Payment, Plan
from portal.payment_confirmation import (
    PaymentConfirmationError,
    confirm_succeeded_payment,
)


class PaymentConfirmationEdgeCaseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="confirmation-edge-user",
        )
        cls.plan = Plan.objects.create(
            name="Confirmation Edge Plan",
            code="confirmation-edge-plan",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )
        cls.vpn_client = VPNClient.objects.create(
            username="confirmation-edge-client",
            owner=cls.user,
        )

    def setUp(self):
        self.order = Order.objects.create(
            user=self.user,
            plan=self.plan,
            client=self.vpn_client,
            order_type=Order.TYPE_RENEWAL,
            amount=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

    def make_payment(self, key):
        return Payment.objects.create(
            order=self.order,
            amount=Decimal("100.00"),
            status=Payment.STATUS_PENDING,
            provider="karion-payments",
            idempotency_key=key,
        )

    def test_two_payments_for_same_order(self):
        first = self.make_payment("edge-payment-first")
        second = self.make_payment("edge-payment-second")

        confirm_succeeded_payment(
            payment_id=first.pk,
            confirmed_amount=Decimal("100.00"),
        )

        confirm_succeeded_payment(
            payment_id=second.pk,
            confirmed_amount=Decimal("100.00"),
        )

        self.order.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()

        self.assertEqual(self.order.status, Order.STATUS_PAID)
        self.assertEqual(
            self.order.fulfillment_status,
            Order.FULFILLMENT_ACTIVATION_PENDING,
        )
        self.assertEqual(first.status, Payment.STATUS_SUCCEEDED)
        self.assertEqual(second.status, Payment.STATUS_SUCCEEDED)
        self.assertIsNotNone(second.paid_at)

    def test_expired_pending_order_with_confirmed_payment(self):
        from datetime import timedelta
        from django.utils import timezone

        payment = self.make_payment("edge-expired-pending")
        self.order.payment_expires_at = timezone.now() - timedelta(minutes=1)
        self.order.save(update_fields=["payment_expires_at"])

        confirm_succeeded_payment(
            payment_id=payment.pk,
            confirmed_amount=Decimal("100.00"),
        )

        self.order.refresh_from_db()
        payment.refresh_from_db()

        self.assertEqual(self.order.status, Order.STATUS_PAID)
        self.assertEqual(payment.status, Payment.STATUS_SUCCEEDED)

    def test_cancelled_order_requires_reconciliation(self):
        payment = self.make_payment("edge-cancelled-order")
        self.order.status = Order.STATUS_CANCELLED
        self.order.save(update_fields=["status"])

        confirm_succeeded_payment(
            payment_id=payment.pk,
            confirmed_amount=Decimal("100.00"),
        )

        self.order.refresh_from_db()
        payment.refresh_from_db()

        self.assertEqual(self.order.status, Order.STATUS_CANCELLED)
        self.assertEqual(payment.status, Payment.STATUS_SUCCEEDED)

    def test_confirmed_payment_for_cancelled_order_is_recorded(self):
        payment = self.make_payment("edge-cancelled-but-paid")
        self.order.status = Order.STATUS_CANCELLED
        self.order.save(update_fields=["status"])

        confirm_succeeded_payment(
            payment_id=payment.pk,
            confirmed_amount=Decimal("100.00"),
        )

        self.order.refresh_from_db()
        payment.refresh_from_db()

        self.assertEqual(payment.status, Payment.STATUS_SUCCEEDED)
        self.assertIsNotNone(payment.paid_at)
        self.assertEqual(self.order.status, Order.STATUS_CANCELLED)
        self.assertEqual(
            self.order.fulfillment_status,
            Order.FULFILLMENT_PENDING,
        )

    def test_repeated_confirmation_of_cancelled_order_payment(self):
        payment = self.make_payment("edge-cancelled-repeat")
        self.order.status = Order.STATUS_CANCELLED
        self.order.save(update_fields=["status"])

        first = confirm_succeeded_payment(
            payment_id=payment.pk,
            confirmed_amount=Decimal("100.00"),
        )

        first_paid_at = first.paid_at

        second = confirm_succeeded_payment(
            payment_id=payment.pk,
            confirmed_amount=Decimal("100.00"),
        )

        payment.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(payment.status, Payment.STATUS_SUCCEEDED)
        self.assertEqual(payment.paid_at, first_paid_at)
        self.assertEqual(second.paid_at, first_paid_at)
        self.assertEqual(self.order.status, Order.STATUS_CANCELLED)
        self.assertEqual(
            self.order.fulfillment_status,
            Order.FULFILLMENT_PENDING,
        )
        self.assertEqual(
            Payment.objects.filter(
                order=self.order,
                status=Payment.STATUS_SUCCEEDED,
            ).count(),
            1,
        )

    def test_failed_payment_cannot_be_confirmed(self):
        payment = self.make_payment("edge-payment-failed")
        payment.status = Payment.STATUS_FAILED
        payment.save(update_fields=["status"])

        with self.assertRaises(PaymentConfirmationError):
            confirm_succeeded_payment(
                payment_id=payment.pk,
                confirmed_amount=Decimal("100.00"),
            )

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.STATUS_PENDING)

    def test_zero_amount_is_rejected(self):
        payment = self.make_payment("edge-payment-zero")

        with self.assertRaises(PaymentConfirmationError):
            confirm_succeeded_payment(
                payment_id=payment.pk,
                confirmed_amount=Decimal("0.00"),
            )

    def test_invalid_amount_is_rejected(self):
        payment = self.make_payment("edge-payment-invalid")

        with self.assertRaises(PaymentConfirmationError):
            confirm_succeeded_payment(
                payment_id=payment.pk,
                confirmed_amount="not-a-number",
            )
