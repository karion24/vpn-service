"""Подтверждение платежа и активация при открытии личного кабинета.

Статус оплаты никогда не берётся из URL возврата ЮKassa.
"""
import logging
import uuid
from decimal import Decimal, InvalidOperation

from django.utils import timezone
from datetime import timedelta

from .karion_payments_api import reconcile_invoice_payment, KarionPaymentsAPIError
from .models import Order, Payment
from .payment_confirmation import confirm_succeeded_payment, PaymentConfirmationError
from .paid_new_order_execution import activate_paid_new_order
from .paid_order_execution import apply_paid_order_entitlement
from .paid_order_sync import sync_paid_order

logger = logging.getLogger(__name__)


def _verify_invoice(payment, invoice):
    if not isinstance(invoice, dict):
        raise ValueError("Некорректный ответ шлюза")
    if uuid.UUID(str(invoice.get("id"))) != payment.gateway_invoice_id:
        raise ValueError("Несовпадение ID счёта")
    if invoice.get("external_id") != f"karion-vpn-order-{payment.order_id}":
        raise ValueError("Несовпадение номера заказа")
    if invoice.get("currency") != "RUB":
        raise ValueError("Неверная валюта")
    try:
        amount = Decimal(str(invoice.get("total")))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Неверная сумма") from None
    if not amount.is_finite() or amount != payment.amount or amount != payment.order.amount:
        raise ValueError("Несовпадение суммы")
    return amount


def reconcile_cabinet_payments(user):
    # Только свои недавние платежи, ограничение для быстрого открытия кабинета.
    recent = timezone.now() - timedelta(days=2)
    payments = list(
        Payment.objects.filter(
            order__user=user,
            provider="karion_payments",
            status=Payment.STATUS_PENDING,
            gateway_invoice_id__isnull=False,
            created_at__gte=recent,
        ).select_related("order").order_by("-pk")[:3]
    )
    for payment in payments:
        try:
            response = reconcile_invoice_payment(payment.gateway_invoice_id)
            if response.get("success") is not True:
                raise ValueError("Шлюз не подтвердил ответ")
            invoice = response.get("invoice")
            amount = _verify_invoice(payment, invoice)
            if invoice.get("status") == "paid":
                confirm_succeeded_payment(payment_id=payment.pk, confirmed_amount=amount)
        except (KarionPaymentsAPIError, PaymentConfirmationError, ValueError, TypeError) as exc:
            logger.warning("Сверка заказа %s в кабинете: %s", payment.order_id, exc)

    # Создаём подписку после подтверждения оплаты; повторный вызов безопасен.
    order_ids = list(Order.objects.filter(
        user=user,
        status=Order.STATUS_PAID,
        fulfillment_status__in=[
            Order.FULFILLMENT_ACTIVATION_PENDING,
            Order.FULFILLMENT_SYNC_PENDING,
        ],
    ).order_by("pk").values_list("pk", flat=True)[:5])
    for order_id in order_ids:
        try:
            order = Order.objects.get(pk=order_id)
            if order.order_type == Order.TYPE_NEW:
                if order.fulfillment_status == Order.FULFILLMENT_ACTIVATION_PENDING:
                    activate_paid_new_order(order_id)
            elif order.order_type == Order.TYPE_RENEWAL:
                if order.fulfillment_status == Order.FULFILLMENT_ACTIVATION_PENDING:
                    apply_paid_order_entitlement(order_id)
                order.refresh_from_db()
                if order.fulfillment_status == Order.FULFILLMENT_SYNC_PENDING:
                    sync_paid_order(order_id)
        except Exception:
            logger.exception("Ошибка активации оплаченного заказа %s", order_id)
