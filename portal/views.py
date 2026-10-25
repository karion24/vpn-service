import json
import shutil
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Prefetch
from django.http import Http404, JsonResponse, FileResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import Client, Plan, Order, Device, Device
from .config_generator import (
    generate_client_config,
    ConfigGenerationError,
)
from .remnawave import _request
from .services import (
    provision_device,
    disable_device,
    DeviceProvisionError,
    DeviceDisableError,
    DeviceRemoveError,
    remove_device_permanently,
)



def _get_device(token):
    return get_object_or_404(
        Device.objects.select_related(
            "client",
            "client__subscription",
            "client__subscription__plan",
        ),
        token=token,
    )


def _get_active_device(token):
    device = _get_device(token)
    client = device.client

    if not device.is_active:
        raise Http404("Устройство отключено")

    if not client.is_active:
        raise Http404("Подписка отключена")

    try:
        subscription = client.subscription
    except Exception:
        raise Http404("Подписка не найдена")

    if subscription.status != subscription.STATUS_ACTIVE:
        raise Http404("Подписка не активна")

    expires_at = subscription.expires_at or client.expires_at

    if not expires_at:
        raise Http404("Срок подписки не указан")

    if expires_at <= timezone.now():
        raise Http404("Срок подписки истёк")

    if not device.remnawave_uuid:
        raise Http404("Подключение устройства ещё не создано")

    return device

def _get_client(token):
    return get_object_or_404(
        Client,
        token=token,
    )


def _get_active_client(token):
    client = _get_client(token)

    if not client.is_active:
        raise Http404("Подписка отключена")

    if not client.expires_at:
        raise Http404("Срок подписки не указан")

    if client.expires_at <= timezone.now():
        raise Http404("Срок подписки истёк")

    if not client.remnawave_uuid:
        raise Http404("VPN-пользователь ещё не создан")

    return client


def _get_client_state(client):
    now = timezone.now()

    if not client.is_active:
        return {
            "code": "disabled",
            "title": "Подписка приостановлена",
            "description": (
                "Доступ к Karion VPN временно отключён. "
                "После активации подписки подключение снова станет доступно."
            ),
            "can_connect": False,
        }

    if not client.expires_at:
        return {
            "code": "no_expiry",
            "title": "Подписка ещё не активирована",
            "description": (
                "Для этой подписки пока не установлен срок действия."
            ),
            "can_connect": False,
        }

    if client.expires_at <= now:
        return {
            "code": "expired",
            "title": "Срок подписки закончился",
            "description": (
                "Для продолжения работы необходимо продлить подписку."
            ),
            "can_connect": False,
        }

    if not client.remnawave_uuid:
        return {
            "code": "preparing",
            "title": "Подключение готовится",
            "description": (
                "VPN-доступ ещё создаётся. "
                "Попробуйте открыть эту страницу немного позже."
            ),
            "can_connect": False,
        }

    return {
        "code": "active",
        "title": "Подписка активна",
        "description": "",
        "can_connect": True,
    }


def _get_locations():
    try:
        hosts_response = _request(
            "GET",
            "/hosts",
        )

        nodes_response = _request(
            "GET",
            "/nodes",
        )

        hosts = hosts_response.get(
            "response",
            [],
        )

        nodes = nodes_response.get(
            "response",
            [],
        )

    except Exception:
        return []

    active_nodes_by_address = {}

    for node in nodes:
        if node.get("isDisabled"):
            continue

        if not node.get("isConnected"):
            continue

        address = str(
            node.get("address", "")
        ).strip()

        if not address:
            continue

        active_nodes_by_address[address] = node

    locations = []

    for host in hosts:
        if host.get("isDisabled"):
            continue

        if host.get("isHidden"):
            continue

        address = str(
            host.get("address", "")
        ).strip()

        if not address:
            continue

        node = active_nodes_by_address.get(
            address
        )

        if not node:
            continue

        locations.append(
            {
                "name": (
                    host.get("remark")
                    or node.get("name")
                    or address
                ),
                "code": (
                    node.get("countryCode")
                    or ""
                ).upper(),
                "address": address,
                "view_position": host.get(
                    "viewPosition",
                    999999,
                ),
            }
        )

    locations.sort(
        key=lambda item: (
            item["view_position"],
            item["name"],
        )
    )

    return locations



HEALTHCHECK_STATE_FILE = Path(
    "/opt/karion-healthcheck/state.json"
)

HEALTHCHECK_STALE_SECONDS = 150


def _parse_health_datetime(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _format_health_datetime(value):
    """
    ISO/UTC -> локальное время Django.
    При TIME_ZONE=Asia/Krasnoyarsk получим Красноярск.
    """
    dt = value

    if isinstance(value, str):
        dt = _parse_health_datetime(value)

    if dt is None:
        return "—"

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=dt_timezone.utc
        )

    try:
        dt = timezone.localtime(dt)
    except Exception:
        pass

    return dt.strftime(
        "%d.%m.%Y %H:%M:%S"
    )


def _load_healthcheck_state():
    try:
        data = json.loads(
            HEALTHCHECK_STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

    except Exception as exc:
        return {
            "available": False,
            "error": str(exc),
            "updated_at": None,
            "updated_at_display": "—",
            "age_seconds": None,
            "stale": True,
            "nodes": [],
            "fingerprint": {},
            "fingerprint_diagnosis": None,
        }

    updated_at = _parse_health_datetime(
        data.get("updated_at")
    )

    stale = True
    age_seconds = None

    if updated_at is not None:
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(
                tzinfo=dt_timezone.utc
            )

        age_seconds = max(
            0,
            int(
                (
                    datetime.now(dt_timezone.utc)
                    - updated_at
                ).total_seconds()
            ),
        )

        stale = (
            age_seconds
            > HEALTHCHECK_STALE_SECONDS
        )

    nodes = []

    for address, item in data.get(
        "nodes",
        {},
    ).items():
        node = dict(item)

        node["address"] = (
            node.get("address")
            or address
        )

        status = node.get(
            "status",
            "UNKNOWN",
        )

        vpn_status = node.get(
            "vpn_status",
            "UNKNOWN",
        )

        fail_count = int(
            node.get("fail_count")
            or 0
        )

        success_count = int(
            node.get("success_count")
            or 0
        )

        # Визуальное состояние карточки.
        if status == "DOWN":
            node["display_state"] = "down"

        elif vpn_status == "DOWN":
            # Узел ещё формально UP, но уже есть
            # неудачные реальные VPN-проверки.
            node["display_state"] = "warning"

        elif success_count > 0:
            node["display_state"] = "recovery"

        elif status == "UP":
            node["display_state"] = "up"

        else:
            node["display_state"] = "warning"

        if status == "DOWN":
            node["display_label"] = "DOWN"

        elif vpn_status == "DOWN":
            node["display_label"] = (
                f"FAIL {fail_count}/3"
            )

        elif success_count > 0:
            node["display_label"] = (
                f"RECOVERY {success_count}/2"
            )

        else:
            node["display_label"] = status

        node["last_check_display"] = (
            _format_health_datetime(
                node.get("last_check")
            )
        )

        nodes.append(node)

    country_order = {
        "NL": 10,
        "FI": 20,
        "SE": 30,
    }

    nodes.sort(
        key=lambda item: (
            country_order.get(
                item.get("code"),
                999,
            ),
            item.get("name", ""),
        )
    )

    fingerprint = dict(
        data.get("fingerprint")
        or {}
    )

    fingerprint[
        "last_change_display"
    ] = _format_health_datetime(
        fingerprint.get("last_change")
    )

    fingerprint[
        "cooldown_until_display"
    ] = _format_health_datetime(
        fingerprint.get("cooldown_until")
    )

    history = []

    for item in fingerprint.get(
        "history",
        [],
    ):
        history_item = dict(item)

        history_item["at_display"] = (
            _format_health_datetime(
                history_item.get("at")
            )
        )

        history.append(
            history_item
        )

    fingerprint["history"] = history

    return {
        "available": True,
        "error": None,
        "updated_at": updated_at,
        "updated_at_display":
            _format_health_datetime(
                updated_at
            ),
        "age_seconds": age_seconds,
        "stale": stale,
        "nodes": nodes,
        "fingerprint": fingerprint,
        "fingerprint_diagnosis": data.get(
            "fingerprint_diagnosis"
        ),
    }


@staff_member_required
def vpn_monitor(request):
    health = _load_healthcheck_state()

    nodes = health["nodes"]

    total = len(nodes)

    up = sum(
        1
        for node in nodes
        if node.get("status") == "UP"
    )

    down = sum(
        1
        for node in nodes
        if node.get("status") == "DOWN"
    )

    warning = sum(
        1
        for node in nodes
        if node.get("display_state")
        in ("warning", "recovery")
    )

    # Это соответствует текущей логике генератора:
    # DOWN healthcheck-узлы в новые конфиги
    # не попадают.
    subscription_nodes = sum(
        1
        for node in nodes
        if node.get("status") != "DOWN"
        and not node.get("admin_disabled")
        and not node.get("hidden")
    )

    if not health["available"]:
        system_state = "danger"
        system_title = (
            "Healthcheck недоступен"
        )
        system_text = (
            "Не удалось прочитать состояние "
            "системы мониторинга."
        )

    elif health["stale"]:
        system_state = "warning"
        system_title = (
            "Нет свежих данных healthcheck"
        )
        system_text = (
            "Последнее состояние устарело."
        )

    elif down:
        system_state = "danger"

        if down == 1:
            system_title = (
                "1 VPN-сервер недоступен"
            )
        else:
            system_title = (
                f"{down} VPN-сервера недоступны"
            )

        system_text = (
            "Недоступные узлы исключаются "
            "из новых конфигураций."
        )

    elif warning:
        system_state = "warning"
        system_title = (
            "Есть нестабильные VPN-узлы"
        )
        system_text = (
            "Идёт подтверждение сбоя "
            "или восстановления."
        )

    else:
        system_state = "good"
        system_title = (
            "Система работает нормально"
        )
        system_text = (
            "Все VPN-узлы проходят "
            "реальную проверку трафика."
        )

    diagnosis = health.get(
        "fingerprint_diagnosis"
    )

    fingerprint_alert = bool(
        diagnosis
    )

    context = {
        "health": health,
        "total_nodes": total,
        "up_nodes": up,
        "down_nodes": down,
        "warning_nodes": warning,
        "subscription_nodes":
            subscription_nodes,
        "system_state": system_state,
        "system_title": system_title,
        "system_text": system_text,
        "fingerprint_alert":
            fingerprint_alert,
    }

    return render(
        request,
        "portal/vpn_monitor.html",
        context,
    )

def connect_index(request):
    return render(
        request,
        "portal/connect_index.html",
    )


def connect_client(request, token):
    client = _get_client(token)
    state = _get_client_state(client)

    locations = []

    if state["can_connect"]:
        locations = _get_locations()

    context = {
        "client": client,
        "locations": locations,
        "state": state,
    }

    return render(
        request,
        "portal/connect_client.html",
        context,
    )


def connect_full(request, token):
    client = _get_active_client(token)

    try:
        config = generate_client_config(
            client,
            "full",
        )

    except ConfigGenerationError as exc:
        return JsonResponse(
            {
                "error": str(exc),
            },
            status=500,
        )

    response = JsonResponse(
        config,
        json_dumps_params={
            "ensure_ascii": False,
            "indent": 2,
        },
    )

    # INCY: периодическое обновление подписки.
    # Значение задаётся в часах.
    response["profile-update-interval"] = "1"

    # Конфигурация динамическая: не кэшировать.
    response["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, max-age=0"
    )
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"

    return response


def connect_selective(request, token):
    client = _get_active_client(token)

    try:
        config = generate_client_config(
            client,
            "selective",
        )

    except ConfigGenerationError as exc:
        return JsonResponse(
            {
                "error": str(exc),
            },
            status=500,
        )

    response = JsonResponse(
        config,
        json_dumps_params={
            "ensure_ascii": False,
            "indent": 2,
        },
    )

    # INCY: периодическое обновление подписки.
    # Значение задаётся в часах.
    response["profile-update-interval"] = "1"

    # Конфигурация динамическая: не кэшировать.
    response["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, max-age=0"
    )
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"

    return response


# =========================================================
# CUSTOMER CABINET
# =========================================================

from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.contrib import messages

from .forms import RegisterForm, LoginForm


def register_view(request):
    if request.user.is_authenticated:
        return redirect("cabinet")

    referral_code = (
        request.GET.get("ref", "")
        or request.POST.get("ref", "")
    ).strip()

    if request.method == "POST":
        form = RegisterForm(request.POST)

        if form.is_valid():
            user = form.save()

            # referral_code пока намеренно не обрабатываем.
            # Точка входа уже предусмотрена, а саму
            # реферальную систему добавим отдельным модулем.

            login(request, user)

            return redirect("cabinet")

    else:
        form = RegisterForm()

    return render(
        request,
        "portal/register.html",
        {
            "form": form,
            "referral_code": referral_code,
        },
    )


@login_required
def cabinet(request):
    # Сверяем недавние платежи с доверенным шлюзом до отображения кабинета.
    from .cabinet_payment_reconciliation import reconcile_cabinet_payments
    reconcile_cabinet_payments(request.user)

    clients = (
        Client.objects
        .filter(owner=request.user)
        .select_related("subscription__plan")
        .prefetch_related(
            Prefetch(
                "devices",
                queryset=Device.objects.filter(
                    is_active=True
                ).order_by("created_at"),
                to_attr="active_devices",
            )
        )
        .order_by("-created_at")
    )

    pending_orders = (
        Order.objects
        .filter(
            user=request.user,
            status=Order.STATUS_PENDING,
            payment_expires_at__gt=timezone.now(),
        )
        .select_related("plan")
        .order_by("-created_at")
    )


    return render(
        request,
        "portal/cabinet.html",
        {
            "clients": clients,
            "pending_orders": pending_orders,
            "processing_orders": Order.objects.none(),
            "refresh_payment_status": pending_orders.exists(),
        },
    )




@login_required
def add_device(request, client_id):
    """
    Добавляет устройство к подписке текущего пользователя.
    """
    if request.method != "POST":
        return redirect("cabinet")

    client = get_object_or_404(
        Client.objects.select_related("subscription__plan"),
        pk=client_id,
        owner=request.user,
    )

    name = request.POST.get("name", "").strip()
    platform = request.POST.get("platform", "").strip()

    valid_platforms = {
        Device.PLATFORM_IOS,
        Device.PLATFORM_ANDROID,
        Device.PLATFORM_WINDOWS,
        Device.PLATFORM_OTHER,
    }

    if platform not in valid_platforms:
        platform = Device.PLATFORM_OTHER

    if not name:
        name = "Моё устройство"

    try:
        device = provision_device(
            client=client,
            name=name,
            platform=platform,
        )
    except DeviceProvisionError as exc:
        from django.contrib import messages
        messages.error(request, str(exc))
        return redirect("cabinet")

    return redirect(
        "device_connect",
        token=device.token,
    )


@login_required
def remove_device(request, device_id):
    """
    Отвязывает устройство текущего пользователя.
    """
    if request.method != "POST":
        return redirect("cabinet")

    device = get_object_or_404(
        Device.objects.select_related("client"),
        pk=device_id,
        client__owner=request.user,
    )

    try:
        remove_device_permanently(device)

    except DeviceRemoveError as exc:
        from django.contrib import messages
        messages.error(request, str(exc))

    return redirect("cabinet")


@login_required
def renew_subscription(request, client_id):
    """
    Создаёт заказ на продление существующего подключения.

    Если уже есть неоплаченный заказ на продление этого
    подключения — новый заказ не создаём, а открываем старый.
    """

    if request.method != "POST":
        return redirect("cabinet")

    client = get_object_or_404(
        Client.objects.select_related("subscription__plan"),
        pk=client_id,
        owner=request.user,
    )

    try:
        subscription = client.subscription
    except Subscription.DoesNotExist:
        messages.error(
            request,
            "У подключения отсутствует подписка.",
        )
        return redirect("cabinet")

    # -----------------------------------------------------
    # Не создаём дубликаты неоплаченных продлений
    # -----------------------------------------------------

    existing_order = (
        Order.objects
        .filter(
            user=request.user,
            client=client,
            order_type=Order.TYPE_RENEWAL,
            status=Order.STATUS_PENDING,
            payment_expires_at__gt=timezone.now(),
        )
        .order_by("-created_at")
        .first()
    )

    if existing_order:
        messages.info(
            request,
            (
                "Заявка на продление этого подключения "
                "уже существует."
            ),
        )
        return redirect(
            "order_detail",
            order_id=existing_order.id,
        )

    plan = subscription.plan

    order = Order.objects.create(
        user=request.user,
        client=client,
        plan=plan,
        order_type=Order.TYPE_RENEWAL,
        status=Order.STATUS_PENDING,
        payment_expires_at=timezone.now() + __import__("datetime").timedelta(minutes=10),
        amount=plan.price,
        duration_days=plan.duration_days,
        device_limit=plan.device_limit,
    )

    messages.success(
        request,
        (
            f"Создана заявка №{order.id} "
            f"на продление подключения."
        ),
    )

    return redirect("cabinet")

@login_required
def plans_view(request):
    """
    Выбор тарифа для создания новой VPN-подписки.
    """
    plans = (
        Plan.objects
        .filter(is_active=True)
        .order_by("sort_order", "price", "id")
    )

    return render(
        request,
        "portal/plans.html",
        {
            "plans": plans,
        },
    )



@login_required
def create_order(request, plan_id):
    """
    Создаёт заказ на новую VPN-подписку.

    ВАЖНО:
    Client, Subscription и пользователь Remnawave
    здесь ещё НЕ создаются.
    """
    if request.method != "POST":
        raise Http404()

    plan = get_object_or_404(
        Plan,
        pk=plan_id,
        is_active=True,
    )

    # Есть ли уже действующая подписка.
    has_active_subscription = (
        Client.objects
        .filter(
            owner=request.user,
            is_active=True,
            expires_at__gt=timezone.now(),
        )
        .exists()
    )

    # Есть ли уже неоплаченная заявка.
    has_pending_order = (
        Order.objects
        .filter(
            user=request.user,
            status=Order.STATUS_PENDING,
        )
        .exists()
    )

    # Если что-то уже есть — первый POST ничего
    # не создаёт, а показывает подтверждение.
    if (
        (has_active_subscription or has_pending_order)
        and request.POST.get("confirmed") != "yes"
    ):
        return render(
            request,
            "portal/order_confirm.html",
            {
                "plan": plan,
                "has_active_subscription": (
                    has_active_subscription
                ),
                "has_pending_order": (
                    has_pending_order
                ),
            },
        )

    order = Order.objects.create(
        user=request.user,
        plan=plan,
        order_type=Order.TYPE_NEW,
        status=Order.STATUS_PENDING,
        payment_expires_at=timezone.now() + __import__("datetime").timedelta(minutes=10),
        amount=plan.price,
        duration_days=plan.duration_days,
        device_limit=plan.device_limit,
    )

    return redirect("cabinet")


@login_required
def order_detail(request, order_id):
    """
    Страница заказа.

    Пользователь может видеть только собственный заказ.
    """
    order = get_object_or_404(
        Order.objects.select_related(
            "plan",
            "client",
        ),
        pk=order_id,
        user=request.user,
    )

    return render(
        request,
        "portal/order_detail.html",
        {
            "order": order,
            "current_time": timezone.now(),
        },
    )



@login_required
def cancel_order(request, order_id):
    """
    Отказ клиента от неоплаченной заявки.

    Запись Order физически не удаляется.
    """
    if request.method != "POST":
        raise Http404()

    order = get_object_or_404(
        Order,
        pk=order_id,
        user=request.user,
    )

    # Оплаченные и уже отменённые заказы
    # клиент изменять не может.
    if order.status != Order.STATUS_PENDING:
        return redirect(
            "order_detail",
            order_id=order.pk,
        )

    # Отдельное серверное подтверждение.
    if request.POST.get("confirmed") != "yes":
        return render(
            request,
            "portal/order_cancel_confirm.html",
            {
                "order": order,
            },
        )

    from .order_expiration import can_cancel_order

    if not can_cancel_order(order):
        return redirect(
            "order_detail",
            order_id=order.pk,
        )

    order.status = Order.STATUS_CANCELLED

    order.save(
        update_fields=[
            "status",
            "updated_at",
        ]
    )

    return redirect("cabinet")


def how_to_connect(request):
    """
    Публичная инструкция по подключению.
    Доступна без регистрации и авторизации.
    """
    return render(
        request,
        "portal/how_to_connect.html",
    )


def home(request):
    """
    Главная точка входа.

    Авторизованный пользователь попадает в кабинет.
    Новый пользователь видит инструкцию подключения.
    """
    if request.user.is_authenticated:
        return redirect("cabinet")

    return how_to_connect(request)



# =========================================================
# DEVICE CONNECT
# =========================================================

def device_connect_client(request, token):
    """
    Страница подключения конкретного устройства.
    """
    device = _get_active_device(token)

    return render(
        request,
        "portal/connect_client.html",
        {
            "client": device.client,
            "device": device,
            "subscription_client": device.client,
            "device_mode": True,
            "state": {
                "code": "active",
                "title": "Подключение активно",
                "description": "",
                "can_connect": True,
            },
            "locations": _get_locations(),
        },
    )


def device_connect_full(request, token):
    """
    Полная конфигурация конкретного устройства.
    """
    device = _get_active_device(token)

    try:
        config = generate_client_config(
            device,
            mode="full",
        )
    except ConfigGenerationError as exc:
        raise Http404(str(exc))

    response = JsonResponse(
        config,
        json_dumps_params={
            "ensure_ascii": False,
        },
    )

    response["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, max-age=0"
    )
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"

    return response


def device_connect_selective(request, token):
    """
    Выборочная конфигурация конкретного устройства.
    """
    device = _get_active_device(token)

    try:
        config = generate_client_config(
            device,
            mode="selective",
        )
    except ConfigGenerationError as exc:
        raise Http404(str(exc))

    response = JsonResponse(
        config,
        json_dumps_params={
            "ensure_ascii": False,
        },
    )

    response["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, max-age=0"
    )
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"

    return response


# =========================================================
# WINDOWS CLIENT DOWNLOAD
# =========================================================


def device_windows_download(request, token):
    """
    Подготовка Windows клиента Karion VPN.
    """

    device = _get_active_device(token)

    import subprocess
    import time


    download_dir = Path(
        "/opt/karion-portal/downloads"
    )

    download_dir.mkdir(
        parents=True,
        exist_ok=True,
    )


    # Удаляем опубликованные архивы старше 10 минут.
    now = time.time()

    for old_file in download_dir.glob("*.zip"):
        try:
            if now - old_file.stat().st_mtime > 600:
                old_file.unlink()
        except Exception:
            pass


    # Генератор создаёт:
    #
    # /tmp/Karion-{device.name}.zip
    # /tmp/Karion-{device.name}/
    #
    package_name = f"Karion-{device.name}"

    archive = (
        Path("/tmp") /
        f"{package_name}.zip"
    )

    build_dir = (
        Path("/tmp") /
        package_name
    )


    # На всякий случай убираем предыдущую сборку
    # именно этого устройства.
    try:
        archive.unlink(missing_ok=True)
    except Exception:
        pass

    try:
        if build_dir.exists():
            shutil.rmtree(build_dir)
    except Exception:
        pass


    try:
        subprocess.run(
            [
                "/opt/karion-portal/venv/bin/python",
                "manage.py",
                "create_v2rayn_config",
                token,
            ],
            cwd="/opt/karion-portal",
            check=True,
        )

    except subprocess.CalledProcessError:
        raise Http404(
            "Ошибка создания клиента"
        )


    if not archive.exists():
        raise Http404(
            "Архив не создан"
        )


    # Публичное имя не содержит имени устройства.
    # Используем token, поэтому архивы разных устройств
    # не пересекаются.
    public_name = (
        f"Karion-{device.token[:8]}-Windows.zip"
    )

    download_file = (
        download_dir /
        public_name
    )


    try:
        shutil.copy2(
            archive,
            download_file,
        )

    finally:
        # Сборка в /tmp больше не нужна.
        try:
            archive.unlink(missing_ok=True)
        except Exception:
            pass

        try:
            if build_dir.exists():
                shutil.rmtree(build_dir)
        except Exception:
            pass


    return JsonResponse(
        {
            "status": "ready",
            "url": "/downloads/" + public_name,
        }
    )

