from datetime import datetime

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from portal.models import Subscription, Device
from portal.payment_fulfillment import has_pending_paid_fulfillment
from portal.sync_concurrency import subscription_state_is_current
from portal.remnawave import RemnawaveClient


TOLERANCE_SECONDS = 1.0


def parse_rw_datetime(value):
    if not value:
        return None

    dt = parse_datetime(value)

    if dt is None:
        try:
            dt = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )
        except Exception:
            return None

    if timezone.is_naive(dt):
        dt = timezone.make_aware(
            dt,
            timezone.get_current_timezone(),
        )

    return dt


def same_dt(a, b):
    if a is None and b is None:
        return True

    if a is None or b is None:
        return False

    return abs(
        (a - b).total_seconds()
    ) < TOLERANCE_SECONDS


class Command(BaseCommand):
    help = "Двусторонняя синхронизация срока Django <-> Remnawave"

    def handle(self, *args, **options):

        processed = 0
        django_to_rw = 0
        rw_to_django = 0
        unchanged = 0
        errors = 0

        subscriptions = (
            Subscription.objects
            .select_related(
                "client",
                "client__remnawave_server",
            )
            .all()
        )

        from django.db import connection
        from portal.models import Order

        with connection.cursor() as cursor:
            columns = {
                col.name
                for col in connection.introspection.get_table_description(
                    cursor, Order._meta.db_table
                )
            }

        fulfillment_column_available = (
            'fulfillment_status' in columns
        )

        for subscription in subscriptions:

            client = subscription.client

            # Оплаченное продление с незавершённым
            # исполнением обрабатывает отдельный механизм.
            if fulfillment_column_available:
                if has_pending_paid_fulfillment(client.pk):
                    continue

            if not client.remnawave_server:
                continue

            devices = list(
                Device.objects
                .filter(
                    client=client,
                    is_active=True,
                )
                .exclude(
                    remnawave_username__isnull=True
                )
                .exclude(
                    remnawave_username=""
                )
                .exclude(
                    remnawave_user_id__isnull=True
                )
            )

            if not devices:
                continue

            rw = RemnawaveClient(
                client.remnawave_server
            )

            try:
                remote_items = []

                for device in devices:

                    remote = rw.get_user_by_username(
                        device.remnawave_username
                    )

                    remote_expire = parse_rw_datetime(
                        remote.get("expireAt")
                    )

                    remote_updated = parse_rw_datetime(
                        remote.get("updatedAt")
                    )

                    if remote_expire is None:
                        continue

                    remote_items.append(
                        {
                            "device": device,
                            "user_id": remote.get("id"),
                            "expire": remote_expire,
                            "updated": remote_updated,
                        }
                    )

                if not remote_items:
                    continue

                local_expire = subscription.expires_at
                synced_value = subscription.expires_synced_value

                # Изменяли ли срок в Django после последней
                # согласованной версии?
                local_changed = not same_dt(
                    local_expire,
                    synced_value,
                )

                # Какие устройства Remnawave отличаются
                # от последней согласованной версии?
                remote_changed_items = [
                    item
                    for item in remote_items
                    if not same_dt(
                        item["expire"],
                        synced_value,
                    )
                ]

                # -------------------------------------------------
                # 1. Django изменён, Remnawave не изменён
                # -------------------------------------------------

                if local_changed and not remote_changed_items:

                    if local_expire is None:
                        raise RuntimeError(
                            "Django expires_at пустой; "
                            "автоматически отправлять пустой срок "
                            "в Remnawave запрещено."
                        )

                    for item in remote_items:
                        if not subscription_state_is_current(
                            subscription.pk,
                            local_expire,
                            synced_value,
                            check_paid_fulfillment=fulfillment_column_available,
                        ):
                            raise RuntimeError(
                                "Отменена отправка устаревшего "
                                "срока в Remnawave: подписка "
                                "или оплаченный заказ изменились."
                            )

                        rw.update_user_expire_at(
                            item["user_id"],
                            local_expire,
                        )

                    # После HTTP-запроса проверяем, не пришла ли
                    # оплата во время отправки старого срока.

                    # Восстанавливаем срок после HTTP-гонки.
                    # Повторная оплата во время восстановления
                    # требует ещё одной синхронизации.
                    if not subscription_state_is_current(
                        subscription.pk,
                        local_expire,
                        synced_value,
                        check_paid_fulfillment=fulfillment_column_available,
                    ):
                        restored = False

                        for recovery_attempt in range(5):
                            current = (
                                Subscription.objects
                                .get(pk=subscription.pk)
                            )

                            current_expire = current.expires_at
                            current_synced = current.expires_synced_value

                            if current_expire is None:
                                raise RuntimeError(
                                    "Невозможно восстановить "
                                    "пустой срок подписки."
                                )

                            for restore_item in remote_items:
                                rw.update_user_expire_at(
                                    restore_item["user_id"],
                                    current_expire,
                                )

                            if subscription_state_is_current(
                                subscription.pk,
                                current_expire,
                                current_synced,
                                check_paid_fulfillment=False,
                            ):
                                restored = True
                                break

                        if not restored:
                            raise RuntimeError(
                                "Подписка продолжает изменяться. "
                                "Восстановление Remnawave "
                                "не завершено после 5 попыток."
                            )

                        raise RuntimeError(
                            "Подписка изменилась во время HTTP-запроса. "
                            "Актуальный срок восстановлен в Remnawave."
                        )


                    with transaction.atomic():

                        locked = (
                            Subscription.objects
                            .select_for_update()
                            .select_related("client")
                            .get(pk=subscription.pk)
                        )

                        if not subscription_state_is_current(
                            subscription.pk,
                            local_expire,
                            synced_value,
                            check_paid_fulfillment=fulfillment_column_available,
                        ):
                            raise RuntimeError(
                                "Подписка или оплаченный заказ "
                                "изменились во время синхронизации. "
                                "Устаревшая запись отменена."
                            )

                        if not same_dt(
                            locked.expires_at,
                            local_expire,
                        ):
                            raise RuntimeError(
                                "Срок подписки изменился во время "
                                "синхронизации. Устаревшая запись отменена."
                            )

                        locked.expires_synced_value = (
                            local_expire
                        )
                        locked.expires_synced_at = (
                            timezone.now()
                        )

                        locked.save(
                            update_fields=[
                                "expires_synced_value",
                                "expires_synced_at",
                            ]
                        )

                        c = locked.client

                        if not same_dt(
                            c.expires_at,
                            local_expire,
                        ):
                            c.expires_at = local_expire
                            c.save(
                                update_fields=[
                                    "expires_at",
                                    "updated_at",
                                ]
                            )

                    django_to_rw += 1

                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Client {client.id}: "
                            f"Django -> Remnawave: "
                            f"{local_expire}"
                        )
                    )

                # -------------------------------------------------
                # 2. Remnawave изменён, Django не изменён
                # -------------------------------------------------

                elif (
                    not local_changed
                    and remote_changed_items
                ):

                    # Если несколько устройств изменены,
                    # побеждает наиболее свежее updatedAt.
                    source = max(
                        remote_changed_items,
                        key=lambda item: (
                            item["updated"]
                            or datetime.min.replace(
                                tzinfo=timezone.utc
                            )
                        ),
                    )

                    new_expire = source["expire"]

                    with transaction.atomic():

                        locked = (
                            Subscription.objects
                            .select_for_update()
                            .select_related("client")
                            .get(pk=subscription.pk)
                        )

                        if not subscription_state_is_current(
                            subscription.pk,
                            local_expire,
                            synced_value,
                            check_paid_fulfillment=fulfillment_column_available,
                        ):
                            raise RuntimeError(
                                "Подписка или оплаченный заказ "
                                "изменились во время синхронизации. "
                                "Устаревшая запись отменена."
                            )

                        locked.expires_at = new_expire
                        locked.expires_synced_value = (
                            new_expire
                        )
                        locked.expires_synced_at = (
                            timezone.now()
                        )

                        if new_expire > timezone.now():
                            locked.status = (
                                Subscription.STATUS_ACTIVE
                            )
                            locked.client.is_active = True

                        locked.save(
                            update_fields=[
                                "expires_at",
                                "status",
                                "expires_synced_value",
                                "expires_synced_at",
                                "updated_at",
                            ]
                        )

                        c = locked.client
                        c.expires_at = new_expire

                        c.save(
                            update_fields=[
                                "expires_at",
                                "is_active",
                                "updated_at",
                            ]
                        )

                    # Выравниваем остальные устройства.
                    for item in remote_items:

                        if not same_dt(
                            item["expire"],
                            new_expire,
                        ):
                            if not subscription_state_is_current(
                                subscription.pk,
                                new_expire,
                                new_expire,
                                check_paid_fulfillment=fulfillment_column_available,
                            ):
                                raise RuntimeError(
                                    "Отменена отправка устаревшего "
                                    "срока в Remnawave: подписка "
                                    "или оплаченный заказ изменились."
                                )

                            rw.update_user_expire_at(
                                item["user_id"],
                                new_expire,
                            )

                    # Оплата могла поступить во время HTTP-запроса.
                    # В этом случае восстанавливаем актуальный срок
                    # на ВСЕХ устройствах данной подписки.
                    if not subscription_state_is_current(
                        subscription.pk,
                        new_expire,
                        new_expire,
                        check_paid_fulfillment=False,
                    ):
                        restored = False

                        for recovery_attempt in range(5):
                            current = Subscription.objects.get(
                                pk=subscription.pk
                            )
                            current_expire = current.expires_at
                            current_synced = current.expires_synced_value

                            if current_expire is None:
                                raise RuntimeError(
                                    "Невозможно восстановить "
                                    "пустой срок подписки."
                                )

                            for restore_item in remote_items:
                                rw.update_user_expire_at(
                                    restore_item["user_id"],
                                    current_expire,
                                )

                            if subscription_state_is_current(
                                subscription.pk,
                                current_expire,
                                current_synced,
                                check_paid_fulfillment=False,
                            ):
                                restored = True
                                break

                        if not restored:
                            raise RuntimeError(
                                "Срок продолжает изменяться. "
                                "Восстановление не завершено "
                                "после 5 попыток."
                            )

                        raise RuntimeError(
                            "Оплата или изменение подписки "
                            "во время HTTP-запроса. "
                            "Актуальный срок восстановлен "
                            "на всех устройствах Remnawave."
                        )

                    rw_to_django += 1

                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Client {client.id}: "
                            f"Remnawave -> Django: "
                            f"{new_expire} "
                            f"(device "
                            f"{source['device'].id}: "
                            f"{source['device'].name})"
                        )
                    )

                # -------------------------------------------------
                # 3. Изменили обе стороны
                # -------------------------------------------------

                elif local_changed and remote_changed_items:

                    source = max(
                        remote_changed_items,
                        key=lambda item: (
                            item["updated"]
                            or datetime.min.replace(
                                tzinfo=timezone.utc
                            )
                        ),
                    )

                    remote_time = source["updated"]
                    local_time = subscription.updated_at

                    # В конфликте действительно используем
                    # время последнего изменения.
                    if (
                        remote_time
                        and remote_time > local_time
                    ):
                        new_expire = source["expire"]

                        with transaction.atomic():

                            locked = (
                                Subscription.objects
                                .select_for_update()
                                .select_related("client")
                                .get(pk=subscription.pk)
                            )

                            if not subscription_state_is_current(
                                subscription.pk,
                                local_expire,
                                synced_value,
                                check_paid_fulfillment=fulfillment_column_available,
                            ):
                                raise RuntimeError(
                                    "Подписка или оплаченный заказ "
                                    "изменились во время синхронизации. "
                                    "Устаревшая запись отменена."
                                )

                            locked.expires_at = new_expire
                            locked.expires_synced_value = (
                                new_expire
                            )
                            locked.expires_synced_at = (
                                timezone.now()
                            )

                            if new_expire > timezone.now():
                                locked.status = (
                                    Subscription.STATUS_ACTIVE
                                )
                                locked.client.is_active = True

                            locked.save(
                                update_fields=[
                                    "expires_at",
                                    "status",
                                    "expires_synced_value",
                                    "expires_synced_at",
                                    "updated_at",
                                ]
                            )

                            c = locked.client
                            c.expires_at = new_expire

                            c.save(
                                update_fields=[
                                    "expires_at",
                                    "is_active",
                                    "updated_at",
                                ]
                            )

                        for item in remote_items:
                            if not same_dt(
                                item["expire"],
                                new_expire,
                            ):
                                if not subscription_state_is_current(
                                    subscription.pk,
                                    new_expire,
                                    new_expire,
                                    check_paid_fulfillment=fulfillment_column_available,
                                ):
                                    raise RuntimeError(
                                        "Отменена отправка устаревшего "
                                        "срока в Remnawave: подписка "
                                        "или оплаченный заказ изменились."
                                    )

                                rw.update_user_expire_at(
                                    item["user_id"],
                                    new_expire,
                                )

                        rw_to_django += 1

                        self.stdout.write(
                            self.style.WARNING(
                                f"Client {client.id}: "
                                f"CONFLICT -> Remnawave wins: "
                                f"{new_expire}"
                            )
                        )

                    else:
                        if local_expire is None:
                            raise RuntimeError(
                                "Конфликт: Django expires_at пустой."
                            )

                        for item in remote_items:
                            if not same_dt(
                                item["expire"],
                                local_expire,
                            ):
                                if not subscription_state_is_current(
                                    subscription.pk,
                                    local_expire,
                                    synced_value,
                                    check_paid_fulfillment=fulfillment_column_available,
                                ):
                                    raise RuntimeError(
                                        "Отменена отправка устаревшего "
                                        "срока в Remnawave: подписка "
                                        "или оплаченный заказ изменились."
                                    )

                                rw.update_user_expire_at(
                                    item["user_id"],
                                    local_expire,
                                )

                        with transaction.atomic():

                            locked = (
                                Subscription.objects
                                .select_for_update()
                                .select_related("client")
                                .get(pk=subscription.pk)
                            )

                            if not subscription_state_is_current(
                                subscription.pk,
                                local_expire,
                                synced_value,
                                check_paid_fulfillment=fulfillment_column_available,
                            ):
                                raise RuntimeError(
                                    "Подписка или оплаченный заказ "
                                    "изменились во время синхронизации. "
                                    "Устаревшая запись отменена."
                                )

                            locked.expires_synced_value = (
                                local_expire
                            )
                            locked.expires_synced_at = (
                                timezone.now()
                            )

                            locked.save(
                                update_fields=[
                                    "expires_synced_value",
                                    "expires_synced_at",
                                ]
                            )

                            c = locked.client

                            if not same_dt(
                                c.expires_at,
                                local_expire,
                            ):
                                c.expires_at = local_expire
                                c.save(
                                    update_fields=[
                                        "expires_at",
                                        "updated_at",
                                    ]
                                )

                        django_to_rw += 1

                        self.stdout.write(
                            self.style.WARNING(
                                f"Client {client.id}: "
                                f"CONFLICT -> Django wins: "
                                f"{local_expire}"
                            )
                        )

                # -------------------------------------------------
                # 4. Ничего не менялось
                # -------------------------------------------------

                else:
                    # Даже без изменений проверяем, нет ли
                    # рассинхронизации между устройствами.
                    out_of_sync = [
                        item
                        for item in remote_items
                        if not same_dt(
                            item["expire"],
                            local_expire,
                        )
                    ]

                    if out_of_sync:
                        for item in out_of_sync:
                            if not subscription_state_is_current(
                                subscription.pk,
                                local_expire,
                                synced_value,
                                check_paid_fulfillment=fulfillment_column_available,
                            ):
                                raise RuntimeError(
                                    "Отменена отправка устаревшего "
                                    "срока в Remnawave: подписка "
                                    "или оплаченный заказ изменились."
                                )

                            rw.update_user_expire_at(
                                item["user_id"],
                                local_expire,
                            )

                    unchanged += 1

                processed += 1

            except Exception as exc:

                errors += 1

                self.stderr.write(
                    self.style.ERROR(
                        f"Client {client.id}: {exc}"
                    )
                )

        self.stdout.write("")
        self.stdout.write(
            f"Processed: {processed}; "
            f"Django->RW: {django_to_rw}; "
            f"RW->Django: {rw_to_django}; "
            f"unchanged: {unchanged}; "
            f"errors: {errors}"
        )
