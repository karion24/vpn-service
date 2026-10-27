from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone

from portal.models import Client, CustomerProfile, Plan, Subscription, SubscriptionReminder
from portal.subscription_reminders import send_subscription_reminder


class ReminderConcurrencyTests(TransactionTestCase):
    def test_database_claim_protects_callers_without_file_lock(self):
        user = get_user_model().objects.create_user(username="parallel", email="test@example.com")
        CustomerProfile.objects.create(user=user)
        plan = Plan.objects.create(code="parallel", name="Parallel")
        client = Client.objects.create(username="parallel", owner=user)
        sub = Subscription.objects.create(client=client, plan=plan, status="active", expires_at=timezone.now() + timedelta(hours=12))
        entered, release = Event(), Event()
        def deliver(*args):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test did not release sender")
        def worker():
            close_old_connections()
            try:
                return send_subscription_reminder(sub.pk)
            finally:
                close_old_connections()
        with patch("portal.subscription_reminders.deliver_reminder", side_effect=deliver) as sender:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(worker)
                try:
                    self.assertTrue(entered.wait(10))
                    self.assertEqual(send_subscription_reminder(sub.pk), "needs_review")
                finally:
                    release.set()
                self.assertEqual(future.result(timeout=10), "sent")
            sender.assert_called_once()
        self.assertEqual(SubscriptionReminder.objects.get().attempts, 1)
