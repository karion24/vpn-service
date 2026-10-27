from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from portal.admin import CustomerProfileAdminForm
from portal.models import Client, CustomerProfile, Plan, Subscription, SubscriptionReminder
from portal.subscription_reminders import send_subscription_reminder


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SubscriptionReminderTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="reminder", email="client@example.com")
        self.profile = CustomerProfile.objects.create(user=self.user)
        self.plan = Plan.objects.create(name="Стандарт", code="reminder", duration_days=30)
        self.now = timezone.now()
        self.subscription = self.make_subscription("first", self.now + timedelta(hours=23))

    def make_subscription(self, username, expiry):
        client = Client.objects.create(username=username, owner=self.user, expires_at=expiry)
        return Subscription.objects.create(client=client, plan=self.plan, status="active", expires_at=expiry)

    def form(self, **changes):
        data = {"email": "client@example.com", "phone": "", "telegram": "", "max_contact": "", "primary_channel": "email"}
        data.update(changes)
        return CustomerProfileAdminForm(data=data, instance=CustomerProfile.objects.get(pk=self.profile.pk))

    def test_admin_channel_validation(self):
        self.assertEqual(self.profile.primary_channel, "email")
        for channel, field in [("telegram", "telegram"), ("max", "max_contact"), ("email", "email")]:
            with self.subTest(channel=channel):
                form = self.form(primary_channel=channel, **{field: "   "})
                self.assertFalse(form.is_valid())
                self.assertIn("primary_channel", form.errors)
                form = self.form(primary_channel=channel, **{field: "client@example.com" if channel == "email" else "12345"})
                self.assertTrue(form.is_valid(), form.errors)

    def test_admin_uses_new_email_and_preserves_login(self):
        from portal.admin import CustomerProfileAdmin
        from django.contrib.admin.sites import AdminSite
        form = self.form(email="changed@example.com")
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        CustomerProfileAdmin(CustomerProfile, AdminSite()).save_model(None, obj, form, True)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "changed@example.com")
        self.assertEqual(self.user.username, "reminder")

    def test_email_once_and_uses_subscription_date(self):
        self.subscription.client.expires_at = self.now + timedelta(days=90)
        self.subscription.client.save()
        self.assertEqual(send_subscription_reminder(self.subscription.pk), "sent")
        self.assertEqual(send_subscription_reminder(self.subscription.pk), "already_sent")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        expiry = timezone.localtime(self.subscription.expires_at).strftime("%d.%m.%Y в %H:%M %Z")
        self.assertIn(expiry, mail.outbox[0].body)
        self.assertIn(f"№{self.subscription.client_id}", mail.outbox[0].body)

    def test_window_and_independent_subscriptions(self):
        other = self.make_subscription("second", self.now + timedelta(hours=12))
        for name, expiry in [("expired", self.now), ("later", self.now + timedelta(hours=24, seconds=1))]:
            sub = self.make_subscription(name, expiry)
            self.assertEqual(send_subscription_reminder(sub.pk, self.now), "skipped")
        self.assertEqual(send_subscription_reminder(self.subscription.pk, self.now), "sent")
        self.assertEqual(send_subscription_reminder(other.pk, self.now), "sent")
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(SubscriptionReminder.objects.count(), 2)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.expires_at, self.now + timedelta(hours=23))

    def test_renewal_defers_notification_until_new_expiry_window(self):
        send_subscription_reminder(self.subscription.pk, self.now)
        self.subscription.expires_at += timedelta(days=30)
        self.subscription.save()
        self.assertEqual(send_subscription_reminder(self.subscription.pk, self.now), "skipped")
        self.assertEqual(send_subscription_reminder(self.subscription.pk, self.now + timedelta(days=30)), "sent")
        self.assertEqual(SubscriptionReminder.objects.count(), 2)

    def test_inactive_and_cancelled_are_excluded(self):
        self.subscription.status = "cancelled"
        self.subscription.save()
        self.assertEqual(send_subscription_reminder(self.subscription.pk), "skipped")
        self.subscription.status = "active"
        self.subscription.save()
        self.user.is_active = False
        self.user.save()
        self.assertEqual(send_subscription_reminder(self.subscription.pk), "skipped")

    def test_missing_gateway_is_failure_not_success(self):
        self.profile.primary_channel = "telegram"
        self.profile.telegram = "123"
        self.profile.save()
        with override_settings(KARION_NOTIFICATION_BACKENDS={}):
            self.assertEqual(send_subscription_reminder(self.subscription.pk), "failed")
        reminder = SubscriptionReminder.objects.get()
        self.assertIsNone(reminder.sent_at)
        self.assertEqual(reminder.last_error, "NotificationDeliveryError")
        with override_settings(KARION_NOTIFICATION_BACKENDS={"telegram": "example.send"}):
            with patch("portal.subscription_reminders.import_string") as loader:
                self.assertEqual(send_subscription_reminder(self.subscription.pk), "sent")
                kwargs = loader.return_value.call_args.kwargs
                self.assertEqual(kwargs["recipient"], "123")
                self.assertEqual(kwargs["idempotency_key"], f"karion-expiry-{reminder.pk}")

    def test_max_adapter(self):
        self.profile.primary_channel = "max"
        self.profile.max_contact = "456"
        self.profile.save()
        with override_settings(KARION_NOTIFICATION_BACKENDS={"max": "example.send"}):
            with patch("portal.subscription_reminders.import_string") as loader:
                self.assertEqual(send_subscription_reminder(self.subscription.pk), "sent")
                self.assertEqual(loader.return_value.call_args.kwargs["recipient"], "456")

    def test_email_failure_is_retried(self):
        with patch("portal.subscription_reminders.send_mail", side_effect=__import__("smtplib").SMTPAuthenticationError(535, b"test rejection")):
            self.assertEqual(send_subscription_reminder(self.subscription.pk), "failed")
        self.assertIsNone(SubscriptionReminder.objects.get().sent_at)
        self.assertEqual(send_subscription_reminder(self.subscription.pk), "sent")
        self.assertEqual(SubscriptionReminder.objects.get().attempts, 2)

    def test_selected_contact_cannot_be_cleared(self):
        self.profile.primary_channel = "telegram"
        self.profile.telegram = "123"
        self.profile.save()
        form = self.form(primary_channel="telegram", telegram="")
        self.assertFalse(form.is_valid())
        self.assertIn("primary_channel", form.errors)

    def test_admin_rejects_usernames_for_bot_delivery(self):
        for channel, field in [("telegram", "telegram"), ("max", "max_contact")]:
            form = self.form(primary_channel=channel, **{field: "@client"})
            self.assertFalse(form.is_valid())
            self.assertIn("primary_channel", form.errors)

    def test_exact_24_hour_boundary(self):
        self.subscription.expires_at = self.now + timedelta(hours=24)
        self.subscription.save()
        self.assertEqual(send_subscription_reminder(self.subscription.pk, self.now), "sent")

    def test_overlapping_command_is_rejected(self):
        import fcntl
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = directory + "/reminders.lock"
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with override_settings(KARION_REMINDER_LOCK_FILE=path):
                    with self.assertRaises(CommandError):
                        call_command("send_subscription_reminders", "--apply", stdout=StringIO())
            finally:
                os.close(fd)
        self.assertFalse(SubscriptionReminder.objects.exists())

    def test_command_dry_run_and_apply(self):
        call_command("send_subscription_reminders", stdout=StringIO())
        self.assertFalse(SubscriptionReminder.objects.exists())
        self.assertEqual(len(mail.outbox), 0)
        call_command("send_subscription_reminders", "--apply", stdout=StringIO())
        call_command("send_subscription_reminders", "--apply", stdout=StringIO())
        self.assertEqual(len(mail.outbox), 1)

    def test_command_reports_failure_and_continues(self):
        other = self.make_subscription("other", self.now + timedelta(hours=10))
        with patch("portal.management.commands.send_subscription_reminders.send_subscription_reminder", side_effect=[RuntimeError(), "sent"]) as sender:
            with self.assertRaises(CommandError):
                call_command("send_subscription_reminders", "--apply", stdout=StringIO(), stderr=StringIO())
            self.assertEqual(sender.call_count, 2)

    def test_uncertain_smtp_result_is_not_retried(self):
        import smtplib
        with patch("portal.subscription_reminders.send_mail", side_effect=smtplib.SMTPServerDisconnected("unknown")) as sender:
            self.assertEqual(send_subscription_reminder(self.subscription.pk), "needs_review")
            self.assertEqual(send_subscription_reminder(self.subscription.pk), "needs_review")
            sender.assert_called_once()
        self.assertEqual(SubscriptionReminder.objects.get().status, "uncertain")

    def test_interrupted_process_is_not_retried(self):
        SubscriptionReminder.objects.create(subscription=self.subscription, expires_at=self.subscription.expires_at, channel="email", status="sending", attempts=1)
        with patch("portal.subscription_reminders.deliver_reminder") as sender:
            self.assertEqual(send_subscription_reminder(self.subscription.pk), "needs_review")
            sender.assert_not_called()

    def test_renewal_after_claim_cancels_stale_message(self):
        from portal.subscription_reminders import _claim
        new_expiry = self.subscription.expires_at + timedelta(days=30)
        def claim_then_renew(*args):
            result = _claim(*args)
            Subscription.objects.filter(pk=self.subscription.pk).update(expires_at=new_expiry)
            return result
        with patch("portal.subscription_reminders._claim", side_effect=claim_then_renew):
            with patch("portal.subscription_reminders.deliver_reminder") as sender:
                self.assertEqual(send_subscription_reminder(self.subscription.pk), "skipped")
                sender.assert_not_called()
        self.assertEqual(SubscriptionReminder.objects.get().status, "skipped")

    def test_renewal_during_delivery_keeps_old_attempt_and_new_expiry(self):
        old_expiry = self.subscription.expires_at
        new_expiry = old_expiry + timedelta(days=30)
        def deliver(*args):
            Subscription.objects.filter(pk=self.subscription.pk).update(expires_at=new_expiry)
        with patch("portal.subscription_reminders.deliver_reminder", side_effect=deliver) as sender:
            self.assertEqual(send_subscription_reminder(self.subscription.pk), "sent_date_changed")
            self.assertEqual(send_subscription_reminder(self.subscription.pk), "skipped")
            sender.assert_called_once()
        reminder = SubscriptionReminder.objects.get()
        self.assertEqual(reminder.expires_at, old_expiry)
        self.assertEqual(reminder.status, "sent")
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.expires_at, new_expiry)

    def test_admin_preserves_existing_unselected_contacts(self):
        self.profile.telegram = "@existing_contact"
        self.profile.max_contact = "old-max-reference"
        self.profile.save()
        form = self.form(telegram="@existing_contact", max_contact="old-max-reference")
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.telegram, "@existing_contact")
        self.assertEqual(self.profile.max_contact, "old-max-reference")

    def test_invalid_admin_form_does_not_save_email(self):
        form = self.form(email="changed@example.com", primary_channel="telegram", telegram="@invalid")
        self.assertFalse(form.is_valid())
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "client@example.com")

    def test_admin_email_is_rolled_back_if_profile_save_fails(self):
        from portal.admin import CustomerProfileAdmin
        from django.contrib.admin import ModelAdmin, AdminSite
        form = self.form(email="changed@example.com")
        self.assertTrue(form.is_valid(), form.errors)
        with patch.object(ModelAdmin, "save_model", side_effect=RuntimeError("db failure")):
            with self.assertRaises(RuntimeError):
                CustomerProfileAdmin(CustomerProfile, AdminSite()).save_model(None, form.save(commit=False), form, True)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "client@example.com")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="smtp.yandex.ru", EMAIL_PORT=465, EMAIL_USE_SSL=True, EMAIL_USE_TLS=False, EMAIL_HOST_USER="test@example.com", EMAIL_HOST_PASSWORD="fake-test-password")
    @patch("django.core.mail.backends.smtp.smtplib.SMTP")
    @patch("django.core.mail.backends.smtp.smtplib.SMTP_SSL")
    def test_smtp_uses_ssl_465_and_authentication(self, ssl, plain):
        ssl.return_value.sendmail.return_value = {}
        self.assertEqual(send_subscription_reminder(self.subscription.pk), "sent")
        self.assertEqual(ssl.call_args.args[:2], ("smtp.yandex.ru", 465))
        ssl.return_value.login.assert_called_once_with("test@example.com", "fake-test-password")
        ssl.return_value.starttls.assert_not_called()
        plain.assert_not_called()

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST_PASSWORD="")
    @patch("portal.subscription_reminders.send_mail")
    def test_missing_smtp_password_fails_before_sending(self, sender):
        self.assertEqual(send_subscription_reminder(self.subscription.pk), "failed")
        sender.assert_not_called()

    def test_smtp_zero_result_is_failed_and_no_fallback(self):
        with patch("portal.subscription_reminders.send_mail", return_value=0):
            with patch("portal.subscription_reminders.import_string") as messenger:
                self.assertEqual(send_subscription_reminder(self.subscription.pk), "failed")
                messenger.assert_not_called()
