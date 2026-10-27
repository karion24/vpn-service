"""Text delivery using the contracts in dash.zip; no ticket/dashboard dependencies."""
from urllib.parse import urlsplit

import requests
from django.conf import settings


class NotificationDeliveryError(RuntimeError):
    """Safe error: never include tokens, request URLs or provider response bodies."""


def recipient_id(value):
    value = str(value).strip()
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        raise ValueError("Для личных уведомлений нужен положительный числовой ID, а не @username.")
    return int(value)


def _post(url, **kwargs):
    try:
        response = requests.post(url, timeout=25, allow_redirects=False, **kwargs)
        if not 200 <= response.status_code < 300:
            raise NotificationDeliveryError("Шлюз не подтвердил отправку сообщения.")
        data = response.json()
        if not isinstance(data, dict):
            raise NotificationDeliveryError("Некорректный ответ шлюза.")
        return data
    except (requests.RequestException, ValueError):
        raise NotificationDeliveryError("Не удалось подтвердить отправку сообщения.") from None


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
        data = _post(
            base + "/send-message",
            headers={"X-KARION-TOKEN": settings.TELEGRAM_BOT_SERVER_TOKEN},
            json={"chat_id": chat_id, "text": message},
        )
        if data.get("ok") is not True:
            raise NotificationDeliveryError("Telegram-шлюз отклонил сообщение.")
    else:
        token = settings.TELEGRAM_BOT_TOKEN.strip()
        if not token:
            raise NotificationDeliveryError("Не настроен Telegram bot token или шлюз.")
        data = _post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
        )
        if data.get("ok") is not True:
            raise NotificationDeliveryError("Telegram отклонил сообщение.")
    # dash.zip's contract has no idempotency parameter. Do not invent one.


def send_max(*, recipient, message, idempotency_key):
    user_id = recipient_id(recipient)
    token = settings.MAX_BOT_TOKEN.strip()
    if not token:
        raise NotificationDeliveryError("Не настроен MAX bot token.")
    data = _post(
        "https://platform-api.max.ru/messages",
        params={"user_id": user_id},
        headers={"Authorization": token},
        json={"text": message},
    )
    # An accepted MAX response contains a message ID (see _extract_mid in dash.zip).
    nested = data.get("message")
    body = data.get("body")
    candidates = [data.get("mid")]
    if isinstance(body, dict):
        candidates.append(body.get("mid"))
    if isinstance(nested, dict):
        candidates.append(nested.get("mid"))
        if isinstance(nested.get("body"), dict):
            candidates.append(nested["body"].get("mid"))
    if not any(value is not None and str(value).strip() for value in candidates):
        raise NotificationDeliveryError("MAX не вернул подтверждение сообщения.")
