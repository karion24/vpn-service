from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from portal.models import Client, Order, Payment, Plan, Subscription
from portal.payment_confirmation import confirm_succeeded_payment


class PaymentToEntitlementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="payment-chain-test-user",
        )
        cls.plan = Plan.objects.create(
            name="Payment Chain Test",
            code="payment-chain-test",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

    def make_subscription(self, username, expires_at):
        vpn_client = Client.objects.create(
            username=username,
            owner=self.user,
            expires_at=expires_at,
            is_active=True,
        )
        subscription = Subscription.objects.create(
            client=vpn_client,
            plan=self.plan,
            status=Subscription.STATUS_ACTIVE,
            expires_at=expires_at,
        )
        return vpn_client, subscription

    def pay_renewal(self, vpn_client, key):
        order = Order.objects.create(
            user=self.user,
            plan=self.plan,
            client=vpn_client,
            order_type=Order.TYPE_RENEWAL,
            status=Order.STATUS_PENDING,
            fulfillment_status=Order.FULFILLMENT_PENDING,
            amount=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )
        payment = Payment.objects.create(
            order=order,
            amount=Decimal("100.00"),
            status=Payment.STATUS_PENDING,
            provider="karion-payments",
            idempotency_key=key,
        )
        confirm_succeeded_payment(
            payment_id=payment.pk,
            confirmed_amount=Decimal("100.00"),
        )
        return order

    def process_queue(self):
        call_command(
            "process_paid_orders",
            "--apply",
            stdout=StringIO(),
            stderr=StringIO(),
        )

    def test_payment_then_entitlement(self):
        initial = timezone.now() + timedelta(days=10)
        vpn_client, subscription = self.make_subscription(
            "payment-chain-one",
            initial,
        )
        order = self.pay_renewal(vpn_client, "chain-one")

        self.process_queue()
        self.process_queue()

        vpn_client.refresh_from_db()
        subscription.refresh_from_db()
        order.refresh_from_db()

        expected = initial + timedelta(days=30)

        self.assertEqual(vpn_client.expires_at, expected)
        self.assertEqual(subscription.expires_at, expected)
        self.assertEqual(order.status, Order.STATUS_PAID)
        self.assertEqual(
            order.fulfillment_status,
            Order.FULFILLMENT_SYNC_PENDING,
        )

    def test_two_renewals_add_sixty_days(self):
        initial = timezone.now() + timedelta(days=10)
        vpn_client, subscription = self.make_subscription(
            "payment-chain-two",
            initial,
        )
        first = self.pay_renewal(vpn_client, "chain-two-first")
        second = self.pay_renewal(vpn_client, "chain-two-second")

        self.process_queue()
        self.process_queue()

        vpn_client.refresh_from_db()
        subscription.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()

        expected = initial + timedelta(days=60)

        self.assertEqual(vpn_client.expires_at, expected)
        self.assertEqual(subscription.expires_at, expected)
        self.assertEqual(
            first.fulfillment_status,
            Order.FULFILLMENT_SYNC_PENDING,
        )
        self.assertEqual(
            second.fulfillment_status,
            Order.FULFILLMENT_SYNC_PENDING,
        )

    def test_other_subscription_is_not_extended(self):
        initial_a = timezone.now() + timedelta(days=10)
        initial_b = timezone.now() + timedelta(days=20)

        client_a, subscription_a = self.make_subscription(
            "payment-chain-client-a",
            initial_a,
        )
        client_b, subscription_b = self.make_subscription(
            "payment-chain-client-b",
            initial_b,
        )

        self.pay_renewal(client_a, "chain-independent")

        self.process_queue()

        client_a.refresh_from_db()
        subscription_a.refresh_from_db()
        client_b.refresh_from_db()
        subscription_b.refresh_from_db()

        self.assertEqual(
            client_a.expires_at,
            initial_a + timedelta(days=30),
        )
        self.assertEqual(
            subscription_a.expires_at,
            initial_a + timedelta(days=30),
        )
        self.assertEqual(client_b.expires_at, initial_b)
        self.assertEqual(subscription_b.expires_at, initial_b)
