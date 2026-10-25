from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from portal.models import Client as VPNClient, Order, Payment, Plan
from portal.payment_fulfillment import has_pending_paid_fulfillment


class PaidFulfillmentDatabaseTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="payment_fulfillment_test",
            password="test-password",
        )

        cls.plan = Plan.objects.create(
            name="Тестовый VPN",
            code="payment-fulfillment-test",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        cls.vpn_client = VPNClient.objects.create(
            username="fulfillment-client-1",
            owner=cls.user,
        )

        cls.other_client = VPNClient.objects.create(
            username="fulfillment-client-2",
            owner=cls.user,
        )

    def make_order(
        self,
        *,
        client=None,
        status=Order.STATUS_PENDING,
        fulfillment=Order.FULFILLMENT_PENDING,
    ):
        return Order.objects.create(
            user=self.user,
            plan=self.plan,
            client=client or self.vpn_client,
            order_type=Order.TYPE_RENEWAL,
            status=status,
            fulfillment_status=fulfillment,
            amount=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

    def test_unpaid_order_is_not_protected(self):
        self.make_order(
            fulfillment=Order.FULFILLMENT_ACTIVATION_PENDING,
        )
        self.assertFalse(
            has_pending_paid_fulfillment(self.vpn_client.pk)
        )

    def test_paid_order_waiting_activation_is_protected(self):
        self.make_order(
            status=Order.STATUS_PAID,
            fulfillment=Order.FULFILLMENT_ACTIVATION_PENDING,
        )
        self.assertTrue(
            has_pending_paid_fulfillment(self.vpn_client.pk)
        )

    def test_paid_order_waiting_sync_is_protected(self):
        self.make_order(
            status=Order.STATUS_PAID,
            fulfillment=Order.FULFILLMENT_SYNC_PENDING,
        )
        self.assertTrue(
            has_pending_paid_fulfillment(self.vpn_client.pk)
        )

    def test_succeeded_payment_protects_pending_order(self):
        order = self.make_order(
            fulfillment=Order.FULFILLMENT_ACTIVATION_PENDING,
        )

        Payment.objects.create(
            order=order,
            amount=Decimal("100.00"),
            status=Payment.STATUS_SUCCEEDED,
            idempotency_key="fulfillment-db-test-payment",
        )

        self.assertTrue(
            has_pending_paid_fulfillment(self.vpn_client.pk)
        )

    def test_paid_order_with_fulfillment_error_is_protected(self):
        self.make_order(
            status=Order.STATUS_PAID,
            fulfillment=Order.FULFILLMENT_ERROR,
        )
        self.assertTrue(
            has_pending_paid_fulfillment(self.vpn_client.pk)
        )

    def test_completed_order_is_not_protected(self):
        self.make_order(
            status=Order.STATUS_PAID,
            fulfillment=Order.FULFILLMENT_COMPLETED,
        )
        self.assertFalse(
            has_pending_paid_fulfillment(self.vpn_client.pk)
        )

    def test_other_client_is_not_protected(self):
        self.make_order(
            status=Order.STATUS_PAID,
            fulfillment=Order.FULFILLMENT_SYNC_PENDING,
        )
        self.assertFalse(
            has_pending_paid_fulfillment(self.other_client.pk)
        )
