"""Run periodically; a host-wide lock prevents overlapping local schedulers."""
import fcntl
import os
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from portal.subscription_reminders import upcoming_subscriptions, send_subscription_reminder


class Command(BaseCommand):
    help = "Напоминания о подписках, истекающих в ближайшие 24 часа (без --apply только просмотр)"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        fd = os.open(settings.KARION_REMINDER_LOCK_FILE, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CommandError("Другой процесс уже отправляет напоминания.")
            now = timezone.now()
            ids = list(upcoming_subscriptions(now).values_list("pk", flat=True))
            if not options["apply"]:
                self.stdout.write(f"DRY-RUN: подписок в ближайшие 24 часа: {len(ids)}; отправки нет.")
                return
            counts = Counter()
            for subscription_id in ids:
                try:
                    result = send_subscription_reminder(subscription_id)
                except Exception as exc:
                    self.stderr.write(f"Subscription #{subscription_id}: {type(exc).__name__}")
                    result = "failed"
                counts[result] += 1
                self.stdout.write(f"Subscription #{subscription_id}: {result}")
            self.stdout.write(str(dict(counts)))
            if counts["failed"] or counts["missing_profile"] or counts["needs_review"]:
                raise CommandError("Часть напоминаний не отправлена; проверьте контакты и настройки каналов.")
        finally:
            os.close(fd)
