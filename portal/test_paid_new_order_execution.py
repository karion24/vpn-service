from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from portal.models import Client, Order, Plan, Subscription
from portal.paid_new_order_execution import activate_paid_new_order


class PaidNewOrderExecutionTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="paid_new_order_test",
            password="test-password",
        )

        self.plan = Plan.objects.create(
            name="Тестовая VPN-подписка",
            price=Decimal("300.00"),
            duration_days=30,
            device_limit=2,
        )

        self.order = Order.objects.create(
            user=self.user,
            plan=self.plan,
            order_type=Order.TYPE_NEW,
            status=Order.STATUS_PAID,
            paid_at=timezone.now(),
            fulfillment_status=Order.FULFILLMENT_ACTIVATION_PENDING,
            amount=Decimal("300.00"),
            duration_days=30,
            device_limit=2,
        )

    def test_activation_and_idempotency(self):
        before = timezone.now()

        first = activate_paid_new_order(self.order.pk)

        self.assertEqual(
            first.fulfillment_status,
            Order.FULFILLMENT_COMPLETED,
        )
        self.assertIsNotNone(first.client_id)

        client = Client.objects.get(pk=first.client_id)
        subscription = Subscription.objects.get(client=client)

        self.assertEqual(
            client.username,
            f"karion-{self.user.pk}-{self.order.pk}",
        )
        self.assertEqual(client.owner_id, self.user.pk)
        self.assertTrue(client.is_active)

        self.assertEqual(
            subscription.status,
            Subscription.STATUS_ACTIVE,
        )
        self.assertEqual(subscription.plan_id, self.plan.pk)
        self.assertEqual(subscription.price, self.order.amount)

        expected_min = before + timedelta(days=30)
        expected_max = timezone.now() + timedelta(days=30)

        self.assertGreaterEqual(client.expires_at, expected_min)
        self.assertLessEqual(client.expires_at, expected_max)

        second = activate_paid_new_order(self.order.pk)

        self.assertEqual(second.client_id, first.client_id)
        self.assertEqual(
            Client.objects.filter(
                username=client.username
            ).count(),
            1,
        )
        self.assertEqual(
            Subscription.objects.filter(client=client).count(),
            1,
        )

    def test_unpaid_order_cannot_be_activated(self):
        self.order.status = Order.STATUS_PENDING
        self.order.save(update_fields=["status"])

        from portal.paid_new_order_execution import (
            PaidNewOrderActivationError,
        )

        with self.assertRaises(PaidNewOrderActivationError):
            activate_paid_new_order(self.order.pk)

        self.assertFalse(
            Client.objects.filter(owner=self.user).exists()
        )
