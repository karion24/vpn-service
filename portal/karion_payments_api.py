"""Клиент централизованного платёжного шлюза Karion."""

import json
import os
import urllib.error
import urllib.request
from decimal import Decimal
from urllib.parse import urlparse


class KarionPaymentsAPIError(Exception):
    pass


def _config():
    base_url = os.environ.get(
        "KARION_PAYMENTS_API_URL", ""
    ).rstrip("/")

    api_key = os.environ.get(
        "KARION_PAYMENTS_API_KEY", ""
    )

    parsed = urlparse(base_url)

    if (
        parsed.scheme != "https"
        or parsed.hostname != "pay.karion24.ru"
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise KarionPaymentsAPIError(
            "Некорректный адрес платёжного шлюза."
        )

    if not api_key.startswith("kpay_"):
        raise KarionPaymentsAPIError(
            "API-ключ платёжного шлюза не настроен."
        )

    return base_url, api_key


def _request(method, path, payload=None):
    base_url, api_key = _config()

    body = (
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if payload is not None
        else None
    )

    request = urllib.request.Request(
        base_url + path,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise KarionPaymentsAPIError(
            f"Платёжный шлюз вернул HTTP {exc.code}."
        ) from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise KarionPaymentsAPIError(
            "Не удалось получить корректный ответ платёжного шлюза."
        ) from exc

    if not isinstance(result, dict):
        raise KarionPaymentsAPIError(
            "Некорректный формат ответа платёжного шлюза."
        )

    return result


def create_invoice(*, order, customer_email, customer_name=""):
    """Создаёт или возвращает существующий счёт заказа."""

    amount = Decimal(str(order.amount))

    if not amount.is_finite() or amount <= 0:
        raise KarionPaymentsAPIError(
            "Некорректная сумма заказа."
        )

    payload = {
        "external_id": f"karion-vpn-order-{order.pk}",
        "customer": {
            "name": customer_name[:255],
            "email": customer_email,
        },
        "description": f"Доступ к IT-сервису Karion — {order.duration_days} дней",
        "items": [
            {
                "name": f"Доступ к IT-сервису Karion — {order.duration_days} дней",
                "quantity": "1",
                "price": str(amount),
            }
        ],
    }

    result = _request("POST", "/api/v1/invoices/", payload)

    if result.get("success") is not True:
        raise KarionPaymentsAPIError(
            "Платёжный шлюз не подтвердил создание счёта."
        )

    invoice = result.get("invoice")

    if not isinstance(invoice, dict):
        raise KarionPaymentsAPIError(
            "В ответе отсутствуют данные счёта."
        )

    if (
        invoice.get("external_id") != payload["external_id"]
        or Decimal(str(invoice.get("total", "0"))) != amount
    ):
        raise KarionPaymentsAPIError(
            "Данные созданного счёта не совпадают с заказом."
        )

    return invoice


def get_invoice(invoice_id):
    """Получает актуальный статус счёта через защищённый API."""

    return _request(
        "GET",
        f"/api/v1/invoices/{invoice_id}/",
    )



def reconcile_invoice_payment(invoice_id):
    """Read-only YooKassa status check via authenticated Payments API.

    The Payments service alone verifies and commits provider status.
    """
    return _request(
        "POST",
        f"/api/v1/invoices/{invoice_id}/reconcile-payment/",
    )


def start_invoice_payment(invoice_id):
    """Запускает оплату счёта и возвращает ссылку ЮKassa."""

    result = _request(
        "POST",
        f"/api/v1/invoices/{invoice_id}/start-payment/",
    )

    if result.get("success") is not True:
        raise KarionPaymentsAPIError(
            "Платёжный шлюз не подтвердил запуск оплаты."
        )

    confirmation_url = result.get("confirmation_url")

    if not isinstance(confirmation_url, str) or not confirmation_url:
        raise KarionPaymentsAPIError(
            "Платёжный шлюз не вернул ссылку на оплату."
        )

    return confirmation_url


def cancel_invoice(invoice_id):
    """Отменяет неоплаченный счёт через Karion Payments."""

    result = _request(
        "POST",
        f"/api/v1/invoices/{invoice_id}/cancel/",
    )

    if (
        result.get("success") is not True
        or result.get("status") != "cancelled"
        or str(result.get("invoice_id")) != str(invoice_id)
    ):
        raise KarionPaymentsAPIError(
            "Платёжный шлюз не подтвердил отмену счёта."
        )

    return result
