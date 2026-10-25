from decimal import Decimal
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from portal.models import (
    Client, Device, Order, Payment, Plan, Subscription,
)
from portal.payment_confirmation import confirm_succeeded_payment
from portal.services import provision_device


class PaymentFirstDeviceChainTests(TestCase):

    def test_payment_to_first_remnawave_device(self):
        user = get_user_model().objects.create_user(
            username="payment-first-device-test",
        )

        plan = Plan.objects.create(
            name="Тест первого устройства",
            code="payment-first-device-test",
            price=Decimal("300.00"),
            duration_days=30,
            device_limit=2,
        )

        order = Order.objects.create(
            user=user,
            plan=plan,
            order_type=Order.TYPE_NEW,
            status=Order.STATUS_PENDING,
            fulfillment_status=Order.FULFILLMENT_PENDING,
            amount=Decimal("300.00"),
            duration_days=30,
            device_limit=2,
            payment_expires_at=timezone.now(),
        )

        payment = Payment.objects.create(
            order=order,
            amount=Decimal("300.00"),
            status=Payment.STATUS_PENDING,
            provider="karion_payments",
            idempotency_key=f"first-device-{order.pk}",
        )

        confirm_succeeded_payment(
            payment_id=payment.pk,
            confirmed_amount=Decimal("300.00"),
        )

        call_command(
            "fulfill_karion_vpn_orders",
            apply=True,
            stdout=StringIO(),
        )

        order.refresh_from_db()

        self.assertEqual(order.status, Order.STATUS_PAID)
        self.assertEqual(
            order.fulfillment_status,
            Order.FULFILLMENT_COMPLETED,
        )

        client = Client.objects.get(pk=order.client_id)
        subscription = Subscription.objects.get(client=client)

        self.assertEqual(
            subscription.status,
            Subscription.STATUS_ACTIVE,
        )

        remote_id = 123456789
        remote_uuid = str(uuid4())
        remote_short_uuid = str(uuid4())

        with patch(
            "portal.services._get_remnawave_client"
        ) as get_remnawave:
            remnawave = get_remnawave.return_value
            remnawave.create_user.return_value = {
                "id": remote_id,
                "vlessUuid": remote_uuid,
                "shortUuid": remote_short_uuid,
            }

            device = provision_device(
                client=client,
                name="Первое устройство",
                platform=Device.PLATFORM_WINDOWS,
            )

            remnawave.create_user.assert_called_once_with(
                username=(
                    f"karion-u{user.pk}"
                    f"-c{client.pk}"
                    f"-d{device.pk}"
                ),
                expire_at=subscription.expires_at,
            )

        device.refresh_from_db()

        self.assertEqual(device.client_id, client.pk)
        self.assertTrue(device.is_active)
        self.assertEqual(device.remnawave_user_id, remote_id)
        self.assertEqual(device.remnawave_uuid, remote_uuid)
        self.assertEqual(
            device.remnawave_short_uuid,
            remote_short_uuid,
        )

        self.assertEqual(
            Device.objects.filter(
                client=client,
                is_active=True,
            ).count(),
            1,
        )
