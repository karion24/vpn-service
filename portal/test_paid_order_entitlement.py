from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from portal.models import Client, Order, Plan, Subscription


class PaidOrderEntitlementTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="entitlement-test-user",
        )

        self.plan = Plan.objects.create(
            name="Entitlement Test",
            code="entitlement-test",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        self.old_expire = (
            timezone.now() + timedelta(days=10)
        )

        self.client = Client.objects.create(
            username="entitlement-test-client",
            owner=self.user,
            is_active=True,
            expires_at=self.old_expire,
        )

        self.subscription = Subscription.objects.create(
            client=self.client,
            plan=self.plan,
            status=Subscription.STATUS_ACTIVE,
            expires_at=self.old_expire,
            expires_synced_value=self.old_expire,
        )

        self.order = Order.objects.create(
            user=self.user,
            plan=self.plan,
            client=self.client,
            order_type=Order.TYPE_RENEWAL,
            status=Order.STATUS_PAID,
            fulfillment_status=(
                Order.FULFILLMENT_ACTIVATION_PENDING
            ),
            amount=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

    def test_paid_renewal_adds_days(self):
        from portal.paid_order_execution import (
            apply_paid_order_entitlement,
        )

        apply_paid_order_entitlement(self.order.pk)

        self.client.refresh_from_db()
        self.subscription.refresh_from_db()
        self.order.refresh_from_db()

        expected = (
            self.old_expire + timedelta(days=30)
        )

        self.assertEqual(
            self.client.expires_at,
            expected,
        )
        self.assertEqual(
            self.subscription.expires_at,
            expected,
        )
        self.assertEqual(
            self.order.fulfillment_status,
            Order.FULFILLMENT_SYNC_PENDING,
        )

    def test_repeat_does_not_add_days_twice(self):
        from portal.paid_order_execution import (
            apply_paid_order_entitlement,
        )

        apply_paid_order_entitlement(self.order.pk)
        apply_paid_order_entitlement(self.order.pk)

        self.client.refresh_from_db()

        self.assertEqual(
            self.client.expires_at,
            self.old_expire + timedelta(days=30),
        )
