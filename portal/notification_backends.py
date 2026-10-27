"""Text delivery using the contracts in dash.zip; no ticket/dashboard dependencies."""
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.utils.module_loading import import_string


class NotificationDeliveryError(RuntimeError):
    """Safe error: never include tokens, request URLs or provider response bodies."""


def recipient_id(value):
    value = str(value).strip()
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        raise ValueError("Для личных уведомлений нужен положительный числовой ID, а не @username.")
    return int(value)


class NotificationDeliveryUnknown(NotificationDeliveryError):
    """A request may have been accepted; never retry it automatically."""


def _post(url, **kwargs):
    try:
        response = requests.post(url, timeout=25, allow_redirects=False, **kwargs)
    except requests.ConnectTimeout:
        raise NotificationDeliveryError("Соединение не установлено.") from None
    except requests.RequestException:
        raise NotificationDeliveryUnknown("Результат отправки неизвестен.") from None
    if not 200 <= response.status_code < 300:
        error = NotificationDeliveryError if 400 <= response.status_code < 500 and response.status_code != 408 else NotificationDeliveryUnknown
        raise error("Шлюз не подтвердил отправку сообщения.")
    return response


def _json(response):
    try:
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except ValueError:
        raise NotificationDeliveryUnknown("Некорректный ответ после отправки.") from None


def telegram_gateway_dash_response(response):
    """Client expectation in dash.zip, NOT a verified server contract.

    Enable explicitly only after confirming this contract on the gateway server.
    """
    data = _json(response)
    if response.status_code == 200 and data.get("ok") is True:
        return True
    if data.get("ok") is False:
        raise NotificationDeliveryError("Telegram-шлюз отклонил сообщение.")
    raise NotificationDeliveryUnknown("Ответ Telegram-шлюза требует проверки.")


def send_telegram(*, recipient, message, idempotency_key):
    chat_id = recipient_id(recipient)
    base = settings.TELEGRAM_BOT_SERVER_URL.strip().rstrip("/")
    if base:
        parsed = urlsplit(base)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise NotificationDeliveryError("Нужен HTTPS-адрес Telegram-шлюза.")
        if not settings.TELEGRAM_BOT_SERVER_TOKEN:
            raise NotificationDeliveryError("Не настроен токен Telegram-шлюза.")
        validator_path = settings.TELEGRAM_BOT_SERVER_RESPONSE_VALIDATOR
        if not validator_path:
            raise NotificationDeliveryError("Не подтверждён контракт ответа Telegram-шлюза.")
        try:
            validator = import_string(validator_path)
        except (ImportError, AttributeError):
            raise NotificationDeliveryError("Не настроена проверка ответа Telegram-шлюза.") from None
        response = _post(
            base + "/send-message",
            headers={"X-KARION-TOKEN": settings.TELEGRAM_BOT_SERVER_TOKEN},
            json={"chat_id": chat_id, "text": message},
        )
        if validator(response) is not True:
            raise NotificationDeliveryUnknown("Ответ Telegram-шлюза не подтверждён.")
    else:
        token = settings.TELEGRAM_BOT_TOKEN.strip()
        if not token:
            raise NotificationDeliveryError("Не настроен Telegram bot token или шлюз.")
        data = _json(_post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
        ))
        if data.get("ok") is False:
            raise NotificationDeliveryError("Telegram отклонил сообщение.")
        if data.get("ok") is not True:
            raise NotificationDeliveryUnknown("Ответ Telegram не подтверждён.")
    # dash.zip's contract has no idempotency parameter. Do not invent one.


def send_max(*, recipient, message, idempotency_key):
    user_id = recipient_id(recipient)
    token = settings.MAX_BOT_TOKEN.strip()
    if not token:
        raise NotificationDeliveryError("Не настроен MAX bot token.")
    _post(
        "https://platform-api.max.ru/messages",
        params={"user_id": user_id},
        headers={"Authorization": token},
        json={"text": message},
    )
    # dash.zip treats HTTP 2xx as accepted even if JSON/mid is absent.
    # MAX_BOT_TOKEN is a bot token, never a personal-account session.
