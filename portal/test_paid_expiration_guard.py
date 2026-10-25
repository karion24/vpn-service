from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from portal.models import (
    Client,
    Order,
    Plan,
    Subscription,
)


class PaidExpirationGuardTests(TestCase):

    def test_pending_paid_renewal_prevents_expiration(self):
        user = get_user_model().objects.create_user(
            username="paid-expiration-owner",
        )

        plan = Plan.objects.create(
            name="Paid Expiration Guard",
            code="paid-expiration-guard",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=1,
        )

        expired_at = timezone.now() - timedelta(minutes=5)

        client = Client.objects.create(
            username="paid-expiration-client",
            owner=user,
            is_active=True,
            expires_at=expired_at,
        )

        subscription = Subscription.objects.create(
            client=client,
            plan=plan,
            status=Subscription.STATUS_ACTIVE,
            expires_at=expired_at,
        )

        Order.objects.create(
            user=user,
            client=client,
            plan=plan,
            order_type=Order.TYPE_RENEWAL,
            status=Order.STATUS_PAID,
            fulfillment_status=Order.FULFILLMENT_ACTIVATION_PENDING,
            amount=Decimal("100.00"),
            duration_days=30,
            device_limit=1,
        )

        with patch(
            "portal.management.commands."
            "expire_subscriptions."
            "sync_expired_subscription"
        ) as sync_mock:
            call_command(
                "expire_subscriptions",
                verbosity=0,
            )

        client.refresh_from_db()
        subscription.refresh_from_db()

        self.assertEqual(
            subscription.status,
            Subscription.STATUS_ACTIVE,
        )
        self.assertTrue(client.is_active)
        sync_mock.assert_not_called()
