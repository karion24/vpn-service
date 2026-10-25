from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from portal.models import Client, Order, Plan, Subscription


class FulfillmentCommandTests(TestCase):

    def test_paid_new_order_is_activated_once(self):
        user = get_user_model().objects.create_user(
            username="fulfillment_command_test",
            password="test-password",
        )

        plan = Plan.objects.create(
            name="Тестовая VPN-подписка",
            price=Decimal("300.00"),
            duration_days=30,
            device_limit=2,
        )

        order = Order.objects.create(
            user=user,
            plan=plan,
            order_type=Order.TYPE_NEW,
            status=Order.STATUS_PAID,
            paid_at=timezone.now(),
            fulfillment_status=Order.FULFILLMENT_ACTIVATION_PENDING,
            amount=Decimal("300.00"),
            duration_days=30,
            device_limit=2,
        )

        output = StringIO()

        call_command(
            "fulfill_karion_vpn_orders",
            apply=True,
            stdout=output,
        )

        order.refresh_from_db()

        self.assertEqual(
            order.fulfillment_status,
            Order.FULFILLMENT_COMPLETED,
        )
        self.assertIsNotNone(order.client_id)

        self.assertTrue(
            Subscription.objects.filter(
                client_id=order.client_id,
                status=Subscription.STATUS_ACTIVE,
            ).exists()
        )

        self.assertEqual(
            Client.objects.filter(owner=user).count(),
            1,
        )

        call_command(
            "fulfill_karion_vpn_orders",
            apply=True,
            stdout=StringIO(),
        )

        self.assertEqual(
            Client.objects.filter(owner=user).count(),
            1,
        )
        self.assertEqual(
            Subscription.objects.filter(
                client_id=order.client_id,
            ).count(),
            1,
        )

        self.assertIn("COMPLETED", output.getvalue())
