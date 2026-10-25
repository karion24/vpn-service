"""
Состояние исполнения оплаченных VPN-заказов.

Модуль не выполняет сетевых запросов и не изменяет БД.
"""

from django.db.models import Q

from .models import Order


PROTECTED_FULFILLMENT_STATUSES = (
    Order.FULFILLMENT_ACTIVATION_PENDING,
    Order.FULFILLMENT_SYNC_PENDING,
    Order.FULFILLMENT_ERROR,
)


def has_pending_paid_fulfillment(client_id):
    """
    Проверяет, существует ли подтверждённый оплаченный
    заказ на продление, исполнение которого не завершено.

    ВАЖНО:
    Это предварительная проверка, а не блокировка.
    Перед изменением срока в конкурентном коде состояние
    необходимо повторно проверить внутри транзакции.
    """
    if client_id is None:
        return False

    return (
        Order.objects
        .filter(
            client_id=client_id,
            order_type=Order.TYPE_RENEWAL,
            fulfillment_status__in=PROTECTED_FULFILLMENT_STATUSES,
        )
        .filter(
            Q(status=Order.STATUS_PAID)
            | Q(payments__status="succeeded")
        )
        .exists()
    )
