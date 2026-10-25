"""Сверка платежей VPN с централизованным шлюзом Karion."""

import logging
import uuid
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand

from portal.karion_payments_api import (
    KarionPaymentsAPIError,
    get_invoice,
)
from portal.models import Payment
from portal.payment_confirmation import (
    PaymentConfirmationError,
    confirm_succeeded_payment,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Сверка VPN-платежей через API Karion Payments"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Подтверждать оплаченные счета (без параметра DRY-RUN)",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=100,
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        limit = options["limit"]

        if limit < 1:
            self.stderr.write("ОШИБКА: --limit должен быть больше нуля.")
            return

        payments = (
            Payment.objects
            .filter(
                provider="karion_payments",
                status=Payment.STATUS_PENDING,
                gateway_invoice_id__isnull=False,
            )
            .select_related("order")
            .order_by("pk")[:limit]
        )

        checked = 0
        confirmed = 0
        waiting = 0
        errors = 0

        self.stdout.write(
            "РЕЖИМ: " + ("APPLY" if apply_changes else "DRY-RUN")
        )

        for payment in payments:
            checked += 1

            try:
                result = get_invoice(payment.gateway_invoice_id)

                if result.get("success") is not True:
                    raise ValueError("Шлюз не подтвердил ответ.")

                invoice = result.get("invoice")

                if not isinstance(invoice, dict):
                    raise ValueError("Нет данных счёта.")

                if uuid.UUID(str(invoice.get("id"))) != payment.gateway_invoice_id:
                    raise ValueError("UUID счёта не совпадает.")

                expected_external_id = (
                    f"karion-vpn-order-{payment.order_id}"
                )

                if invoice.get("external_id") != expected_external_id:
                    raise ValueError("Внешний ID заказа не совпадает.")

                if invoice.get("currency") != "RUB":
                    raise ValueError("Валюта счёта не совпадает.")

                try:
                    amount = Decimal(str(invoice.get("total")))
                except (InvalidOperation, ValueError, TypeError):
                    raise ValueError("Некорректная сумма счёта.") from None

                if (
                    not amount.is_finite()
                    or amount != payment.amount
                    or amount != payment.order.amount
                ):
                    raise ValueError("Сумма счёта не совпадает.")

                status = invoice.get("status")

                if status != "paid":
                    waiting += 1
                    self.stdout.write(
                        f"WAIT Payment #{payment.pk}: invoice={status}"
                    )
                    continue

                if not apply_changes:
                    self.stdout.write(
                        f"DRY-RUN Payment #{payment.pk}: "
                        "оплата подтверждена шлюзом"
                    )
                    continue

                confirm_succeeded_payment(
                    payment_id=payment.pk,
                    confirmed_amount=amount,
                )

                confirmed += 1
                self.stdout.write(
                    f"CONFIRMED Payment #{payment.pk}: "
                    f"Order #{payment.order_id}"
                )

            except (
                KarionPaymentsAPIError,
                PaymentConfirmationError,
                ValueError,
                TypeError,
            ) as exc:
                errors += 1
                logger.warning(
                    "Ошибка сверки Payment #%s: %s",
                    payment.pk,
                    exc,
                )
                self.stderr.write(
                    f"ERROR Payment #{payment.pk}: {exc}"
                )

        self.stdout.write("")
        self.stdout.write("========== ИТОГИ ==========")
        self.stdout.write(f"Проверено: {checked}")
        self.stdout.write(f"Подтверждено: {confirmed}")
        self.stdout.write(f"Ожидают оплаты: {waiting}")
        self.stdout.write(f"Ошибок: {errors}")

        if not apply_changes:
            self.stdout.write("DRY-RUN: изменения не выполнялись.")
