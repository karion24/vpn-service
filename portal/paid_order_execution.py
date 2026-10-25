"""
Начисление оплаченного срока подписки.

В этом модуле нет сетевых обращений к Remnawave.
"""

from datetime import timedelta

import time

from django.db import OperationalError, transaction
from django.utils import timezone

from portal.models import Client, Order, Subscription


class PaidOrderExecutionError(Exception):
    pass


def apply_paid_order_entitlement(order_id):
    """
    Повторяет целую транзакцию при временной блокировке SQLite.

    Повторная попытка заново читает состояние заказа.
    """
    for attempt in range(5):
        try:
            return _apply_paid_order_entitlement_atomic(order_id)
        except OperationalError as exc:
            message = str(exc).lower()

            if (
                "locked" not in message
                or attempt == 4
            ):
                raise

            time.sleep(0.05 * (attempt + 1))


@transaction.atomic
def _apply_paid_order_entitlement_atomic(order_id):
    """
    Начисляет дни оплаченного продления ровно один раз.

    Переход activation_pending -> sync_pending
    фиксируется вместе с изменением срока подписки.
    """

    order = (
        Order.objects
        .select_for_update()
        .get(pk=order_id)
    )

    if order.status != Order.STATUS_PAID:
        raise PaidOrderExecutionError(
            "Заказ не имеет подтверждённого статуса оплаты."
        )

    if order.fulfillment_status in (
        Order.FULFILLMENT_SYNC_PENDING,
        Order.FULFILLMENT_COMPLETED,
    ):
        return order

    if (
        order.fulfillment_status
        != Order.FULFILLMENT_ACTIVATION_PENDING
    ):
        raise PaidOrderExecutionError(
            "Заказ не ожидает начисления оплаченного срока."
        )

    if order.order_type != Order.TYPE_RENEWAL:
        raise PaidOrderExecutionError(
            "Этот обработчик пока поддерживает только продления."
        )

    if not order.client_id:
        raise PaidOrderExecutionError(
            "В заказе отсутствует подключение."
        )

    client = (
        Client.objects
        .select_for_update()
        .get(pk=order.client_id)
    )

    subscription = (
        Subscription.objects
        .select_for_update()
        .get(client=client)
    )

    now = timezone.now()

    base_date = client.expires_at

    if base_date is None or base_date < now:
        base_date = now

    new_expires_at = (
        base_date + timedelta(days=order.duration_days)
    )

    client.expires_at = new_expires_at
    client.is_active = True
    client.save(
        update_fields=[
            "expires_at",
            "is_active",
            "updated_at",
        ]
    )

    subscription.expires_at = new_expires_at
    subscription.status = Subscription.STATUS_ACTIVE
    subscription.price = order.amount

    if not subscription.starts_at:
        subscription.starts_at = now

    subscription.save(
        update_fields=[
            "expires_at",
            "status",
            "price",
            "starts_at",
            "updated_at",
        ]
    )

    order.fulfillment_status = (
        Order.FULFILLMENT_SYNC_PENDING
    )
    order.save(
        update_fields=[
            "fulfillment_status",
            "updated_at",
        ]
    )

    return order
