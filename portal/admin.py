from .models import RemnawaveNode
from .remnawave_node_sync import sync_remnawave_nodes
from .remnawave_node_delete import (
    inspect_remnawave_node_delete,
    delete_remnawave_node,
    RemnawaveNodeDeleteError,
)
from .remnawave_node_lifecycle import (
    set_remnawave_node_enabled,
    RemnawaveNodeLifecycleError,
)
from .remnawave_node_create import reconcile_remnawave_node
from .remnawave_node_create import build_install_script, RemnawaveNodeCreateError
from django.urls import path
from django.shortcuts import render, redirect

from .remnawave_node_forms import RemnawaveNodeCreateForm
from .remnawave_node_create import (
    create_remnawave_node,
    RemnawaveNodeCreateError,
)
from django import forms
from django.contrib import admin, messages
from django.urls import reverse, path
from django.http import HttpResponse
from django.utils.html import format_html

from .models import Client, Device, RemnawaveServer, CustomerProfile
from .services import (
    provision_client,
    sync_client_to_remnawave,
)



# =========================================================
# REMNAWAVE SERVERS
# =========================================================

@admin.register(RemnawaveServer)
class RemnawaveServerAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "code",
        "base_url",
        "is_active",
        "accept_new_clients",
        "priority",
        "updated_at",
    )

    list_filter = (
        "is_active",
        "accept_new_clients",
    )

    search_fields = (
        "name",
        "code",
        "base_url",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Сервер",
            {
                "fields": (
                    "name",
                    "code",
                    "base_url",
                    "is_active",
                    "accept_new_clients",
                    "priority",
                ),
            },
        ),
        (
            "Подключение к Remnawave",
            {
                "fields": (
                    "api_token",
                    "squad_uuid",
                ),
            },
        ),
        (
            "Служебная информация",
            {
                "classes": ("collapse",),
                "fields": (
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )

class DeviceInline(admin.TabularInline):
    model = Device
    extra = 0
    can_delete = False
    show_change_link = True

    fields = (
        "name",
        "platform",
        "status_display",
        "unlinked_at",
        "remnawave_username",
    )

    readonly_fields = fields

    verbose_name = "Устройство"
    verbose_name_plural = "Устройства этой подписки"

    @admin.display(description="Состояние")
    def status_display(self, obj):
        if obj.unlinked_at is not None:
            return "Отвязано пользователем"

        if obj.is_active:
            return "Активно"

        return "Отключено администратором"

    def has_add_permission(self, request, obj=None):
        # Устройства создаются только через штатную
        # процедуру provision_device(), которая создаёт
        # соответствующего пользователя Remnawave.
        return False


class CustomerProfileAdminForm(forms.ModelForm):
    email = forms.EmailField(label="Электронный адрес", required=False)

    class Meta:
        model = CustomerProfile
        fields = ("phone", "telegram", "max_contact", "primary_channel")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.user_id:
            self.fields["email"].initial = self.instance.user.email
        self.fields["telegram"].help_text = "Числовой chat_id личного чата с вашим ботом; не @username."
        self.fields["max_contact"].help_text = "Числовой user_id MAX. Пользователь должен разрешить сообщения бота."

    def clean(self):
        cleaned = super().clean()
        # Model.clean validates against the email being submitted, not the old one.
        if self.instance.user_id and "email" in cleaned:
            self.instance.user.email = cleaned["email"]
        return cleaned


@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    form = CustomerProfileAdminForm

    def save_model(self, request, obj, form, change):
        obj.user.email = form.cleaned_data["email"]
        obj.user.save(update_fields=["email"])
        super().save_model(request, obj, form, change)

    list_display = (
        "email_display",
        "phone",
        "telegram",
        "max_contact",
        "primary_channel",
        "subscriptions_display",
        "devices_display",
        "date_joined_display",
    )

    search_fields = (
        "user__email",
        "user__username",
        "user__first_name",
        "user__last_name",
        "phone",
        "telegram",
        "max_contact",
    )

    readonly_fields = (
        "email_display",
        "username_display",
        "first_name_display",
        "last_name_display",
        "subscriptions_detail",
        "devices_detail",
        "orders_detail",
        "date_joined_display",
        "last_login_display",
        "django_user_link",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Клиент",
            {
                "fields": (
                    "email",
                    "first_name_display",
                    "last_name_display",
                    "phone",
                    "telegram",
                    "max_contact",
                    "primary_channel",
                ),
            },
        ),
        (
            "VPN",
            {
                "fields": (
                    "subscriptions_detail",
                    "devices_detail",
                    "orders_detail",
                ),
            },
        ),
        (
            "Служебная информация",
            {
                "classes": ("collapse",),
                "fields": (
                    "username_display",
                    "date_joined_display",
                    "last_login_display",
                    "django_user_link",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("user")
        )

    @admin.display(description="Email")
    def email_display(self, obj):
        return obj.user.email or "—"

    @admin.display(description="Логин")
    def username_display(self, obj):
        return obj.user.username or "—"

    @admin.display(description="Имя")
    def first_name_display(self, obj):
        return obj.user.first_name or "—"

    @admin.display(description="Фамилия")
    def last_name_display(self, obj):
        return obj.user.last_name or "—"

    @admin.display(description="VPN-подписки")
    def subscriptions_display(self, obj):
        return Client.objects.filter(
            owner=obj.user
        ).count()

    @admin.display(description="Активные устройства")
    def devices_display(self, obj):
        return Device.objects.filter(
            client__owner=obj.user,
            is_active=True,
            unlinked_at__isnull=True,
        ).count()

    @admin.display(description="VPN-подписки")
    def subscriptions_detail(self, obj):
        clients = (
            Client.objects
            .filter(owner=obj.user)
            .select_related(
                "subscription",
                "subscription__plan",
            )
            .order_by("id")
        )

        items = []

        for client in clients:
            try:
                sub = client.subscription
                text = (
                    f"#{client.id}: "
                    f"{sub.plan.name} — "
                    f"{sub.get_status_display()}"
                )

                if sub.expires_at:
                    text += (
                        f" — до "
                        f"{sub.expires_at:%d.%m.%Y}"
                    )

                items.append(text)

            except Exception:
                items.append(
                    f"#{client.id}: без параметров подписки"
                )

        url = (
            reverse("admin:portal_client_changelist")
            + f"?owner__id__exact={obj.user_id}"
        )

        summary = " | ".join(items) if items else "Нет подписок"

        return format_html(
            '{}<br><a href="{}">Открыть VPN-подписки →</a>',
            summary,
            url,
        )


    @admin.display(description="Устройства")
    def devices_detail(self, obj):
        qs = Device.objects.filter(
            client__owner=obj.user,
        )

        total = qs.count()

        active = qs.filter(
            is_active=True,
            unlinked_at__isnull=True,
        ).count()

        url = (
            reverse("admin:portal_device_changelist")
            + f"?client__owner__id__exact={obj.user_id}"
        )

        return format_html(
            '{} активных / {} всего'
            '<br><a href="{}">Открыть устройства →</a>',
            active,
            total,
            url,
        )


    @admin.display(description="Заказы")
    def orders_detail(self, obj):
        from .models import Order

        qs = Order.objects.filter(
            user=obj.user
        )

        total = qs.count()

        paid = qs.filter(
            status=Order.STATUS_PAID,
        ).count()

        url = (
            reverse("admin:portal_order_changelist")
            + f"?user__id__exact={obj.user_id}"
        )

        return format_html(
            '{} всего / {} оплачено'
            '<br><a href="{}">Открыть заказы →</a>',
            total,
            paid,
            url,
        )


    @admin.display(description="Дата регистрации")
    def date_joined_display(self, obj):
        return obj.user.date_joined

    @admin.display(description="Пользователь Django")
    def django_user_link(self, obj):
        url = reverse(
            "admin:auth_user_change",
            args=[obj.user_id],
        )

        return format_html(
            '<a href="{}">Открыть пользователя #{}</a>',
            url,
            obj.user_id,
        )


    @admin.display(description="Последний вход")
    def last_login_display(self, obj):
        return obj.user.last_login


class ClientAdminForm(forms.ModelForm):

    subscription_expires_at = forms.DateTimeField(
        label="Действует до",
        required=False,
        widget=forms.DateTimeInput(
            attrs={
                "type": "datetime-local",
            },
            format="%Y-%m-%dT%H:%M",
        ),
        input_formats=[
            "%Y-%m-%dT%H:%M",
            "%d.%m.%Y %H:%M",
            "%d.%m.%Y %H:%M:%S",
        ],
    )

    class Meta:
        model = Client
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk:
            try:
                self.fields[
                    "subscription_expires_at"
                ].initial = (
                    self.instance.subscription.expires_at
                )
            except Exception:
                pass


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):

    form = ClientAdminForm

    list_display = (
        "owner_display",
        "plan_display",
        "remnawave_display",
        "status_display",
        "expires_display",
        "devices_display",
        "created_at",
    )

    list_filter = (
        "is_active",
        "remnawave_server",
        "subscription__status",
        "subscription__plan",
    )

    search_fields = (
        "owner__username",
        "owner__email",
        "name",
        "username",
    )

    readonly_fields = (
        "owner",
        "remnawave_display",
        "plan_detail",
        "status_detail",
        "starts_detail",
        "expires_detail",
        "devices_detail",
        "username",
        "name",
        "token",
        "remnawave_uuid",
        "remnawave_short_uuid",
        "is_active",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "VPN-подписка",
            {
                "fields": (
                    "owner",
                    "remnawave_display",
                    "plan_detail",
                    "status_detail",
                    "starts_detail",
                    "subscription_expires_at",
                    "devices_detail",
                    "allow_full_config",
                ),
            },
        ),
        (
            "Служебная информация",
            {
                "classes": ("collapse",),
                "fields": (
                    "username",
                    "name",
                    "token",
                    "remnawave_uuid",
                    "remnawave_short_uuid",
                    "is_active",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )

    inlines = (
        DeviceInline,
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related(
                "owner",
                "remnawave_server",
                "subscription",
                "subscription__plan",
            )
            .prefetch_related("devices")
        )

    @admin.display(description="Владелец")
    def owner_display(self, obj):
        if not obj.owner:
            return "—"

        return (
            obj.owner.email
            or obj.owner.username
            or f"Пользователь #{obj.owner_id}"
        )

    @admin.display(
        description="Сервер Remnawave",
        ordering="remnawave_server__name",
    )
    def remnawave_display(self, obj):
        if not obj.remnawave_server:
            return "—"

        return obj.remnawave_server.name

    @admin.display(description="Тариф")
    def plan_display(self, obj):
        try:
            return obj.subscription.plan.name
        except Exception:
            return "—"

    @admin.display(description="Статус")
    def status_display(self, obj):
        try:
            return obj.subscription.get_status_display()
        except Exception:
            return "—"

    @admin.display(description="Действует до")
    def expires_display(self, obj):
        try:
            return obj.subscription.expires_at
        except Exception:
            return obj.expires_at

    @admin.display(description="Устройства")
    def devices_display(self, obj):
        try:
            limit = obj.subscription.plan.device_limit
        except Exception:
            limit = 0

        active = sum(
            1
            for device in obj.devices.all()
            if device.is_active
        )

        return f"{active} из {limit}"

    @admin.display(description="Тариф")
    def plan_detail(self, obj):
        return self.plan_display(obj)

    @admin.display(description="Статус подписки")
    def status_detail(self, obj):
        return self.status_display(obj)

    @admin.display(description="Начало подписки")
    def starts_detail(self, obj):
        try:
            return obj.subscription.starts_at
        except Exception:
            return None

    @admin.display(description="Действует до")
    def expires_detail(self, obj):
        return self.expires_display(obj)

    @admin.display(description="Использовано мест")
    def devices_detail(self, obj):
        return self.devices_display(obj)


    def save_model(self, request, obj, form, change):
        """
        Сохраняет срок подписки из карточки Client.

        Источником срока в Django является Subscription.
        Client.expires_at поддерживается синхронно.
        Remnawave обновляется отдельным двусторонним
        синхронизатором.
        """

        super().save_model(
            request,
            obj,
            form,
            change,
        )

        if "subscription_expires_at" not in form.cleaned_data:
            return

        new_expire = form.cleaned_data[
            "subscription_expires_at"
        ]

        try:
            subscription = obj.subscription
        except Exception:
            return

        old_expire = subscription.expires_at

        # Ничего не трогаем, если дата реально
        # не изменилась.
        if old_expire == new_expire:
            return

        subscription.expires_at = new_expire

        subscription.save(
            update_fields=[
                "expires_at",
                "updated_at",
            ]
        )

        obj.expires_at = new_expire

        obj.save(
            update_fields=[
                "expires_at",
                "updated_at",
            ]
        )



# =========================================================
# ORDERS
# =========================================================

from .models import Order, Payment, Plan, Subscription
from .services import (
    activate_paid_order,
    OrderActivationError,
)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):

    list_display = (
        "id",
        "user",
        "plan",
        "order_type",
        "amount",
        "status",
        "created_at",
        "paid_at",
    )

    list_filter = (
        "status",
        "order_type",
        "plan",
        "created_at",
    )

    search_fields = (
        "id",
        "user__username",
        "user__email",
        "client__username",
    )

    readonly_fields = (
        "user",
        "plan",
        "client",
        "order_type",
        "amount",
        "duration_days",
        "device_limit",
        "status",
        "created_at",
        "paid_at",
        "updated_at",
    )

    actions = (
        "confirm_payment",
        "mark_as_refused",
    )

    @admin.action(
        description="✓ Оплата получена — активировать"
    )
    def confirm_payment(self, request, queryset):

        success = 0
        skipped = 0
        errors = 0

        for order in queryset:

            if order.status == Order.STATUS_PAID:
                skipped += 1
                continue

            if order.status == Order.STATUS_CANCELLED:
                self.message_user(
                    request,
                    (
                        f"Заказ #{order.id}: "
                        "заказ отменён."
                    ),
                    level=messages.WARNING,
                )
                skipped += 1
                continue

            try:
                result = activate_paid_order(
                    order.id
                )

                success += 1

                self.message_user(
                    request,
                    (
                        f"Заказ #{result.id}: "
                        "оплата подтверждена, "
                        "подписка активирована."
                    ),
                    level=messages.SUCCESS,
                )

            except Exception as exc:
                errors += 1

                self.message_user(
                    request,
                    (
                        f"Заказ #{order.id}: "
                        f"{exc}"
                    ),
                    level=messages.ERROR,
                )

        if skipped:
            self.message_user(
                request,
                (
                    f"Пропущено заказов: {skipped}. "
                    "Они уже оплачены или отменены."
                ),
                level=messages.WARNING,
            )

        if errors:
            self.message_user(
                request,
                f"Ошибок активации: {errors}.",
                level=messages.ERROR,
            )


    @admin.action(
        description="✕ Отметить как отказ"
    )
    def mark_as_refused(self, request, queryset):
        """
        Переводит только неоплаченные заказы в отказ.

        Оплаченные заказы изменять запрещено.
        Физического удаления Order не происходит.
        """

        success = 0
        skipped_paid = 0
        skipped_refused = 0
        skipped_protected = 0

        for order in queryset:

            if order.status == Order.STATUS_PAID:
                skipped_paid += 1
                continue

            if order.status == Order.STATUS_CANCELLED:
                skipped_refused += 1
                continue

            from .order_expiration import can_cancel_order

            if not can_cancel_order(order):
                skipped_protected += 1
                continue

            order.status = Order.STATUS_CANCELLED

            order.save(
                update_fields=[
                    "status",
                    "updated_at",
                ]
            )

            success += 1

        if success:
            self.message_user(
                request,
                f"Переведено в статус «Отказ»: {success}.",
                level=messages.SUCCESS,
            )

        if skipped_paid:
            self.message_user(
                request,
                (
                    f"Оплаченных заказов пропущено: "
                    f"{skipped_paid}. "
                    "Оплаченный заказ нельзя перевести в отказ."
                ),
                level=messages.WARNING,
            )

        if skipped_refused:
            self.message_user(
                request,
                (
                    f"Уже находились в статусе «Отказ»: "
                    f"{skipped_refused}."
                ),
                level=messages.WARNING,
            )


    # -----------------------------------------------------
    # Order является финансовой историей.
    # Физическое удаление через Admin запрещено.
    # -----------------------------------------------------

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "price",
        "duration_days",
        "device_limit",
        "is_active",
        "sort_order",
    )

    list_editable = (
        "price",
        "is_active",
        "sort_order",
    )

    search_fields = (
        "name",
        "code",
    )


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):

    def get_model_perms(self, request):
        """
        Техническая модель остаётся зарегистрированной,
        но не показывается отдельным пунктом меню.
        """
        return {}

    list_display = (
        "client",
        "plan",
        "status",
        "starts_at",
        "expires_at",
        "auto_renew",
    )

    list_filter = (
        "status",
        "plan",
        "auto_renew",
    )

    search_fields = (
        "client__username",
        "client__name",
        "client__owner__username",
        "client__owner__email",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):

    list_display = (
        "id",
        "order",
        "amount",
        "provider",
        "status",
        "created_at",
        "paid_at",
    )

    list_filter = (
        "status",
        "provider",
    )

    search_fields = (
        "provider_payment_id",
        "idempotency_key",
        "order__user__username",
        "order__user__email",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )


# =========================================================
# VPN DEVICES
# =========================================================

from .services import (
    disable_device,
    enable_device,
    DeviceDisableError,
    DeviceEnableError,
)


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):

    list_display = (
        "id",
        "owner_display",
        "subscription_display",
        "name",
        "platform",
        "status_display",
        "expires_display",
        "remnawave_username",
        "remnawave_user_id",
    )

    list_filter = (
        "is_active",
        "unlinked_at",
        "platform",
        "client__owner",
        "client__subscription__plan",
    )

    search_fields = (
        "name",
        "client__owner__username",
        "client__owner__email",
        "client__username",
        "remnawave_username",
        "remnawave_uuid",
    )

    readonly_fields = (
        "client",
        "owner_display",
        "subscription_display",
        "status_display",
        "expires_display",
        "token",
        "remnawave_username",
        "remnawave_user_id",
        "remnawave_uuid",
        "remnawave_short_uuid",
        "is_active",
        "unlinked_at",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Устройство",
            {
                "fields": (
                    "client",
                    "owner_display",
                    "subscription_display",
                    "name",
                    "platform",
                    "status_display",
                    "expires_display",
                    "unlinked_at",
                ),
            },
        ),
        (
            "Remnawave",
            {
                "fields": (
                    "remnawave_username",
                    "remnawave_user_id",
                    "remnawave_uuid",
                    "remnawave_short_uuid",
                ),
            },
        ),
        (
            "Техническая информация",
            {
                "classes": ("collapse",),
                "fields": (
                    "token",
                    "is_active",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )

    actions = (
        "disable_selected_devices",
        "enable_selected_devices",
    )

    @admin.display(description="Статус")
    def status_display(self, obj):
        if obj.unlinked_at is not None:
            return "Отвязано пользователем"

        if obj.is_active:
            return "Активно"

        return "Отключено администратором"


    @admin.display(description="Владелец")
    def owner_display(self, obj):
        owner = obj.client.owner

        if not owner:
            return "—"

        return owner.email or owner.username

    @admin.display(description="Подписка")
    def subscription_display(self, obj):
        try:
            return obj.client.subscription.plan.name
        except Exception:
            return "—"

    @admin.display(description="Действует до")
    def expires_display(self, obj):
        try:
            return obj.client.subscription.expires_at
        except Exception:
            return None

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related(
                "client",
                "client__owner",
                "client__subscription",
                "client__subscription__plan",
            )
        )

    @admin.action(
        description="Отключить выбранные устройства"
    )
    def disable_selected_devices(self, request, queryset):

        success = 0
        skipped = 0
        errors = 0

        for device in queryset:

            if device.unlinked_at is not None:
                skipped += 1
                continue

            if not device.is_active:
                skipped += 1
                continue

            try:
                disable_device(device)
                success += 1

            except Exception as exc:
                errors += 1

                self.message_user(
                    request,
                    (
                        f"Устройство #{device.id} "
                        f"«{device.name}»: {exc}"
                    ),
                    level=messages.ERROR,
                )

        if success:
            self.message_user(
                request,
                f"Отключено устройств: {success}.",
                level=messages.SUCCESS,
            )

        if skipped:
            self.message_user(
                request,
                (
                    f"Уже были отключены: "
                    f"{skipped}."
                ),
                level=messages.WARNING,
            )

        if errors:
            self.message_user(
                request,
                f"Ошибок: {errors}.",
                level=messages.ERROR,
            )


    @admin.action(
        description="Включить выбранные устройства"
    )
    def enable_selected_devices(self, request, queryset):

        success = 0
        skipped = 0
        errors = 0

        for device in queryset:

            if device.unlinked_at is not None:
                skipped += 1

                self.message_user(
                    request,
                    (
                        f"Устройство #{device.id} "
                        f"«{device.name}» окончательно отвязано "
                        "и не может быть включено повторно."
                    ),
                    level=messages.WARNING,
                )

                continue

            if device.is_active:
                skipped += 1
                continue

            try:
                enable_device(device)
                success += 1

            except Exception as exc:
                errors += 1

                self.message_user(
                    request,
                    (
                        f"Устройство #{device.id} "
                        f"«{device.name}»: {exc}"
                    ),
                    level=messages.ERROR,
                )

        if success:
            self.message_user(
                request,
                f"Включено устройств: {success}.",
                level=messages.SUCCESS,
            )

        if skipped:
            self.message_user(
                request,
                (
                    f"Уже были включены: "
                    f"{skipped}."
                ),
                level=messages.WARNING,
            )

        if errors:
            self.message_user(
                request,
                f"Ошибок: {errors}.",
                level=messages.ERROR,
            )



# =========================================================
# REMNAWAVE NODES
# =========================================================

@admin.register(RemnawaveNode)
class RemnawaveNodeAdmin(admin.ModelAdmin):
    change_form_template = "admin/portal/remnawavenode/change_form.html"


    change_list_template = (
        "admin/portal/remnawavenode/change_list.html"
    )

    actions = (
        "sync_nodes_action",
    )


    list_display = (
        "name",
        "server_display",
        "country_code",
        "address",
        "connection_display",
        "users_online",
        "xray_version",
        "node_version",
        "last_sync_at",
    )

    list_filter = (
        "server",
        "country_code",
        "is_connected",
        "is_enabled",
    )

    search_fields = (
        "name",
        "address",
        "server__name",
        "server__code",
    )

    readonly_fields = (
        "server",
        "name",
        "address",
        "country_code",
        "node_port",
        "vpn_port",
        "remnawave_uuid",
        "remnawave_id",
        "config_profile_uuid",
        "inbound_uuid",
        "host_uuid",
        "is_enabled",
        "is_connected",
        "last_status_message",
        "xray_uptime",
        "xray_version",
        "node_version",
        "users_online",
        "last_sync_at",
        "created_at",
        "updated_at",
        "reconcile_link",
        "install_script_link",
    )

    fieldsets = (
        (
            "Нода",
            {
                "fields": (
                    "server",
                    "name",
                    "address",
                    "country_code",
                    "node_port",
                    "vpn_port",
                    "is_enabled",
                    "is_connected",
                ),
            },
        ),
        (
            "Состояние",
            {
                "fields": (
                    "users_online",
                    "xray_uptime",
                    "xray_version",
                    "node_version",
                    "last_status_message",
                    "last_sync_at",
                ),
            },
        ),
        (
            "Remnawave",
            {
                "classes": ("collapse",),
                "fields": (
                    "remnawave_id",
                    "remnawave_uuid",
                    "config_profile_uuid",
                    "inbound_uuid",
                    "host_uuid",
                ),
            },
        ),
        (
            "Служебная информация",
            {
                "classes": ("collapse",),
                "fields": (
                    "reconcile_link",
                    "install_script_link",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )

    def get_urls(self):
        urls = super().get_urls()

        custom_urls = [
            path(
                "create-node/",
                self.admin_site.admin_view(
                    self.create_node_view
                ),
                name="portal_remnawavenode_create",
            ),
            path(
                "<int:object_id>/install-script/",
                self.admin_site.admin_view(
                    self.install_script_view
                ),
                name="portal_remnawavenode_install_script",
            ),
            path(
                "<int:object_id>/reconcile/",
                self.admin_site.admin_view(
                    self.reconcile_node_view
                ),
                name="portal_remnawavenode_reconcile",
            ),
            path(
                "<int:object_id>/node-lifecycle/",
                self.admin_site.admin_view(
                    self.node_lifecycle_view
                ),
                name="portal_remnawavenode_lifecycle",
            ),
            path(
                "<int:object_id>/remove-remnawave-node/",
                self.admin_site.admin_view(
                    self.delete_node_view
                ),
                name="portal_remnawavenode_remote_delete",
            ),
        ]

        return custom_urls + urls

    def create_node_view(self, request):
        if request.method == "POST":
            form = RemnawaveNodeCreateForm(request.POST)

            if form.is_valid():
                try:
                    result = create_remnawave_node(
                        server=form.cleaned_data["server"],
                        name=form.cleaned_data["name"],
                        address=form.cleaned_data["address"],
                        country_code=(
                            form.cleaned_data["country_code"]
                        ),
                    )

                except RemnawaveNodeCreateError as exc:
                    messages.error(
                        request,
                        str(exc),
                    )

                except Exception as exc:
                    messages.error(
                        request,
                        f"Ошибка создания ноды: {exc}",
                    )

                else:
                    messages.success(
                        request,
                        (
                            f"Нода {result.node.name} "
                            "успешно создана."
                        ),
                    )

                    context = {
                        **self.admin_site.each_context(request),
                        "title": "Нода создана",
                        "node": result.node,
                        "install_script": result.install_script,
                    }

                    return render(
                        request,
                        (
                            "admin/portal/remnawavenode/"
                            "create_result.html"
                        ),
                        context,
                    )

        else:
            form = RemnawaveNodeCreateForm()

        context = {
            **self.admin_site.each_context(request),
            "title": "Создать ноду Remnawave",
            "form": form,
        }

        return render(
            request,
            "admin/portal/remnawavenode/create_node.html",
            context,
        )

    @admin.display(
        description="Remnawave"
    )
    def reconcile_link(self, obj):
        if not obj or not obj.pk:
            return "-"

        url = reverse(
            "admin:portal_remnawavenode_reconcile",
            args=[obj.pk],
        )

        return format_html(
            '<a class="button" href="{}">'
            'Восстановить / проверить в Remnawave'
            '</a>',
            url,
        )

    def reconcile_node_view(self, request, object_id):
        from django.http import Http404
        from django.shortcuts import redirect

        node = self.get_object(request, object_id)

        if node is None:
            raise Http404("Нода не найдена.")

        try:
            result = reconcile_remnawave_node(node)

        except RemnawaveNodeCreateError as exc:
            messages.error(
                request,
                f"Восстановление не выполнено: {exc}",
            )

        except Exception as exc:
            messages.error(
                request,
                f"Ошибка восстановления ноды: {exc}",
            )

        else:
            if result.node_created:
                node_text = "Node создана"
            else:
                node_text = (
                    "Node уже существует "
                    f"(найдена по {result.node_found_by})"
                )

            if result.host_created:
                host_text = "Host создан"
            else:
                host_text = (
                    "Host уже существует "
                    f"(найден по {result.host_found_by})"
                )

            messages.success(
                request,
                (
                    f"{node_text}. {host_text}. "
                    "Связи Django обновлены. "
                    "Переустановка VPS не требуется."
                ),
            )

        return redirect(
            reverse(
                "admin:portal_remnawavenode_change",
                args=[node.pk],
            )
        )


    @admin.display(
        description="Установочный скрипт"
    )
    def install_script_link(self, obj):
        if not obj or not obj.pk:
            return "-"

        url = reverse(
            "admin:portal_remnawavenode_install_script",
            args=[obj.pk],
        )

        return format_html(
            '<a class="button" href="{}">'
            'Скачать install.sh'
            '</a>',
            url,
        )

    def install_script_view(self, request, object_id):
        node = self.get_object(request, object_id)

        if node is None:
            from django.http import Http404
            raise Http404("Нода не найдена.")

        try:
            script = build_install_script(node.server)
        except RemnawaveNodeCreateError as exc:
            messages.error(request, str(exc))

            from django.shortcuts import redirect

            return redirect(
                reverse(
                    "admin:portal_remnawavenode_change",
                    args=[node.pk],
                )
            )

        response = HttpResponse(
            script,
            content_type="text/x-shellscript; charset=utf-8",
        )

        safe_name = (
            (node.name or "remnanode")
            .replace("/", "-")
            .replace("\\", "-")
            .replace(" ", "-")
        )

        response["Content-Disposition"] = (
            f'attachment; filename="{safe_name}-install.sh"'
        )

        return response

    def change_view(
        self,
        request,
        object_id,
        form_url="",
        extra_context=None,
    ):
        extra_context = extra_context or {}

        obj = self.get_object(request, object_id)

        if obj is not None:
            extra_context["node_lifecycle_object"] = obj

        return super().change_view(
            request,
            object_id,
            form_url=form_url,
            extra_context=extra_context,
        )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("server")
        )

    @admin.display(
        description="Remnawave",
        ordering="server__priority",
    )
    def server_display(self, obj):
        return obj.server.name

    @admin.display(
        description="Состояние",
        boolean=True,
        ordering="is_connected",
    )
    def connection_display(self, obj):
        return obj.is_connected


    @admin.action(
        description="Синхронизировать ноды со всех серверов Remnawave"
    )
    def sync_nodes_action(self, request, queryset):
        result = sync_remnawave_nodes()

        errors = len(result["errors"])

        if errors:
            level = messages.WARNING
        else:
            level = messages.SUCCESS

        self.message_user(
            request,
            (
                f'Синхронизация завершена. '
                f'Серверов: {result["servers"]}; '
                f'создано: {result["created"]}; '
                f'обновлено: {result["updated"]}; '
                f'ошибок: {errors}.'
            ),
            level=level,
        )

        for error in result["errors"]:
            self.message_user(
                request,
                (
                    f'{error["server"]}: '
                    f'{error["error"]}'
                ),
                level=messages.ERROR,
            )

    def node_lifecycle_view(
        self,
        request,
        object_id,
    ):
        node = self.get_object(request, object_id)

        if node is None:
            self.message_user(
                request,
                "Нода не найдена.",
                level=messages.ERROR,
            )
            return redirect(
                "admin:portal_remnawavenode_changelist"
            )

        target_enabled = not node.is_enabled

        try:
            result = set_remnawave_node_enabled(
                node,
                target_enabled,
            )

            if target_enabled:
                text = "Нода возвращена в эксплуатацию."
            else:
                text = "Нода выведена из эксплуатации."

            self.message_user(
                request,
                (
                    f"{text} "
                    f"Node изменён: "
                    f"{'да' if result.node_changed else 'нет'}; "
                    f"Host изменён: "
                    f"{'да' if result.host_changed else 'нет'}."
                ),
                level=messages.SUCCESS,
            )

        except Exception as exc:
            self.message_user(
                request,
                str(exc),
                level=messages.ERROR,
            )

        return redirect(
            "admin:portal_remnawavenode_change",
            node.pk,
        )

    def delete_node_view(
        self,
        request,
        object_id,
    ):
        from django.http import Http404

        node = self.get_object(request, object_id)

        if node is None:
            raise Http404("Нода не найдена.")

        # Сохраняем данные до возможного node.delete()
        node_name = node.name
        server_name = node.server.name
        address = node.address

        if request.method == "POST":
            try:
                result = delete_remnawave_node(node)

            except RemnawaveNodeDeleteError as exc:
                self.message_user(
                    request,
                    str(exc),
                    level=messages.ERROR,
                )

                return redirect(
                    "admin:portal_remnawavenode_change",
                    object_id,
                )

            except Exception as exc:
                self.message_user(
                    request,
                    f"Ошибка удаления ноды: {exc}",
                    level=messages.ERROR,
                )

                return redirect(
                    "admin:portal_remnawavenode_change",
                    object_id,
                )

            self.message_user(
                request,
                (
                    f"Нода {node_name} удалена из управления. "
                    f"Сервер Remnawave: {server_name}. "
                    f"Host удалён: "
                    f"{'да' if result.remote_host_deleted else 'не существовал'}; "
                    f"Node удалена: "
                    f"{'да' if result.remote_node_deleted else 'не существовала'}; "
                    "локальная запись Django удалена. "
                    "VPS не изменялся."
                ),
                level=messages.SUCCESS,
            )

            return redirect(
                "admin:portal_remnawavenode_changelist"
            )

        try:
            state = inspect_remnawave_node_delete(node)

        except RemnawaveNodeDeleteError as exc:
            self.message_user(
                request,
                str(exc),
                level=messages.ERROR,
            )

            return redirect(
                "admin:portal_remnawavenode_change",
                object_id,
            )

        context = {
            **self.admin_site.each_context(request),
            "title": f"Удалить ноду {node_name}",
            "opts": self.model._meta,
            "node": node,
            "node_name": node_name,
            "server_name": server_name,
            "address": address,
            "remote_node": state["node"],
            "remote_host": state["host"],
        }

        return render(
            request,
            "admin/portal/remnawavenode/delete_confirm.html",
            context,
        )


    def has_add_permission(self, request):
        # Пока ноды создаются в Remnawave и импортируются в Django.
        return False

    def has_delete_permission(self, request, obj=None):
        # Защищаем от случайного удаления локальной записи.
        return False
