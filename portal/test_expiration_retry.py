from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from portal.models import Client, Plan, Subscription


class ExpirationRetryTests(TestCase):

    def test_already_expired_subscription_is_resynchronized(self):
        user = get_user_model().objects.create_user(
            username="expiration-retry-owner",
        )

        plan = Plan.objects.create(
            name="Expiration Retry",
            code="expiration-retry",
            price=Decimal("100.00"),
            duration_days=30,
            device_limit=1,
        )

        expired_at = timezone.now() - timedelta(minutes=5)

        client = Client.objects.create(
            username="expiration-retry-client",
            owner=user,
            is_active=False,
            expires_at=expired_at,
        )

        subscription = Subscription.objects.create(
            client=client,
            plan=plan,
            status=Subscription.STATUS_EXPIRED,
            expires_at=expired_at,
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

        subscription.refresh_from_db()
        client.refresh_from_db()

        self.assertEqual(
            subscription.status,
            Subscription.STATUS_EXPIRED,
        )
        self.assertFalse(client.is_active)

        sync_mock.assert_called_once()
