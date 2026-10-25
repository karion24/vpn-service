from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from portal.models import Order, Payment
from portal.order_expiration import can_expire_order
from portal.karion_payments_api import (
    cancel_invoice,
    KarionPaymentsAPIError,
)


class Command(BaseCommand):
    help = "Отмена просроченных неоплаченных заказов VPN"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Применить отмену (без параметра dry-run)",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        now = timezone.now()

        order_ids = list(
            Order.objects.filter(
                status=Order.STATUS_PENDING,
                payment_expires_at__isnull=False,
                payment_expires_at__lte=now,
            ).values_list("id", flat=True)
        )

        cancelled = 0
        protected = 0

        self.stdout.write(
            "РЕЖИМ: " + ("APPLY" if apply_changes else "DRY-RUN")
        )

        for order_id in order_ids:
            with transaction.atomic():
                order = (
                    Order.objects
                    .select_for_update()
                    .get(pk=order_id)
                )

                if (
                    order.status != Order.STATUS_PENDING
                    or order.payment_expires_at is None
                    or order.payment_expires_at > timezone.now()
                ):
                    continue

                payments = list(
                    Payment.objects
                    .select_for_update()
                    .filter(order=order)
                )

                if not apply_changes:
                    self.stdout.write(
                        f"DRY-RUN order #{order_id}: "
                        "просрочен, проверка без изменений"
                    )
                    continue

                blocked = False

                for payment in payments:
                    if payment.status in (
                        Payment.STATUS_FAILED,
                        Payment.STATUS_CANCELLED,
                    ):
                        continue

                    if (
                        payment.status != Payment.STATUS_PENDING
                        or not payment.gateway_invoice_id
                    ):
                        blocked = True
                        break

                    try:
                        cancel_invoice(payment.gateway_invoice_id)
                    except KarionPaymentsAPIError as exc:
                        self.stdout.write(
                            f"SKIP order #{order_id}: {exc}"
                        )
                        blocked = True
                        break

                    payment.status = Payment.STATUS_CANCELLED
                    payment.save(
                        update_fields=["status", "updated_at"]
                    )

                if blocked or not can_expire_order(order, timezone.now()):
                    protected += 1
                    self.stdout.write(
                        f"SKIP order #{order_id}: "
                        "отмена не подтверждена"
                    )
                    continue

                order.status = Order.STATUS_CANCELLED
                order.save(
                    update_fields=["status", "updated_at"]
                )

                cancelled += 1
                self.stdout.write(
                    f"CANCELLED order #{order_id}"
                )

        self.stdout.write("")
        self.stdout.write(f"Всего просроченных: {len(order_ids)}")
        self.stdout.write(f"Защищены от отмены: {protected}")
        self.stdout.write(f"Отменено: {cancelled}")
