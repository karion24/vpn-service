from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from portal.models import Client, Order, Payment, Plan, Subscription
from portal.payment_confirmation import confirm_succeeded_payment


class PaymentNewSubscriptionChainTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="payment-new-chain-test",
        )

        self.plan = Plan.objects.create(
            name="Тестовая подписка",
            code="payment-new-chain-test",
            price=Decimal("300.00"),
            duration_days=30,
            device_limit=2,
        )

        self.order = Order.objects.create(
            user=self.user,
            plan=self.plan,
            order_type=Order.TYPE_NEW,
            status=Order.STATUS_PENDING,
            fulfillment_status=Order.FULFILLMENT_PENDING,
            amount=Decimal("300.00"),
            duration_days=30,
            device_limit=2,
            payment_expires_at=timezone.now(),
        )

        self.payment = Payment.objects.create(
            order=self.order,
            amount=Decimal("300.00"),
            status=Payment.STATUS_PENDING,
            provider="karion_payments",
            idempotency_key=f"new-chain-{self.order.pk}",
        )

    def test_payment_confirmation_to_active_subscription(self):
        self.assertFalse(
            Client.objects.filter(owner=self.user).exists()
        )

        confirm_succeeded_payment(
            payment_id=self.payment.pk,
            confirmed_amount=Decimal("300.00"),
        )

        self.order.refresh_from_db()
        self.payment.refresh_from_db()

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

        output = StringIO()

        call_command(
            "fulfill_karion_vpn_orders",
            apply=True,
            stdout=output,
        )

        self.order.refresh_from_db()

        self.assertEqual(
            self.order.fulfillment_status,
            Order.FULFILLMENT_COMPLETED,
        )
        self.assertIsNotNone(self.order.client_id)

        client = Client.objects.get(pk=self.order.client_id)
        subscription = Subscription.objects.get(client=client)

        self.assertEqual(client.owner_id, self.user.pk)
        self.assertEqual(subscription.plan_id, self.plan.pk)
        self.assertEqual(
            subscription.status,
            Subscription.STATUS_ACTIVE,
        )
        self.assertEqual(subscription.price, Decimal("300.00"))

        original_expiry = client.expires_at

        confirm_succeeded_payment(
            payment_id=self.payment.pk,
            confirmed_amount=Decimal("300.00"),
        )

        call_command(
            "fulfill_karion_vpn_orders",
            apply=True,
            stdout=StringIO(),
        )

        self.order.refresh_from_db()
        client.refresh_from_db()

        self.assertEqual(
            self.order.fulfillment_status,
            Order.FULFILLMENT_COMPLETED,
        )
        self.assertEqual(client.expires_at, original_expiry)
        self.assertEqual(
            Client.objects.filter(owner=self.user).count(),
            1,
        )
        self.assertEqual(
            Subscription.objects.filter(client=client).count(),
            1,
        )
        self.assertIn("COMPLETED", output.getvalue())
