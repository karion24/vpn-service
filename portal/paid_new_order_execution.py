"""Безопасная активация новой VPN-подписки после оплаты."""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from portal.models import Client, Order, Subscription


class PaidNewOrderActivationError(Exception):
    pass


@transaction.atomic
def activate_paid_new_order(order_id):
    order = (
        Order.objects
        .select_for_update()
        .select_related("user", "plan")
        .get(pk=order_id)
    )

    if order.order_type != Order.TYPE_NEW:
        raise PaidNewOrderActivationError(
            "Заказ не является покупкой новой подписки."
        )

    if order.status != Order.STATUS_PAID:
        raise PaidNewOrderActivationError(
            "Нельзя активировать неоплаченный заказ."
        )

    if order.fulfillment_status == Order.FULFILLMENT_COMPLETED:
        if not order.client_id:
            raise PaidNewOrderActivationError(
                "Заказ исполнен, но клиент не привязан."
            )
        if not Subscription.objects.filter(
            client_id=order.client_id
        ).exists():
            raise PaidNewOrderActivationError(
                "Заказ исполнен, но подписка отсутствует."
            )
        return order

    if (
        order.fulfillment_status
        != Order.FULFILLMENT_ACTIVATION_PENDING
    ):
        raise PaidNewOrderActivationError(
            "Заказ находится в неподдерживаемом "
            "статусе исполнения."
        )

    if order.duration_days < 1 or order.device_limit < 1:
        raise PaidNewOrderActivationError(
            "Некорректные параметры подписки."
        )

    now = timezone.now()
    username = f"karion-{order.user_id}-{order.pk}"

    if order.client_id:
        client = (
            Client.objects
            .select_for_update()
            .get(pk=order.client_id)
        )

        if (
            client.owner_id != order.user_id
            or client.username != username
        ):
            raise PaidNewOrderActivationError(
                "Привязанный клиент не соответствует заказу."
            )

        if not Subscription.objects.filter(
            client=client
        ).exists():
            raise PaidNewOrderActivationError(
                "У привязанного клиента отсутствует подписка."
            )

    else:
        if Client.objects.filter(username=username).exists():
            raise PaidNewOrderActivationError(
                "Клиент с именем заказа уже существует, "
                "но не привязан к нему. "
                "Требуется ручная проверка."
            )

        expires_at = now + timedelta(
            days=order.duration_days
        )

        client = Client.objects.create(
            username=username,
            name=order.plan.name,
            owner=order.user,
            is_active=True,
            expires_at=expires_at,
        )

        Subscription.objects.create(
            client=client,
            plan=order.plan,
            status=Subscription.STATUS_ACTIVE,
            starts_at=now,
            expires_at=expires_at,
            auto_renew=False,
            price=order.amount,
        )

        order.client = client

    order.fulfillment_status = Order.FULFILLMENT_COMPLETED
    order.save(
        update_fields=[
            "client",
            "fulfillment_status",
            "updated_at",
        ]
    )

    return order
