from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from portal.models import Client, Order, Plan, Subscription
from portal.sync_concurrency import subscription_state_is_current


class SubscriptionConcurrencyTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="concurrency-test-user",
            password="test-password",
        )

        self.plan = Plan.objects.create(
            name="Concurrency Test",
            code="concurrency-test",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        self.expires = timezone.now() + timedelta(days=30)

        self.client = Client.objects.create(
            username="concurrency-test-client",
            owner=self.user,
            expires_at=self.expires,
        )

        self.subscription = Subscription.objects.create(
            client=self.client,
            plan=self.plan,
            status=Subscription.STATUS_ACTIVE,
            expires_at=self.expires,
            expires_synced_value=self.expires,
        )

    def check_state(self):
        return subscription_state_is_current(
            self.subscription.pk,
            self.expires,
            self.expires,
        )

    def test_unchanged_subscription(self):
        self.assertTrue(self.check_state())

    def test_changed_expiration(self):
        Subscription.objects.filter(
            pk=self.subscription.pk
        ).update(
            expires_at=self.expires + timedelta(days=30)
        )

        self.assertFalse(self.check_state())

    def test_changed_sync_marker(self):
        Subscription.objects.filter(
            pk=self.subscription.pk
        ).update(
            expires_synced_value=self.expires + timedelta(days=1)
        )

        self.assertFalse(self.check_state())

    def test_paid_pending_fulfillment(self):
        Order.objects.create(
            user=self.user,
            plan=self.plan,
            client=self.client,
            order_type=Order.TYPE_RENEWAL,
            status=Order.STATUS_PAID,
            fulfillment_status=Order.FULFILLMENT_SYNC_PENDING,
            amount=Decimal("100.00"),
            duration_days=30,
            device_limit=2,
        )

        self.assertFalse(self.check_state())
