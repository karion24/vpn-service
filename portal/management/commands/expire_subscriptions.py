from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from portal.expiration_sync import (
    ExpirationSyncChanged,
    sync_expired_subscription,
)
from portal.models import Order, Subscription
from portal.payment_fulfillment import has_pending_paid_fulfillment


class Command(BaseCommand):
    help = "Отключает истёкшие подписки Karion"

    def handle(self, *args, **options):
        order_table = Order._meta.db_table

        with connection.cursor() as cursor:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor,
                    order_table,
                )
            }

        fulfillment_column_available = (
            "fulfillment_status" in columns
        )

        now = timezone.now()

        # Повторно проверяем и ранее истёкшие подписки:
        # предыдущая попытка HTTP-синхронизации могла завершиться
        # частичным сбоем.
        subscription_ids = list(
            Subscription.objects.filter(
                status__in=[
                    Subscription.STATUS_ACTIVE,
                    Subscription.STATUS_EXPIRED,
                ],
                expires_at__isnull=False,
                expires_at__lte=now,
            ).values_list("id", flat=True)
        )

        if not subscription_ids:
            self.stdout.write("No expired subscriptions.")
            return

        processed = 0
        errors = 0
        skipped = 0

        for subscription_id in subscription_ids:
            client_id = None

            try:
                # Только изменение состояния БД.
                # HTTP-запросов внутри транзакции нет.
                with transaction.atomic():
                    subscription = (
                        Subscription.objects
                        .select_for_update()
                        .select_related("client")
                        .get(pk=subscription_id)
                    )

                    if (
                        subscription.status not in (
                            Subscription.STATUS_ACTIVE,
                            Subscription.STATUS_EXPIRED,
                        )
                        or not subscription.expires_at
                        or subscription.expires_at > timezone.now()
                    ):
                        skipped += 1
                        continue

                    client = subscription.client
                    client_id = client.pk

                    if (
                        fulfillment_column_available
                        and has_pending_paid_fulfillment(client_id)
                    ):
                        self.stdout.write(
                            f"Subscription {subscription_id}: "
                            "expiration skipped — paid renewal "
                            "awaits fulfillment."
                        )
                        skipped += 1
                        continue

                    if subscription.status == Subscription.STATUS_ACTIVE:
                        subscription.status = Subscription.STATUS_EXPIRED
                        subscription.save(
                            update_fields=["status", "updated_at"]
                        )

                        client.is_active = False
                        client.save(
                            update_fields=["is_active", "updated_at"]
                        )

                # HTTP выполняется после фиксации транзакции.
                # Функция повторно проверяет состояние подписки
                # перед отключением каждого устройства.
                sync_expired_subscription(client_id)

                processed += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Expired subscription {subscription_id}: "
                        f"client={client_id}; synchronized."
                    )
                )

            except ExpirationSyncChanged as exc:
                skipped += 1
                self.stdout.write(
                    f"Subscription {subscription_id}: {exc}"
                )

            except Exception as exc:
                errors += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"Subscription {subscription_id}: {exc}"
                    )
                )

        self.stdout.write(
            f"Processed: {processed}; "
            f"errors: {errors}; skipped: {skipped}"
        )
