"""Фиксация подтверждённой оплаты без начисления срока и сетевых запросов.

Вызывать только после независимой проверки платежа у доверенного шлюза.
"""

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from portal.models import Order, Payment


class PaymentConfirmationError(Exception):
    """Платёж нельзя безопасно подтвердить."""


@transaction.atomic
def confirm_succeeded_payment(*, payment_id, confirmed_amount):
    payment = (
        Payment.objects
        .select_for_update()
        .select_related("order")
        .get(pk=payment_id)
    )

    order = (
        Order.objects
        .select_for_update()
        .get(pk=payment.order_id)
    )

    try:
        amount = Decimal(str(confirmed_amount))
    except (InvalidOperation, ValueError, TypeError):
        raise PaymentConfirmationError(
            "Некорректная подтверждённая сумма платежа."
        )

    if not amount.is_finite() or amount <= 0:
        raise PaymentConfirmationError(
            "Подтверждённая сумма должна быть положительной."
        )

    if amount != payment.amount or amount != order.amount:
        raise PaymentConfirmationError(
            "Сумма подтверждённого платежа не совпадает с заказом."
        )

    if payment.status == Payment.STATUS_SUCCEEDED:
        if order.status not in (
            Order.STATUS_PAID,
            Order.STATUS_CANCELLED,
        ):
            raise PaymentConfirmationError(
                "Платёж успешен, но статус заказа не согласован."
            )
        return payment

    if payment.status != Payment.STATUS_PENDING:
        raise PaymentConfirmationError(
            "Подтверждение платежа в текущем статусе запрещено."
        )

    if order.status not in (
        Order.STATUS_PENDING,
        Order.STATUS_PAID,
        Order.STATUS_CANCELLED,
    ):
        raise PaymentConfirmationError(
            "Заказ находится в неподдерживаемом статусе."
        )

    # Повторная оплата того же заказа учитывается как отдельный платёж,
    # но не создаёт повторного задания на продление подписки.
    already_paid = order.status != Order.STATUS_PENDING

    now = timezone.now()

    payment.status = Payment.STATUS_SUCCEEDED
    payment.paid_at = now
    payment.save(update_fields=[
        "status",
        "paid_at",
        "updated_at",
    ])

    if not already_paid:
        order.status = Order.STATUS_PAID
        order.paid_at = now
        order.fulfillment_status = Order.FULFILLMENT_ACTIVATION_PENDING
        order.save(update_fields=[
            "status",
            "paid_at",
            "fulfillment_status",
            "updated_at",
        ])

    return payment
