"""Диагностика платежей, требующих ручной финансовой сверки."""

from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from portal.models import Order, Payment


class Command(BaseCommand):
    help = "Показать подтверждённые платежи, требующие ручной сверки"

    def handle(self, *args, **options):
        cancelled = (
            Payment.objects
            .filter(
                status=Payment.STATUS_SUCCEEDED,
                order__status=Order.STATUS_CANCELLED,
            )
            .select_related("order")
            .order_by("order_id", "id")
        )

        duplicate_orders = (
            Order.objects
            .annotate(
                successful_payments=Count(
                    "payments",
                    filter=Q(
                        payments__status=Payment.STATUS_SUCCEEDED,
                    ),
                ),
            )
            .filter(successful_payments__gt=1)
            .order_by("id")
        )

        cancelled_count = 0
        duplicate_count = 0

        self.stdout.write("========== ОПЛАТЫ ОТМЕНЁННЫХ ЗАКАЗОВ ==========")

        for payment in cancelled:
            cancelled_count += 1
            self.stdout.write(
                f"Payment #{payment.pk} | "
                f"Order #{payment.order_id} | "
                f"amount={payment.amount} | "
                f"provider={payment.provider} | "
                f"provider_payment_id={payment.provider_payment_id or '-'}"
            )

        self.stdout.write("")
        self.stdout.write("========== ПОВТОРНЫЕ УСПЕШНЫЕ ОПЛАТЫ ==========")

        for order in duplicate_orders:
            duplicate_count += 1
            self.stdout.write(
                f"Order #{order.pk} | "
                f"successful_payments={order.successful_payments} | "
                f"amount={order.amount}"
            )

        self.stdout.write("")
        self.stdout.write("========== ИТОГИ ==========")
        self.stdout.write(
            f"Успешных платежей отменённых заказов: {cancelled_count}"
        )
        self.stdout.write(
            f"Заказов с несколькими успешными платежами: {duplicate_count}"
        )
        self.stdout.write("Режим: только чтение. Изменений нет.")
