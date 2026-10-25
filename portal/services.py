from django.utils import timezone

from .remnawave import (
    RemnawaveClient,
    RemnawaveAPIError,
)


def _get_remnawave_client(client):
    """
    Возвращает API-клиент Remnawave, закреплённого
    за данным VPN-клиентом.

    Для старых клиентов без remnawave_server сервер
    назначается автоматически:
    - только активный;
    - только принимающий новых клиентов;
    - с наименьшим priority.
    После выбора сервер сохраняется у Client.
    """

    server = client.remnawave_server

    if server is None:
        from .models import RemnawaveServer

        server = (
            RemnawaveServer.objects
            .filter(
                is_active=True,
                accept_new_clients=True,
            )
            .order_by(
                "priority",
                "pk",
            )
            .first()
        )

        if server is None:
            raise RemnawaveAPIError(
                "Нет активного сервера Remnawave, "
                "принимающего новых клиентов."
            )

        # Закрепляем выбранную панель за клиентом.
        # Все последующие устройства этого клиента
        # будут работать через ту же Remnawave.
        client.remnawave_server = server
        client.save(
            update_fields=[
                "remnawave_server",
                "updated_at",
            ]
        )

    if not server.is_active:
        raise RemnawaveAPIError(
            f"Сервер Remnawave «{server.name}» отключён."
        )

    return RemnawaveClient(server)


class ClientProvisionError(Exception):
    pass


class ClientSyncError(Exception):
    pass


def provision_client(client):
    """
    Создаёт пользователя Remnawave для существующего Client
    и сохраняет идентификаторы в Django.
    """

    if client.remnawave_uuid:
        return client

    if not client.expires_at:
        raise ClientProvisionError(
            "Для клиента не указан expires_at"
        )

    if client.expires_at <= timezone.now():
        raise ClientProvisionError(
            "Нельзя создать пользователя с истёкшим сроком подписки"
        )

    try:
        rw = _get_remnawave_client(client)

        remote_user = rw.create_user(
            username=client.username,
            expire_at=client.expires_at,
        )
    except Exception as exc:
        raise ClientProvisionError(
            f"Не удалось создать пользователя Remnawave: {exc}"
        ) from exc

    remnawave_uuid = remote_user.get("vlessUuid")
    remnawave_short_uuid = remote_user.get("shortUuid")

    if not remnawave_uuid or not remnawave_short_uuid:
        raise ClientProvisionError(
            "Remnawave не вернул vlessUuid или shortUuid"
        )

    client.remnawave_uuid = remnawave_uuid
    client.remnawave_short_uuid = remnawave_short_uuid

    client.save(
        update_fields=[
            "remnawave_uuid",
            "remnawave_short_uuid",
            "updated_at",
        ]
    )

    return client


def sync_client_to_remnawave(client):
    """
    Синхронизирует Client -> Remnawave.

    Логика:

    1. is_active=False:
       пользователь отключается в Remnawave.

    2. expires_at отсутствует:
       пользователь отключается.

    3. expires_at уже прошёл:
       пользователь отключается.
       Прошедшая дата в Remnawave не передаётся.

    4. Подписка активна и срок будущий:
       expireAt обновляется;
       пользователь включается.

    5. Если пользователя Remnawave ещё нет:
       он создаётся.
    """

    now = timezone.now()
    rw = _get_remnawave_client(client)

    # ---------------------------------------------------------
    # Получаем существующего пользователя Remnawave
    # ---------------------------------------------------------

    remote_user = None

    if client.remnawave_uuid:
        try:
            remote_user = rw.get_user_by_username(client.username)
        except Exception as exc:
            raise ClientSyncError(
                f"Не удалось получить пользователя Remnawave: {exc}"
            ) from exc

    # ---------------------------------------------------------
    # Пользователь ещё не создан
    # ---------------------------------------------------------

    if remote_user is None:
        # Не создаём нового пользователя для неактивной
        # или уже истёкшей подписки.
        if (
            not client.is_active
            or not client.expires_at
            or client.expires_at <= now
        ):
            return None

        try:
            provision_client(client)
            remote_user = rw.get_user_by_username(client.username)
        except Exception as exc:
            raise ClientSyncError(
                f"Не удалось создать пользователя Remnawave: {exc}"
            ) from exc

    user_id = remote_user.get("id")

    if user_id is None:
        raise ClientSyncError(
            "Remnawave не вернул числовой id пользователя"
        )

    # ---------------------------------------------------------
    # Клиент отключён вручную
    # ---------------------------------------------------------

    if not client.is_active:
        if remote_user.get("status") != "DISABLED":
            try:
                remote_user = rw.disable_user(user_id)
            except Exception as exc:
                raise ClientSyncError(
                    f"Не удалось отключить пользователя: {exc}"
                ) from exc

        return remote_user

    # ---------------------------------------------------------
    # Срок подписки не указан
    # ---------------------------------------------------------

    if not client.expires_at:
        if remote_user.get("status") != "DISABLED":
            try:
                remote_user = rw.disable_user(user_id)
            except Exception as exc:
                raise ClientSyncError(
                    f"Не удалось отключить пользователя: {exc}"
                ) from exc

        return remote_user

    # ---------------------------------------------------------
    # Подписка закончилась
    # ---------------------------------------------------------

    if client.expires_at <= now:
        if remote_user.get("status") != "DISABLED":
            try:
                remote_user = rw.disable_user(user_id)
            except Exception as exc:
                raise ClientSyncError(
                    f"Не удалось отключить просроченного пользователя: {exc}"
                ) from exc

        return remote_user

    # ---------------------------------------------------------
    # Активная подписка с будущим сроком
    # ---------------------------------------------------------

    try:
        remote_user = rw.update_user_expire_at(
            user_id,
            client.expires_at,
        )
    except Exception as exc:
        raise ClientSyncError(
            f"Не удалось обновить срок подписки: {exc}"
        ) from exc

    # После продления Remnawave обычно сам переводит
    # EXPIRED -> ACTIVE.
    #
    # Но если пользователь был DISABLED вручную,
    # его нужно включить отдельно.

    if remote_user.get("status") == "DISABLED":
        try:
            remote_user = rw.enable_user(user_id)
        except Exception as exc:
            raise ClientSyncError(
                f"Не удалось включить пользователя: {exc}"
            ) from exc

    return remote_user


# =========================================================
# BILLING / ORDER ACTIVATION
# =========================================================

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import Client, Order, Subscription


class OrderActivationError(Exception):
    pass


@transaction.atomic
def activate_paid_order(order_id):
    """
    Единая точка активации оплаченного заказа.

    Сейчас вызывается менеджером из Django Admin.
    Позже эту же функцию будет вызывать webhook эквайринга.

    Повторный вызов безопасен:
    уже оплаченный заказ повторно не продлевается.
    """

    order = (
        Order.objects
        .select_for_update()
        .select_related("user", "plan", "client")
        .get(pk=order_id)
    )

    # -----------------------------------------------------
    # Идемпотентность
    # -----------------------------------------------------

    if order.status == Order.STATUS_PAID:
        return order

    if order.status == Order.STATUS_CANCELLED:
        raise OrderActivationError(
            "Нельзя активировать отменённый заказ."
        )

    now = timezone.now()

    # -----------------------------------------------------
    # НОВАЯ ПОДПИСКА
    # -----------------------------------------------------

    if order.order_type == Order.TYPE_NEW:

        # Уникальное внутреннее имя Remnawave.
        # Оно не зависит от email и позволяет одному
        # пользователю иметь несколько подписок.
        username = (
            f"karion-{order.user_id}-{order.id}"
        )

        client = Client.objects.create(
            username=username,
            name=order.plan.name,
            owner=order.user,
            is_active=True,
            expires_at=(
                now
                + timedelta(
                    days=order.duration_days
                )
            ),
        )

        subscription = Subscription.objects.create(
            client=client,
            plan=order.plan,
            status=Subscription.STATUS_ACTIVE,
            starts_at=now,
            expires_at=client.expires_at,
            auto_renew=False,
            price=order.amount,
        )

        # VPN-пользователь здесь больше не создаётся.
        # Remnawave user создаётся отдельно для каждого Device
        # при добавлении устройства в личном кабинете.

        order.client = client

    # -----------------------------------------------------
    # ПРОДЛЕНИЕ
    # -----------------------------------------------------

    elif order.order_type == Order.TYPE_RENEWAL:

        if not order.client_id:
            raise OrderActivationError(
                "Для заказа продления не указано подключение."
            )

        client = (
            Client.objects
            .select_for_update()
            .get(pk=order.client_id)
        )

        try:
            subscription = client.subscription
        except Subscription.DoesNotExist:
            raise OrderActivationError(
                "У подключения отсутствует подписка."
            )

        # Если подписка ещё действует — добавляем дни
        # к текущему сроку.
        # Если уже закончилась — считаем от текущего момента.
        base_date = client.expires_at

        if not base_date or base_date < now:
            base_date = now

        new_expires_at = (
            base_date
            + timedelta(
                days=order.duration_days
            )
        )

        client.expires_at = new_expires_at
        client.is_active = True

        client.save(
            update_fields=[
                "expires_at",
                "is_active",
                "updated_at",
            ]
        )

        subscription.status = (
            Subscription.STATUS_ACTIVE
        )
        subscription.expires_at = new_expires_at
        subscription.price = order.amount

        if not subscription.starts_at:
            subscription.starts_at = now

        subscription.save(
            update_fields=[
                "status",
                "starts_at",
                "expires_at",
                "price",
                "updated_at",
            ]
        )

        try:
            # Продлеваем все АКТИВНЫЕ устройства подписки.
            # Каждый Device имеет собственного пользователя
            # Remnawave. Legacy-доступ Client больше
            # не используется.
            sync_client_devices_to_remnawave(client)

        except Exception as exc:
            raise OrderActivationError(
                "Оплата НЕ зафиксирована, потому что "
                "не удалось продлить подключение "
                f"в Remnawave: {exc}"
            ) from exc

    else:
        raise OrderActivationError(
            f"Неизвестный тип заказа: {order.order_type}"
        )

    # -----------------------------------------------------
    # Только после успешного Remnawave считаем заказ
    # полностью обработанным.
    # -----------------------------------------------------

    order.status = Order.STATUS_PAID
    order.paid_at = now

    order.save(
        update_fields=[
            "status",
            "paid_at",
            "client",
            "updated_at",
        ]
    )

    return order


# =========================================================
# DEVICE PROVISIONING
# =========================================================

from django.db import transaction

from .models import Device


class DeviceProvisionError(Exception):
    pass


class DeviceDisableError(Exception):
    pass


class DeviceRemoveError(Exception):
    pass


def _get_client_device_limit(client):
    """
    Возвращает лимит устройств текущей подписки.

    Пока лимит берём из тарифа.
    Позже зафиксируем device_limit непосредственно
    в Subscription при оплате заказа.
    """
    try:
        subscription = client.subscription
    except Exception as exc:
        raise DeviceProvisionError(
            "У подключения отсутствует подписка."
        ) from exc

    if subscription.status != subscription.STATUS_ACTIVE:
        raise DeviceProvisionError(
            "Подписка не активна."
        )

    if (
        not subscription.expires_at
        or subscription.expires_at <= timezone.now()
    ):
        raise DeviceProvisionError(
            "Срок подписки истёк."
        )

    return subscription.plan.device_limit


@transaction.atomic
def provision_device(client, name, platform):
    """
    Создаёт отдельное устройство и отдельного
    пользователя Remnawave.

    Один Device = один Remnawave UUID.
    """

    # Блокируем Client на время проверки лимита.
    client = (
        client.__class__.objects
        .select_for_update()
        .get(pk=client.pk)
    )

    limit = _get_client_device_limit(client)

    active_count = (
        Device.objects
        .filter(
            client=client,
            is_active=True,
        )
        .count()
    )

    if active_count >= limit:
        raise DeviceProvisionError(
            f"Достигнут лимит устройств: {active_count} из {limit}."
        )

    device = Device.objects.create(
        client=client,
        name=(name or "Моё устройство").strip(),
        platform=platform,
        is_active=True,
    )

    # Техническое имя Remnawave позволяет сразу определить:
    # u = владелец Django
    # c = VPN-подписка
    # d = конкретное устройство
    if client.owner_id is None:
        raise DeviceProvisionError(
            "У VPN-подписки не указан владелец."
        )

    remnawave_username = (
        f"karion-u{client.owner_id}"
        f"-c{client.id}"
        f"-d{device.id}"
    )

    try:
        rw = _get_remnawave_client(client)

        remote_user = rw.create_user(
            username=remnawave_username,
            expire_at=client.subscription.expires_at,
        )
    except Exception as exc:
        raise DeviceProvisionError(
            f"Не удалось создать устройство в Remnawave: {exc}"
        ) from exc

    remnawave_user_id = remote_user.get("id")
    remnawave_uuid = remote_user.get("vlessUuid")
    remnawave_short_uuid = remote_user.get("shortUuid")

    if (
        remnawave_user_id is None
        or not remnawave_uuid
        or not remnawave_short_uuid
    ):
        raise DeviceProvisionError(
            "Remnawave не вернул необходимые данные устройства."
        )

    device.remnawave_username = remnawave_username
    device.remnawave_user_id = remnawave_user_id
    device.remnawave_uuid = remnawave_uuid
    device.remnawave_short_uuid = remnawave_short_uuid

    device.save(
        update_fields=[
            "remnawave_username",
            "remnawave_user_id",
            "remnawave_uuid",
            "remnawave_short_uuid",
            "updated_at",
        ]
    )

    return device


@transaction.atomic
def disable_device(device):
    """
    Отвязывает устройство.

    Пользователь Remnawave переводится в DISABLED,
    поэтому UUID этого устройства перестаёт работать.
    Устройство остаётся в БД для истории и больше
    не занимает место в лимите.
    """

    device = (
        Device.objects
        .select_for_update()
        .select_related("client")
        .get(pk=device.pk)
    )

    if not device.is_active:
        return device

    if device.remnawave_user_id is not None:
        try:
            rw = _get_remnawave_client(device.client)
            rw.disable_user(device.remnawave_user_id)
        except Exception as exc:
            raise DeviceDisableError(
                f"Не удалось отключить устройство в Remnawave: {exc}"
            ) from exc

    device.is_active = False
    device.save(
        update_fields=[
            "is_active",
            "updated_at",
        ]
    )

    return device



@transaction.atomic
def remove_device_permanently(device):
    """
    Окончательно отвязывает устройство пользователя.

    Remnawave User удаляется полностью.
    Device остаётся в Django как история.

    ВАЖНО:
    сначала удаляем доступ в Remnawave и только после
    успешного удаления помечаем Device неактивным.
    """

    device = (
        Device.objects
        .select_for_update()
        .select_related("client")
        .get(pk=device.pk)
    )

    # Повторная отвязка уже неактивного устройства
    # ничего не делает.
    if not device.is_active:
        return device

    if device.remnawave_user_id is not None:
        try:
            rw = _get_remnawave_client(device.client)

            rw.delete_user(
                device.remnawave_user_id
            )

        except Exception as exc:
            # Если пользователя уже нет в Remnawave,
            # доступ фактически уже уничтожен.
            message = str(exc)

            if (
                "404" not in message
                and "not found" not in message.lower()
            ):
                raise DeviceRemoveError(
                    "Не удалось отвязать устройство "
                    f"в Remnawave: {exc}"
                ) from exc

    device.is_active = False
    device.unlinked_at = timezone.now()

    device.save(
        update_fields=[
            "is_active",
            "unlinked_at",
            "updated_at",
        ]
    )

    return device


class DeviceEnableError(Exception):
    pass


@transaction.atomic
def enable_device(device):
    """
    Повторно включает ранее отключённое устройство.

    Проверяется:
      - Client активен;
      - подписка активна и не истекла;
      - лимит устройств не превышен;
      - Remnawave User существует.

    Сначала включаем Remnawave.
    Только после успешного включения меняем состояние Django.
    """

    device = (
        Device.objects
        .select_for_update()
        .select_related(
            "client",
            "client__subscription",
            "client__subscription__plan",
        )
        .get(pk=device.pk)
    )

    if device.is_active:
        return device

    if device.unlinked_at is not None:
        raise DeviceEnableError(
            "Устройство было окончательно отвязано. "
            "Подключите его заново как новое устройство."
        )

    client = device.client

    # Блокируем Client, чтобы два параллельных включения
    # не смогли одновременно занять последнее место.
    client = (
        client.__class__.objects
        .select_for_update()
        .get(pk=client.pk)
    )

    if not client.is_active:
        raise DeviceEnableError(
            "Подключение отключено."
        )

    try:
        limit = _get_client_device_limit(client)
    except Exception as exc:
        raise DeviceEnableError(str(exc)) from exc

    active_count = (
        Device.objects
        .filter(
            client=client,
            is_active=True,
        )
        .count()
    )

    if active_count >= limit:
        raise DeviceEnableError(
            f"Нет свободного места: "
            f"{active_count} из {limit} устройств уже активны."
        )

    if device.remnawave_user_id is None:
        raise DeviceEnableError(
            "У устройства отсутствует Remnawave User ID."
        )

    subscription = client.subscription
    rw = _get_remnawave_client(client)

    try:
        # Сначала обязательно актуализируем срок.
        remote_user = rw.update_user_expire_at(
            device.remnawave_user_id,
            subscription.expires_at,
        )

        # После обновления expireAt пользователь может
        # остаться DISABLED — включаем явно.
        if remote_user.get("status") == "DISABLED":
            remote_user = rw.enable_user(
                device.remnawave_user_id
            )

    except Exception as exc:
        raise DeviceEnableError(
            f"Не удалось включить устройство "
            f"в Remnawave: {exc}"
        ) from exc

    device.is_active = True

    device.save(
        update_fields=[
            "is_active",
            "updated_at",
        ]
    )

    return device



# =========================================================
# DEVICE SUBSCRIPTION SYNC
# =========================================================

def sync_client_devices_to_remnawave(client):
    """
    Синхронизирует все устройства Client с состоянием подписки.

    Активное устройство:
      - при действующей подписке получает новый expireAt
        и включается при необходимости;
      - при остановленной/истёкшей подписке отключается.

    Отвязанные Device(is_active=False) НЕ включаются обратно.
    """

    now = timezone.now()
    rw = _get_remnawave_client(client)

    try:
        subscription = client.subscription
    except Subscription.DoesNotExist:
        subscription = None

    subscription_active = (
        client.is_active
        and subscription is not None
        and subscription.status == Subscription.STATUS_ACTIVE
        and subscription.expires_at is not None
        and subscription.expires_at > now
    )

    devices = (
        Device.objects
        .filter(
            client=client,
            is_active=True,
        )
        .exclude(remnawave_user_id__isnull=True)
    )

    results = []

    for device in devices:
        try:
            remote_user = rw.get_user_by_username(
                device.remnawave_username
            )

            if not remote_user:
                raise DeviceProvisionError(
                    f"Устройство {device.id}: "
                    "пользователь Remnawave не найден."
                )

            user_id = remote_user.get("id")

            if user_id is None:
                raise DeviceProvisionError(
                    f"Устройство {device.id}: "
                    "Remnawave не вернул id."
                )

            if not subscription_active:

                if remote_user.get("status") != "DISABLED":
                    remote_user = rw.disable_user(user_id)

                results.append(
                    (device.id, "DISABLED")
                )
                continue

            # Сначала обновляем срок.
            remote_user = rw.update_user_expire_at(
                user_id,
                subscription.expires_at,
            )

            # Если устройство ранее было отключено именно
            # из-за состояния подписки — включаем обратно.
            if remote_user.get("status") == "DISABLED":
                remote_user = rw.enable_user(user_id)

            results.append(
                (
                    device.id,
                    remote_user.get("status"),
                )
            )

        except Exception as exc:
            raise ClientSyncError(
                f"Ошибка синхронизации устройства "
                f"{device.id} ({device.name}): {exc}"
            ) from exc

    return results
