from decimal import Decimal
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase

from .payment_gateway import (
    PaymentGatewayClient,
    PaymentGatewayError,
    build_invoice_payload,
)


class PaymentGatewayClientTests(SimpleTestCase):

    def make_client(self, enabled=True):
        client = PaymentGatewayClient()
        client.enabled = enabled
        client.base_url = "https://pay.karion24.ru"
        client.api_key = "kpay_test_only"
        return client

    def make_order(self):
        user = Mock()
        user.email = "test@example.com"
        user.get_full_name.return_value = "Тестовый клиент"

        order = Mock()
        order.pk = 123
        order.user = user
        order.plan.name = "VPN 30 дней"
        order.amount = Decimal("450.00")
        order.duration_days = 30
        order.TYPE_NEW = "new"
        order.TYPE_RENEWAL = "renewal"
        order.order_type = "new"
        return order

    @patch("portal.payment_gateway.requests.post")
    def test_disabled_integration(self, post):
        client = self.make_client(enabled=False)

        with self.assertRaises(PaymentGatewayError):
            client.create_invoice(self.make_order())

        post.assert_not_called()

    def test_invoice_payload(self):
        payload = build_invoice_payload(self.make_order())

        self.assertEqual(
            payload["external_id"], "vpn-order-123"
        )
        self.assertEqual(
            payload["description"], "Доступ к IT-сервису Karion — 30 дней"
        )
        self.assertEqual(
            payload["items"][0]["name"], "Доступ к IT-сервису Karion — 30 дней"
        )
        self.assertEqual(
            payload["items"][0]["price"], "450.00"
        )
        self.assertEqual(
            payload["customer"]["email"], "test@example.com"
        )

    @patch("portal.payment_gateway.requests.post")
    def test_create_invoice_success(self, post):
        post.return_value.status_code = 201
        post.return_value.json.return_value = {
            "success": True,
            "created": True,
            "invoice": {"id": "test-uuid"},
        }

        result = self.make_client().create_invoice(
            self.make_order()
        )

        self.assertTrue(result["success"])
        self.assertTrue(result["created"])

        kwargs = post.call_args.kwargs
        self.assertEqual(
            kwargs["headers"]["Authorization"],
            "Bearer kpay_test_only",
        )
        self.assertFalse(kwargs["allow_redirects"])

    @patch("portal.payment_gateway.requests.post")
    def test_existing_invoice(self, post):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {
            "success": True,
            "created": False,
            "invoice": {"id": "existing-uuid"},
        }

        result = self.make_client().create_invoice(
            self.make_order()
        )

        self.assertFalse(result["created"])

    @patch("portal.payment_gateway.requests.post")
    def test_gateway_http_error(self, post):
        post.return_value.status_code = 409

        with self.assertRaises(PaymentGatewayError):
            self.make_client().create_invoice(
                self.make_order()
            )

    @patch("portal.payment_gateway.requests.post")
    def test_gateway_timeout(self, post):
        post.side_effect = requests.Timeout()

        with self.assertRaises(PaymentGatewayError):
            self.make_client().create_invoice(
                self.make_order()
            )
