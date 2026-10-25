"""HTTP-клиент единого платёжного шлюза Karion."""

import os
from decimal import Decimal
from urllib.parse import urlparse

import requests


class PaymentGatewayError(Exception):
    pass


def build_invoice_payload(order):
    """Формирует неизменяемые данные счёта по VPN-заказу."""

    email = (order.user.email or "").strip()

    if not email:
        raise PaymentGatewayError(
            "Для выставления счёта необходим email покупателя."
        )

    if order.amount is None or order.amount <= 0:
        raise PaymentGatewayError(
            "Некорректная сумма заказа."
        )

    # Единое коммерческое наименование, независимо от технического названия тарифа.
    # Срок берётся из конкретного заказа, включая продления.
    name = f"Доступ к IT-сервису Karion — {order.duration_days} дней"

    return {
        "external_id": f"vpn-order-{order.pk}",
        "customer": {
            "name": order.user.get_full_name()[:255],
            "email": email,
            "phone": "",
        },
        "description": name,
        "items": [
            {
                "name": name[:255],
                "quantity": "1",
                "price": str(
                    Decimal(order.amount).quantize(
                        Decimal("0.01")
                    )
                ),
            }
        ],
    }


class PaymentGatewayClient:
    def __init__(self):
        self.enabled = (
            os.environ.get("KARION_PAYMENTS_ENABLED") == "1"
        )
        self.base_url = os.environ.get(
            "KARION_PAYMENTS_URL",
            "https://pay.karion24.ru",
        ).rstrip("/")
        self.api_key = os.environ.get(
            "KARION_PAYMENTS_API_KEY", ""
        )

    def _check_configuration(self):
        if not self.enabled:
            raise PaymentGatewayError(
                "Платёжная интеграция отключена."
            )

        parsed = urlparse(self.base_url)

        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise PaymentGatewayError(
                "Некорректный HTTPS-адрес шлюза."
            )

        if not self.api_key:
            raise PaymentGatewayError(
                "Не настроен API-ключ платёжного шлюза."
            )

    def create_invoice(self, order):
        self._check_configuration()

        payload = build_invoice_payload(order)

        try:
            response = requests.post(
                self.base_url + "/api/v1/invoices/",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Accept": "application/json",
                },
                timeout=(5, 15),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise PaymentGatewayError(
                "Не удалось связаться с платёжным шлюзом."
            ) from exc

        if response.status_code not in (200, 201):
            raise PaymentGatewayError(
                f"Шлюз вернул HTTP {response.status_code}."
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise PaymentGatewayError(
                "Шлюз вернул некорректный JSON."
            ) from exc

        if not isinstance(data, dict) or data.get("success") is not True:
            raise PaymentGatewayError(
                "Шлюз не подтвердил создание счёта."
            )

        return data
