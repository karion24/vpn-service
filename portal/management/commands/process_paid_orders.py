"""Безопасная обработка очереди оплаченных заказов."""

from django.core.management.base import BaseCommand

from portal.models import Order
from portal.paid_order_execution import apply_paid_order_entitlement


class Command(BaseCommand):
    help = "Начисление срока по подтверждённым оплаченным заказам"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Выполнить начисление (по умолчанию DRY-RUN)",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=100,
            help="Максимальное число заказов за запуск",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        limit = options["limit"]

        if limit < 1:
            self.stderr.write("ОШИБКА: --limit должен быть больше нуля.")
            return

        orders = list(
            Order.objects.filter(
                status=Order.STATUS_PAID,
                fulfillment_status=Order.FULFILLMENT_ACTIVATION_PENDING,
            )
            .order_by("pk")
            .values_list("pk", flat=True)[:limit]
        )

        processed = 0
        skipped = 0
        errors = 0

        self.stdout.write(
            "РЕЖИМ: " + ("APPLY" if apply_changes else "DRY-RUN")
        )
        self.stdout.write(f"Заказов в выборке: {len(orders)}")

        for order_id in orders:
            order = Order.objects.get(pk=order_id)

            if order.order_type != Order.TYPE_RENEWAL:
                skipped += 1
                self.stdout.write(
                    f"SKIP order #{order_id}: "
                    "покупка новой подписки пока не поддерживается"
                )
                continue

            if not apply_changes:
                self.stdout.write(
                    f"DRY-RUN order #{order_id}: ожидает начисления"
                )
                continue

            try:
                result = apply_paid_order_entitlement(order_id)
                processed += 1
                self.stdout.write(
                    f"PROCESSED order #{order_id}: "
                    f"fulfillment={result.fulfillment_status}"
                )
            except Exception as exc:
                errors += 1
                self.stderr.write(
                    f"ERROR order #{order_id}: "
                    f"{type(exc).__name__}: {exc}"
                )

        self.stdout.write("")
        self.stdout.write("========== ИТОГИ ==========")
        self.stdout.write(f"Начислено/обработано: {processed}")
        self.stdout.write(f"Пропущено: {skipped}")
        self.stdout.write(f"Ошибок: {errors}")

        if not apply_changes:
            self.stdout.write(
                "DRY-RUN: база данных не изменялась."
            )
