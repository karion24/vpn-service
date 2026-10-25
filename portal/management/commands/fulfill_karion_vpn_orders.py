"""Автоматическое исполнение оплаченных заказов Karion VPN."""

from django.core.management.base import BaseCommand

from portal.models import Order
from portal.paid_new_order_execution import activate_paid_new_order
from portal.paid_order_execution import apply_paid_order_entitlement
from portal.paid_order_sync import sync_paid_order


class Command(BaseCommand):
    help = "Активация новых VPN-подписок и продление существующих"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Выполнить обработку (по умолчанию DRY-RUN)",
        )
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        limit = options["limit"]

        if limit < 1:
            raise ValueError("--limit должен быть больше нуля.")

        orders = list(
            Order.objects.filter(
                status=Order.STATUS_PAID,
                fulfillment_status__in=[
                    Order.FULFILLMENT_ACTIVATION_PENDING,
                    Order.FULFILLMENT_SYNC_PENDING,
                ],
            )
            .order_by("pk")
            .values_list("pk", flat=True)[:limit]
        )

        self.stdout.write(
            "РЕЖИМ: " + ("APPLY" if apply_changes else "DRY-RUN")
        )

        completed = 0
        errors = 0

        for order_id in orders:
            try:
                order = Order.objects.get(pk=order_id)

                if not apply_changes:
                    self.stdout.write(
                        f"DRY-RUN Order #{order_id}: "
                        f"type={order.order_type}, "
                        f"fulfillment={order.fulfillment_status}"
                    )
                    continue

                if order.order_type == Order.TYPE_NEW:
                    order = activate_paid_new_order(order_id)

                elif order.order_type == Order.TYPE_RENEWAL:
                    if (
                        order.fulfillment_status
                        == Order.FULFILLMENT_ACTIVATION_PENDING
                    ):
                        apply_paid_order_entitlement(order_id)

                    order = Order.objects.get(pk=order_id)

                    if (
                        order.fulfillment_status
                        == Order.FULFILLMENT_SYNC_PENDING
                    ):
                        sync_paid_order(order_id)

                    order = Order.objects.get(pk=order_id)

                else:
                    raise ValueError(
                        f"Неизвестный тип заказа: {order.order_type}"
                    )

                if (
                    order.fulfillment_status
                    == Order.FULFILLMENT_COMPLETED
                ):
                    completed += 1
                    self.stdout.write(
                        f"COMPLETED Order #{order_id}"
                    )

            except Exception as exc:
                errors += 1
                self.stderr.write(
                    f"ERROR Order #{order_id}: "
                    f"{type(exc).__name__}: {exc}"
                )

        self.stdout.write("")
        self.stdout.write("========== ИТОГИ ==========")
        self.stdout.write(f"Заказов в очереди: {len(orders)}")
        self.stdout.write(f"Завершено: {completed}")
        self.stdout.write(f"Ошибок: {errors}")

        if not apply_changes:
            self.stdout.write("DRY-RUN: изменения не выполнялись.")

        if errors:
            raise SystemExit(1)
