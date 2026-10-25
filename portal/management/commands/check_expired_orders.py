from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.models import Order, Payment


class Command(BaseCommand):
    help = "Диагностика просроченных заказов без изменения данных"

    def handle(self, *args, **options):
        now = timezone.now()

        orders = (
            Order.objects
            .filter(
                status=Order.STATUS_PENDING,
                payment_expires_at__isnull=False,
                payment_expires_at__lte=now,
            )
            .prefetch_related("payments")
            .order_by("id")
        )

        counts = {
            "without_payments": 0,
            "needs_reconciliation": 0,
            "finished_without_success": 0,
        }

        for order in orders:
            payments = list(order.payments.all())
            statuses = {payment.status for payment in payments}

            if not payments:
                category = "without_payments"

            elif statuses & {
                Payment.STATUS_PENDING,
                Payment.STATUS_SUCCEEDED,
            }:
                category = "needs_reconciliation"

            else:
                category = "finished_without_success"

            counts[category] += 1

            self.stdout.write(
                f"Order #{order.pk} | "
                f"deadline={order.payment_expires_at.isoformat()} | "
                f"category={category} | "
                f"payments={len(payments)}"
            )

        self.stdout.write("")
        self.stdout.write("ИТОГИ:")
        self.stdout.write(
            f"Без платежей: {counts['without_payments']}"
        )
        self.stdout.write(
            "Требуют сверки: "
            f"{counts['needs_reconciliation']}"
        )
        self.stdout.write(
            "Завершённые без успеха: "
            f"{counts['finished_without_success']}"
        )
        self.stdout.write(
            f"Всего просроченных: {sum(counts.values())}"
        )
        self.stdout.write(
            "Режим диагностики: изменения в БД не выполнялись."
        )
