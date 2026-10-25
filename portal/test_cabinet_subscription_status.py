from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from portal.models import Client, Plan, Subscription


class CabinetSubscriptionStatusTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="cabinet_status_test",
            password="test-password-123",
        )
        self.client.force_login(self.user)

        self.plan = Plan.objects.create(
            name="Тестовый тариф",
            device_limit=2,
        )

    def create_subscription(self, expires_at):
        vpn_client = Client.objects.create(
            username=f"cabinet-test-{Client.objects.count() + 1}",
            name="Тестовая VPN-подписка",
            owner=self.user,
            is_active=True,
            expires_at=expires_at,
        )

        Subscription.objects.create(
            client=vpn_client,
            plan=self.plan,
            status=Subscription.STATUS_ACTIVE,
            expires_at=expires_at,
        )

        return vpn_client

    def test_active_subscription_shows_add_device(self):
        self.create_subscription(
            timezone.now() + timedelta(days=30)
        )

        response = self.client.get(reverse("cabinet"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Тестовая VPN-подписка")
        self.assertContains(response, "+ Добавить устройство")

    def test_expired_subscription_hides_add_device(self):
        self.create_subscription(
            timezone.now() - timedelta(days=1)
        )

        response = self.client.get(reverse("cabinet"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Тестовая VPN-подписка")
        self.assertContains(response, "Приостановлена")
        self.assertNotContains(response, "+ Добавить устройство")
