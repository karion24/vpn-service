"""Переход из личного кабинета Karion VPN к оплате заказа."""

import logging
import uuid
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from portal.karion_payments_api import (
    KarionPaymentsAPIError,
    create_invoice,
    start_invoice_payment,
)
from portal.models import Order, Payment

logger = logging.getLogger(__name__)


@login_required
@require_POST
def pay_order(request, order_id):
    order = get_object_or_404(
        Order.objects.select_related("plan"),
        pk=order_id,
        user=request.user,
    )

    if order.status != Order.STATUS_PENDING:
        messages.error(request, "Заказ уже оплачен или отменён.")
        return redirect("order_detail", order_id=order.pk)

    if (
        order.payment_expires_at
        and order.payment_expires_at <= timezone.now()
    ):
        messages.error(request, "Срок оплаты заказа истёк.")
        return redirect("order_detail", order_id=order.pk)

    email = (request.user.email or "").strip()

    if not email:
        messages.error(
            request,
            "Для онлайн-оплаты укажите email в профиле.",
        )
        return redirect("order_detail", order_id=order.pk)

    try:
        invoice = create_invoice(
            order=order,
            customer_email=email,
            customer_name=request.user.get_full_name(),
        )

        invoice_id = uuid.UUID(str(invoice["id"]))
        invoice_url = str(invoice["url"])

        parsed = urlparse(invoice_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "pay.karion24.ru"
            or parsed.port is not None
            or parsed.username
            or parsed.password
            or not parsed.path.startswith("/invoice/")
            or parsed.query
            or parsed.fragment
        ):
            raise KarionPaymentsAPIError(
                "Платёжный шлюз вернул недопустимую ссылку."
            )

        try:
            confirmed_amount = Decimal(str(invoice["total"]))
        except (InvalidOperation, ValueError, TypeError):
            raise KarionPaymentsAPIError(
                "Некорректная сумма счёта."
            ) from None

        if confirmed_amount != order.amount:
            raise KarionPaymentsAPIError(
                "Сумма счёта не совпадает с заказом."
            )

        with transaction.atomic():
            locked_order = Order.objects.select_for_update().get(
                pk=order.pk,
                user=request.user,
            )

            if locked_order.status != Order.STATUS_PENDING:
                raise KarionPaymentsAPIError(
                    "Статус заказа изменился."
                )

            if (
                locked_order.payment_expires_at
                and locked_order.payment_expires_at <= timezone.now()
            ):
                raise KarionPaymentsAPIError(
                    "Срок оплаты заказа истёк."
                )

            payment, created = Payment.objects.get_or_create(
                order=locked_order,
                provider="karion_payments",
                idempotency_key=f"karion-vpn-order-{locked_order.pk}",
                defaults={
                    "amount": locked_order.amount,
                    "status": Payment.STATUS_PENDING,
                    "gateway_invoice_id": invoice_id,
                },
            )

            if (
                payment.gateway_invoice_id
                and payment.gateway_invoice_id != invoice_id
            ):
                raise KarionPaymentsAPIError(
                    "Для заказа зарегистрирован другой счёт."
                )

            if payment.amount != locked_order.amount:
                raise KarionPaymentsAPIError(
                    "Сумма платежа не совпадает с заказом."
                )

            if payment.status != Payment.STATUS_PENDING:
                raise KarionPaymentsAPIError(
                    "Платёж уже обработан."
                )

            if not payment.gateway_invoice_id:
                payment.gateway_invoice_id = invoice_id
                payment.save(update_fields=[
                    "gateway_invoice_id",
                    "updated_at",
                ])

        confirmation_url = start_invoice_payment(invoice_id)

        checkout = urlparse(confirmation_url)
        allowed_hosts = {
            "yookassa.ru",
            "yoomoney.ru",
        }

        if (
            checkout.scheme != "https"
            or checkout.hostname not in allowed_hosts
            or checkout.username
            or checkout.password
            or checkout.port is not None
            or not checkout.path.startswith("/")
        ):
            raise KarionPaymentsAPIError(
                "Платёжный шлюз вернул недопустимый адрес ЮKassa."
            )

        return redirect(confirmation_url)

    except (
        KarionPaymentsAPIError,
        KeyError,
        ValueError,
        Payment.MultipleObjectsReturned,
    ) as exc:
        logger.warning(
            "Ошибка создания счёта для заказа %s: %s",
            order.pk,
            exc,
        )
        messages.error(
            request,
            "Не удалось открыть оплату. Попробуйте позже.",
        )
        return redirect("order_detail", order_id=order.pk)
