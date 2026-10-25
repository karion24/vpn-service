from datetime import datetime

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from portal.models import Subscription, Device
from portal.remnawave import RemnawaveClient


def parse_rw_datetime(value):
    if not value:
        return None

    dt = parse_datetime(value)

    if dt is None:
        try:
            dt = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except Exception:
            return None

    if timezone.is_naive(dt):
        dt = timezone.make_aware(
            dt,
            timezone.get_current_timezone(),
        )

    return dt


class Command(BaseCommand):
    help = (
        "Первичный импорт срока подписки "
        "Remnawave -> Django"
    )

    def handle(self, *args, **options):

        subscriptions = (
            Subscription.objects
            .select_related(
                "client",
                "client__remnawave_server",
            )
            .all()
        )

        processed = 0
        skipped = 0
        errors = 0

        for subscription in subscriptions:

            client = subscription.client

            if not client.remnawave_server:
                skipped += 1
                continue

            devices = (
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
            )

            if not devices.exists():
                skipped += 1
                continue

            rw = RemnawaveClient(
                client.remnawave_server
            )

            candidates = []

            try:
                for device in devices:

                    remote = rw.get_user_by_username(
                        device.remnawave_username
                    )

                    expire_at = parse_rw_datetime(
                        remote.get("expireAt")
                    )

                    updated_at = parse_rw_datetime(
                        remote.get("updatedAt")
                    )

                    if not expire_at:
                        continue

                    if not updated_at:
                        updated_at = expire_at

                    candidates.append(
                        (
                            updated_at,
                            expire_at,
                            device,
                        )
                    )

                if not candidates:
                    skipped += 1
                    continue

                # Самое свежее изменение среди устройств
                # этой подписки.
                candidates.sort(
                    key=lambda item: item[0],
                    reverse=True,
                )

                remote_updated_at, expire_at, source_device = (
                    candidates[0]
                )

                with transaction.atomic():

                    subscription_locked = (
                        Subscription.objects
                        .select_for_update()
                        .select_related("client")
                        .get(pk=subscription.pk)
                    )

                    client_locked = (
                        subscription_locked.client
                    )

                    old_expire = (
                        subscription_locked.expires_at
                    )

                    subscription_locked.expires_at = (
                        expire_at
                    )

                    # Если срок будущий, подписка должна
                    # оставаться активной.
                    if expire_at > timezone.now():
                        subscription_locked.status = (
                            Subscription.STATUS_ACTIVE
                        )
                        client_locked.is_active = True

                    subscription_locked.expires_synced_at = (
                        timezone.now()
                    )

                    subscription_locked.save(
                        update_fields=[
                            "expires_at",
                            "status",
                            "expires_synced_at",
                            "updated_at",
                        ]
                    )

                    client_locked.expires_at = expire_at

                    client_locked.save(
                        update_fields=[
                            "expires_at",
                            "is_active",
                            "updated_at",
                        ]
                    )

                processed += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Client {client.id}: "
                        f"{old_expire} -> {expire_at} "
                        f"(device {source_device.id}: "
                        f"{source_device.name})"
                    )
                )

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
            f"skipped: {skipped}; "
            f"errors: {errors}"
        )
