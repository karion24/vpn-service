from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from portal.models import Client, Plan, Subscription, Device
from portal.services import provision_device, DeviceProvisionError


class DeviceSubscriptionGuardTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="device-guard-test",
        )

        self.plan = Plan.objects.create(
            name="Device Guard Test",
            code="device-guard-test",
            price=100,
            duration_days=30,
            device_limit=2,
        )

        self.client = Client.objects.create(
            username="device-guard-client",
            owner=self.user,
            is_active=True,
            expires_at=timezone.now() + timedelta(days=30),
        )

        self.subscription = Subscription.objects.create(
            client=self.client,
            plan=self.plan,
            status=Subscription.STATUS_ACTIVE,
            expires_at=timezone.now() + timedelta(days=30),
        )

    def test_expired_subscription_blocks_device(self):
        self.subscription.expires_at = (
            timezone.now() - timedelta(days=1)
        )
        self.subscription.save(update_fields=["expires_at"])

        with self.assertRaises(DeviceProvisionError):
            provision_device(
                self.client,
                "Test device",
                Device.PLATFORM_WINDOWS,
            )

        self.assertFalse(
            Device.objects.filter(client=self.client).exists()
        )

    def test_inactive_subscription_blocks_device(self):
        self.subscription.status = Subscription.STATUS_EXPIRED
        self.subscription.save(update_fields=["status"])

        with self.assertRaises(DeviceProvisionError):
            provision_device(
                self.client,
                "Test device",
                Device.PLATFORM_WINDOWS,
            )

        self.assertFalse(
            Device.objects.filter(client=self.client).exists()
        )
