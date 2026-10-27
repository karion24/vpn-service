from datetime import timedelta

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class ReminderMigrationTests(TransactionTestCase):
    def migrate(self, name):
        executor = MigrationExecutor(connection)
        target = [("portal", name)]
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_contacts_dates_and_delivery_history_survive(self):
        apps = self.migrate("0025_order_fulfillment_status")
        user = apps.get_model("auth", "User").objects.create(username="migration", email="existing@example.com")
        profile = apps.get_model("portal", "CustomerProfile").objects.create(user_id=user.pk, telegram="@existing", phone="123")
        plan = apps.get_model("portal", "Plan").objects.create(code="migration", name="Original")
        expiry = timezone.now() + timedelta(days=10)
        client = apps.get_model("portal", "Client").objects.create(username="migration", owner_id=user.pk, expires_at=expiry)
        sub = apps.get_model("portal", "Subscription").objects.create(client_id=client.pk, plan_id=plan.pk, expires_at=expiry, expires_synced_value=expiry, status="active")
        apps = self.migrate("0026_customer_notification_channels_and_reminders")
        p = apps.get_model("portal", "CustomerProfile").objects.get(pk=profile.pk)
        self.assertEqual((p.telegram, p.phone, p.primary_channel, p.max_contact), ("@existing", "123", "email", ""))
        reminder = apps.get_model("portal", "SubscriptionReminder")
        sent = reminder.objects.create(subscription_id=sub.pk, expires_at=expiry, channel="email", sent_at=timezone.now(), attempts=1)
        unknown = reminder.objects.create(subscription_id=sub.pk, expires_at=expiry + timedelta(days=1), channel="email", attempts=1)
        pending = reminder.objects.create(subscription_id=sub.pk, expires_at=expiry + timedelta(days=2), channel="email")
        apps = self.migrate("0027_reminder_delivery_state")
        reminder = apps.get_model("portal", "SubscriptionReminder")
        self.assertEqual(reminder.objects.get(pk=sent.pk).status, "sent")
        self.assertEqual(reminder.objects.get(pk=unknown.pk).status, "uncertain")
        self.assertEqual(reminder.objects.get(pk=pending.pk).status, "pending")
        self.assertEqual(apps.get_model("portal", "Client").objects.get(pk=client.pk).expires_at, expiry)
        current = apps.get_model("portal", "Subscription").objects.get(pk=sub.pk)
        self.assertEqual((current.expires_at, current.expires_synced_value, current.status), (expiry, expiry, "active"))
        self.assertEqual(apps.get_model("auth", "User").objects.get(pk=user.pk).email, "existing@example.com")
