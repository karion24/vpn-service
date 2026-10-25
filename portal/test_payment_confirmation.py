from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from portal.models import Client as VPNClient, Order, Payment, Plan


class PaymentConfirmationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="payment-confirmation-test",
        )
        cls.plan = Plan.objects.create(
            name="Payment Confirmation Test",
            code="payment-confirmation-test",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )
        cls.vpn_client = VPNClient.objects.create(
            username="payment-confirmation-client",
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
        self.payment = Payment.objects.create(
            order=self.order,
            amount=Decimal("100.00"),
            status=Payment.STATUS_PENDING,
            provider="karion-payments",
            idempotency_key=f"confirmation-test-{self.order.pk}",
        )

    def confirm(self, amount=Decimal("100.00")):
        from portal.payment_confirmation import confirm_succeeded_payment

        return confirm_succeeded_payment(
            payment_id=self.payment.pk,
            confirmed_amount=amount,
        )

    def test_successful_confirmation(self):
        self.confirm()

        self.payment.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            Payment.STATUS_SUCCEEDED,
        )
        self.assertEqual(
            self.order.status,
            Order.STATUS_PAID,
        )
        self.assertEqual(
            self.order.fulfillment_status,
            Order.FULFILLMENT_ACTIVATION_PENDING,
        )
        self.assertIsNotNone(self.payment.paid_at)
        self.assertIsNotNone(self.order.paid_at)

    def test_repeat_confirmation_is_idempotent(self):
        self.confirm()
        self.payment.refresh_from_db()
        first_paid_at = self.payment.paid_at

        self.confirm()
        self.payment.refresh_from_db()

        self.assertEqual(self.payment.paid_at, first_paid_at)
        self.assertEqual(
            Payment.objects.filter(order=self.order).count(),
            1,
        )

    def test_wrong_amount_is_rejected(self):
        from portal.payment_confirmation import PaymentConfirmationError

        with self.assertRaises(PaymentConfirmationError):
            self.confirm(Decimal("99.00"))

        self.order.refresh_from_db()
        self.payment.refresh_from_db()

        self.assertEqual(self.order.status, Order.STATUS_PENDING)
        self.assertEqual(self.payment.status, Payment.STATUS_PENDING)

    def test_cancelled_order_is_not_activated(self):
        from portal.payment_confirmation import PaymentConfirmationError

        self.order.status = Order.STATUS_CANCELLED
        self.order.save(update_fields=["status"])

        self.confirm()

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.STATUS_CANCELLED)
