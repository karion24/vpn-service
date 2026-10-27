"""Expiry reminders. Delivery does not change subscription dates or payment state."""
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from django.utils.module_loading import import_string

from .models import CustomerProfile, Subscription, SubscriptionReminder


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
        raise ValueError("Selected contact is empty")
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
        if send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [recipient]) != 1:
            raise RuntimeError("Email backend did not accept the message")
        return
    backend = settings.KARION_NOTIFICATION_BACKENDS.get(profile.primary_channel)
    if not backend:
        raise RuntimeError("Notification backend is not configured")
    # The adapter resolves the stored contact using the existing gateway contract.
    import_string(backend)(recipient=recipient, message=message, idempotency_key=key)


def send_subscription_reminder(subscription_id, now=None):
    now = now or timezone.now()
    # Re-read the date immediately before delivery; never use an order or another subscription.
    subscription = upcoming_subscriptions(now).filter(pk=subscription_id).first()
    if subscription is None:
        return "skipped"
    try:
        profile = subscription.client.owner.customer_profile
    except CustomerProfile.DoesNotExist:
        return "missing_profile"
    reminder, _ = SubscriptionReminder.objects.get_or_create(
        subscription=subscription, expires_at=subscription.expires_at,
        defaults={"channel": profile.primary_channel},
    )
    if reminder.sent_at:
        return "already_sent"
    reminder.channel = profile.primary_channel
    reminder.attempts += 1
    try:
        deliver_reminder(profile, subscription, f"karion-expiry-{reminder.pk}")
    except Exception as exc:
        # Do not persist gateway bodies, contact details, or credentials in error logs.
        reminder.last_error = type(exc).__name__
        reminder.save(update_fields=["channel", "attempts", "last_error"])
        return "failed"
    reminder.sent_at = timezone.now()
    reminder.last_error = ""
    reminder.save(update_fields=["channel", "attempts", "sent_at", "last_error"])
    return "sent"
