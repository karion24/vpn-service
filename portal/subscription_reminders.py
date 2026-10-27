"""Expiry reminders. Delivery does not change subscription dates or payment state."""
from datetime import timedelta
import smtplib
import time

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import OperationalError
from django.db.models import F
from django.core.mail import send_mail
from django.utils import timezone
from django.utils.module_loading import import_string

from .models import CustomerProfile, Subscription, SubscriptionReminder
from .notification_backends import NotificationDeliveryError, NotificationDeliveryUnknown, recipient_id


def upcoming_subscriptions(now):
    return Subscription.objects.filter(
        status=Subscription.STATUS_ACTIVE,
        client__is_active=True,
        client__owner__is_active=True,
        expires_at__gt=now,
        expires_at__lte=now + timedelta(hours=24),
    ).select_related("client__owner__customer_profile", "plan")


def deliver_reminder(profile, subscription, key):
    recipient = profile.notification_recipient()
    if not recipient:
        raise NotificationDeliveryError("Selected contact is empty")
    expiry = timezone.localtime(subscription.expires_at).strftime("%d.%m.%Y в %H:%M %Z")
    subject = "Karion: срок VPN-подписки заканчивается"
    message = (
        f'Здравствуйте! Срок вашей VPN-подписки «{subscription.client.name}» '
        f'(№{subscription.client_id}, тариф «{subscription.plan.name}») '
        f'заканчивается {expiry}. До окончания осталось не более суток.\n\n'
        'Продлите подписку в личном кабинете, чтобы сохранить доступ, '
        'или выберите подходящий тариф для новой подписки.\n'
        f'{settings.KARION_CABINET_URL}\n\nКоманда Karion'
    )
    if profile.primary_channel == CustomerProfile.CHANNEL_EMAIL:
        try:
            validate_email(recipient)
        except ValidationError:
            raise NotificationDeliveryError("Invalid recipient email") from None
        if settings.EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend":
            if settings.EMAIL_USE_SSL and settings.EMAIL_USE_TLS:
                raise NotificationDeliveryError("SSL and STARTTLS cannot both be enabled")
            if not settings.EMAIL_HOST_PASSWORD or not settings.EMAIL_HOST_USER:
                raise NotificationDeliveryError("SMTP authentication is not configured")
        try:
            accepted = send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [recipient], fail_silently=False)
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused,
                smtplib.SMTPSenderRefused, smtplib.SMTPDataError, smtplib.SMTPConnectError):
            raise NotificationDeliveryError("SMTP rejected the message") from None
        if accepted != 1:
            raise NotificationDeliveryError("Email backend did not accept the message")
        return
    backend = settings.KARION_NOTIFICATION_BACKENDS.get(profile.primary_channel)
    if not backend:
        raise NotificationDeliveryError("Notification backend is not configured")
    try:
        recipient_id(recipient)
        sender = import_string(backend)
    except (ValueError, ImportError, AttributeError):
        raise NotificationDeliveryError("Invalid messenger ID or backend") from None
    sender(recipient=recipient, message=message, idempotency_key=key)


def _claim(subscription, channel):
    # A committed conditional UPDATE protects even callers outside the command,
    # including independent hosts sharing the same database. No HTTP in a transaction.
    for attempt in range(5):
        try:
            reminder, _ = SubscriptionReminder.objects.get_or_create(
                subscription=subscription, expires_at=subscription.expires_at,
                defaults={"channel": channel},
            )
            claimed = SubscriptionReminder.objects.filter(
                pk=reminder.pk, sent_at__isnull=True,
                status__in=["pending", "failed", "skipped"],
            ).update(status="sending", channel=channel, attempts=F("attempts") + 1)
            if claimed:
                reminder.refresh_from_db()
                return reminder, None
            reminder.refresh_from_db()
            return None, "already_sent" if reminder.sent_at else "needs_review"
        except OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def send_subscription_reminder(subscription_id, now=None):
    subscription = upcoming_subscriptions(now or timezone.now()).filter(pk=subscription_id).first()
    if subscription is None:
        return "skipped"
    try:
        profile = subscription.client.owner.customer_profile
    except CustomerProfile.DoesNotExist:
        return "missing_profile"
    reminder, outcome = _claim(subscription, profile.primary_channel)
    if reminder is None:
        return outcome
    # Recheck after acquiring the database claim. If a renewal won the race,
    # do not send a message constructed from the stale expiry date.
    current = upcoming_subscriptions(now or timezone.now()).filter(
        pk=subscription_id, expires_at=reminder.expires_at,
        client__owner_id=profile.user_id,
    ).first()
    if current is None:
        reminder.status = "skipped"
        reminder.save(update_fields=["status"])
        return "skipped"
    try:
        profile.refresh_from_db()
        reminder.channel = profile.primary_channel
        reminder.save(update_fields=["channel"])
        deliver_reminder(profile, current, f"karion-expiry-{reminder.pk}")
    except NotificationDeliveryUnknown as exc:
        reminder.status = "uncertain"
        reminder.last_error = type(exc).__name__
    except NotificationDeliveryError as exc:
        reminder.status = "failed"
        reminder.last_error = type(exc).__name__
    except Exception as exc:
        # Includes timeouts/disconnects: the provider might have accepted it.
        reminder.status = "uncertain"
        reminder.last_error = type(exc).__name__
    else:
        reminder.status = "sent"
        reminder.sent_at = timezone.now()
        reminder.last_error = ""
    reminder.save(update_fields=["status", "sent_at", "last_error"])
    if reminder.status == "sent":
        if not Subscription.objects.filter(pk=subscription_id, expires_at=reminder.expires_at).exists():
            return "sent_date_changed"
        return "sent"
    return "needs_review" if reminder.status == "uncertain" else "failed"
