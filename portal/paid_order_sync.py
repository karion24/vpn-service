"""
Синхронизация оплаченных продлений с Remnawave.

Сетевые запросы выполняются вне транзакций БД.
При изменении состояния во время HTTP выполняется повторный проход.
"""

from django.db import transaction

from portal.models import Client, Device, Order, Subscription
from portal.services import _get_remnawave_client
from portal.subscription_lock import subscription_lock


class PaidOrderSyncError(Exception):
    pass


def _snapshot(client_id):
    client = Client.objects.get(pk=client_id)
    subscription = Subscription.objects.get(client=client)

    devices = list(
        Device.objects.filter(
            client=client,
            is_active=True,
            unlinked_at__isnull=True,
            remnawave_user_id__isnull=False,
        ).order_by("pk")
    )

    return client, subscription, devices


def _state(client_id):
    client, subscription, devices = _snapshot(client_id)

    return (
        client.is_active,
        client.expires_at,
        subscription.status,
        subscription.expires_at,
        tuple(
            (
                device.pk,
                device.remnawave_user_id,
                device.remnawave_username,
            )
            for device in devices
        ),
    )


def sync_paid_order(order_id, max_attempts=5):
    """
    Синхронизирует актуальное состояние VPN-подписки.

    При успехе завершает все ожидающие синхронизации
    оплаченные продления той же VPN-подписки.
    """

    order = Order.objects.get(pk=order_id)

    if order.fulfillment_status == Order.FULFILLMENT_COMPLETED:
        return order

    if (
        order.status != Order.STATUS_PAID
        or order.order_type != Order.TYPE_RENEWAL
        or order.fulfillment_status != Order.FULFILLMENT_SYNC_PENDING
        or not order.client_id
    ):
        raise PaidOrderSyncError(
            f"Заказ #{order_id} не готов к синхронизации."
        )

    client_id = order.client_id

    with subscription_lock(client_id, timeout=30):
        order.refresh_from_db()
        if order.fulfillment_status == Order.FULFILLMENT_COMPLETED:
            return order
        if (
            order.status != Order.STATUS_PAID
            or order.order_type != Order.TYPE_RENEWAL
            or order.fulfillment_status != Order.FULFILLMENT_SYNC_PENDING
            or order.client_id != client_id
        ):
            raise PaidOrderSyncError(
                f"Заказ #{order_id} изменился во время ожидания блокировки."
            )


        for attempt in range(max_attempts):
            client, subscription, devices = _snapshot(client_id)

            before = _state(client_id)

            if (
                not client.is_active
                or subscription.status != Subscription.STATUS_ACTIVE
                or subscription.expires_at is None
            ):
                raise PaidOrderSyncError(
                    "Подписка неактивна или не имеет срока окончания."
                )

            rw = _get_remnawave_client(client)

            for device in devices:
                # Устройство могло быть отвязано после формирования списка.
                if not Device.objects.filter(
                    pk=device.pk,
                    client_id=client_id,
                    is_active=True,
                    unlinked_at__isnull=True,
                    remnawave_user_id=device.remnawave_user_id,
                ).exists():
                    continue

                remote = rw.get_user_by_username(
                    device.remnawave_username
                )

                if not remote or remote.get("id") is None:
                    raise PaidOrderSyncError(
                        f"Устройство #{device.pk} отсутствует в Remnawave."
                    )

                remote_id = int(remote["id"])

                if remote_id != device.remnawave_user_id:
                    raise PaidOrderSyncError(
                        f"Несовпадение Remnawave ID устройства #{device.pk}."
                    )

                updated = rw.update_user_expire_at(
                    remote_id,
                    subscription.expires_at,
                )

                was_disabled = remote.get("status") == "DISABLED"
                still_disabled = updated.get("status") == "DISABLED"

                if was_disabled or still_disabled:
                    # Повторная проверка перед включением.
                    if Device.objects.filter(
                        pk=device.pk,
                        client_id=client_id,
                        is_active=True,
                        unlinked_at__isnull=True,
                    ).exists():
                        rw.enable_user(remote_id)

            after = _state(client_id)

            if before != after:
                continue

            # Сначала проверяем, что состояние всё ещё актуально.
            # Сетевых запросов внутри транзакции нет.
            with transaction.atomic():
                if _state(client_id) != before:
                    continue

                Order.objects.filter(
                    client_id=client_id,
                    status=Order.STATUS_PAID,
                    order_type=Order.TYPE_RENEWAL,
                    fulfillment_status=Order.FULFILLMENT_SYNC_PENDING,
                ).update(
                    fulfillment_status=Order.FULFILLMENT_COMPLETED,
                )

            return Order.objects.get(pk=order_id)

        raise PaidOrderSyncError(
            f"Состояние подписки #{client_id} менялось "
            f"во время синхронизации {max_attempts} раз."
        )
