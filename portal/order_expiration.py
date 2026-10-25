from django.utils import timezone

from .models import Order, Payment


def can_expire_order(order, now=None):
    """
    Проверяет возможность отмены просроченного заказа.

    Никаких изменений в базе не выполняет.
    """
    if now is None:
        now = timezone.now()

    if order.status != Order.STATUS_PENDING:
        return False

    if order.payment_expires_at is None:
        return False

    if order.payment_expires_at > now:
        return False

    payments = list(order.payments.all())

    safe_terminal_statuses = {
        Payment.STATUS_FAILED,
        Payment.STATUS_CANCELLED,
    }

    for payment in payments:
        if payment.status not in safe_terminal_statuses:
            return False

    return True


def can_cancel_order(order):
    """
    Проверяет безопасность ручной отмены заказа.

    Не требует истечения 10 минут.
    Не изменяет данные.
    """
    if order.status != Order.STATUS_PENDING:
        return False

    safe_terminal_statuses = {
        Payment.STATUS_FAILED,
        Payment.STATUS_CANCELLED,
    }

    return all(
        payment.status in safe_terminal_statuses
        for payment in order.payments.all()
    )
