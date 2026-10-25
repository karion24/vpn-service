"""
Защита синхронизации подписок от устаревших данных.

Функции не выполняют сетевых запросов и не изменяют БД.
"""

from portal.models import Subscription
from portal.payment_fulfillment import (
    has_pending_paid_fulfillment,
)


def subscription_state_is_current(
    subscription_id,
    expected_expires_at,
    expected_synced_value,
    *,
    check_paid_fulfillment=True,
):
    """
    Проверяет, что данные подписки не изменились
    с момента чтения исходного состояния.

    Сравнение точное: даже небольшое изменение
    даты считается изменением состояния.
    """

    current = (
        Subscription.objects
        .filter(pk=subscription_id)
        .values(
            "client_id",
            "expires_at",
            "expires_synced_value",
        )
        .first()
    )

    if current is None:
        return False

    if current["expires_at"] != expected_expires_at:
        return False

    if current["expires_synced_value"] != expected_synced_value:
        return False

    if check_paid_fulfillment:
        if has_pending_paid_fulfillment(
            current["client_id"]
        ):
            return False

    return True
