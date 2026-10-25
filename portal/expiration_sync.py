"""
Безопасная синхронизация истечения VPN-подписки.

Не изменяет сроки подписок и не управляет заказами.
HTTP-запросы выполняются без собственной транзакции БД.
"""

from django.utils import timezone

from portal.models import Client, Device, Subscription
from portal.services import (
    ClientSyncError,
    _get_remnawave_client,
)


class ExpirationSyncChanged(Exception):
    """Состояние подписки изменилось во время отключения."""


def _subscription_is_expired(client_id):
    client = Client.objects.get(pk=client_id)
    subscription = Subscription.objects.get(client_id=client_id)

    return (
        not client.is_active
        and subscription.status == Subscription.STATUS_EXPIRED
        and subscription.expires_at is not None
        and subscription.expires_at <= timezone.now()
    )


def _active_devices(client_id):
    return list(
        Device.objects.filter(
            client_id=client_id,
            is_active=True,
            unlinked_at__isnull=True,
            remnawave_user_id__isnull=False,
        ).exclude(
            remnawave_username__isnull=True,
        ).order_by("pk")
    )


def restore_current_subscription(client_id, rw=None):
    """
    Восстанавливает актуальный срок на всех привязанных
    устройствах действующей подписки.

    При изменении срока во время HTTP-запросов повторяет
    синхронизацию, максимум пять раз.
    """
    if rw is None:
        client = Client.objects.get(pk=client_id)
        rw = _get_remnawave_client(client)

    for attempt in range(5):
        client = Client.objects.get(pk=client_id)
        subscription = Subscription.objects.get(client_id=client_id)

        expires_at = subscription.expires_at

        active = (
            client.is_active
            and subscription.status == Subscription.STATUS_ACTIVE
            and expires_at is not None
            and expires_at > timezone.now()
        )

        if not active:
            raise ExpirationSyncChanged(
                "Подписка неактивна: автоматическое "
                "включение устройств запрещено."
            )

        for device in _active_devices(client_id):
            # Повторная проверка исключает устройство,
            # которое пользователь успел отвязать.
            if not Device.objects.filter(
                pk=device.pk,
                client_id=client_id,
                is_active=True,
                unlinked_at__isnull=True,
            ).exists():
                continue

            remote_user = rw.get_user_by_username(
                device.remnawave_username
            )

            if not remote_user or remote_user.get("id") is None:
                raise ClientSyncError(
                    f"Устройство {device.pk} не найдено в Remnawave."
                )

            user_id = remote_user["id"]

            result = rw.update_user_expire_at(
                user_id,
                expires_at,
            )

            if result.get("status") == "DISABLED":
                rw.enable_user(user_id)

        current_client = Client.objects.get(pk=client_id)
        current_subscription = Subscription.objects.get(
            client_id=client_id
        )

        if (
            current_client.is_active
            and current_subscription.status
                == Subscription.STATUS_ACTIVE
            and current_subscription.expires_at == expires_at
        ):
            return

    raise ExpirationSyncChanged(
        "Подписка менялась во время восстановления "
        "устройств более пяти раз."
    )


def sync_expired_subscription(client_id):
    """
    Отключает устройства только пока подписка
    действительно истекла.

    При обнаружении продления восстанавливает
    все активные устройства.
    """
    client = Client.objects.get(pk=client_id)
    rw = _get_remnawave_client(client)

    if not _subscription_is_expired(client_id):
        restore_current_subscription(client_id, rw=rw)
        raise ExpirationSyncChanged(
            "Отключение отменено: подписка уже продлена."
        )

    for device in _active_devices(client_id):
        if not _subscription_is_expired(client_id):
            restore_current_subscription(client_id, rw=rw)
            raise ExpirationSyncChanged(
                "Отключение прервано: подписка продлена."
            )

        if not Device.objects.filter(
            pk=device.pk,
            client_id=client_id,
            is_active=True,
            unlinked_at__isnull=True,
        ).exists():
            continue

        remote_user = rw.get_user_by_username(
            device.remnawave_username
        )

        if not remote_user or remote_user.get("id") is None:
            raise ClientSyncError(
                f"Устройство {device.pk} не найдено в Remnawave."
            )

        if remote_user.get("status") != "DISABLED":
            rw.disable_user(remote_user["id"])

        # Проверяем состояние ПОСЛЕ HTTP-запроса.
        if not _subscription_is_expired(client_id):
            restore_current_subscription(client_id, rw=rw)
            raise ExpirationSyncChanged(
                "Подписка продлена во время HTTP-отключения. "
                "Устройства восстановлены."
            )

    return True
