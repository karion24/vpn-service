import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError


def generate_token():
    return secrets.token_urlsafe(18)


class CustomerProfile(models.Model):
    CHANNEL_EMAIL = "email"
    CHANNEL_TELEGRAM = "telegram"
    CHANNEL_MAX = "max"
    CHANNEL_CHOICES = (
        (CHANNEL_EMAIL, "Электронный адрес"),
        (CHANNEL_TELEGRAM, "Telegram"),
        (CHANNEL_MAX, "Max"),
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="customer_profile",
    )

    phone = models.CharField(
        max_length=30,
        blank=True,
    )

    telegram = models.CharField(
        max_length=100,
        blank=True,
    )

    max_contact = models.CharField("Max", max_length=100, blank=True)
    primary_channel = models.CharField(
        "Основной канал", max_length=20, choices=CHANNEL_CHOICES,
        default=CHANNEL_EMAIL,
    )

    def notification_recipient(self):
        if self.primary_channel == self.CHANNEL_EMAIL:
            return (self.user.email or "").strip() if self.user_id else ""
        if self.primary_channel == self.CHANNEL_TELEGRAM:
            return (self.telegram or "").strip()
        if self.primary_channel == self.CHANNEL_MAX:
            return (self.max_contact or "").strip()
        return ""

    def clean(self):
        super().clean()
        if not self.notification_recipient():
            label = dict(self.CHANNEL_CHOICES).get(self.primary_channel, "Канал")
            raise ValidationError({
                "primary_channel": f"Нельзя выбрать {label}: контакт не заполнен."
            })

        if self.primary_channel in (self.CHANNEL_TELEGRAM, self.CHANNEL_MAX):
            from .notification_backends import recipient_id
            try:
                recipient_id(self.notification_recipient())
            except ValueError as exc:
                raise ValidationError({"primary_channel": str(exc)}) from None

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"

    def __str__(self):
        return self.user.email or self.user.username


# =========================================================
# REMNAWAVE SERVERS
# =========================================================

class RemnawaveServer(models.Model):
    """
    Управляющий сервер Remnawave.

    Один Django-портал может управлять несколькими
    независимыми панелями Remnawave.
    """

    name = models.CharField(
        "Название",
        max_length=100,
        unique=True,
    )

    code = models.SlugField(
        "Код",
        max_length=50,
        unique=True,
    )

    base_url = models.URLField(
        "Адрес панели",
        max_length=255,
    )

    api_token = models.TextField(
        "API-токен",
    )

    squad_uuid = models.CharField(
        "UUID внутренней группы",
        max_length=100,
        blank=True,
    )

    node_secret_key = models.TextField(
        "Node Secret Key",
        blank=True,
        help_text=(
            "SECRET_KEY для подключения Remnawave Node "
            "к этой панели."
        ),
    )

    is_active = models.BooleanField(
        "Активен",
        default=True,
    )

    accept_new_clients = models.BooleanField(
        "Принимать новые подключения",
        default=True,
    )

    priority = models.PositiveIntegerField(
        "Приоритет",
        default=100,
        help_text="Чем меньше число, тем выше приоритет.",
    )

    created_at = models.DateTimeField(
        "Создано",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        "Изменено",
        auto_now=True,
    )

    class Meta:
        verbose_name = "Сервер Remnawave"
        verbose_name_plural = "Серверы Remnawave"
        ordering = (
            "priority",
            "id",
        )

    def __str__(self):
        return self.name


class Plan(models.Model):
    name = models.CharField(
        "Название",
        max_length=100,
    )

    code = models.SlugField(
        "Код тарифа",
        max_length=50,
        unique=True,
    )

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    duration_days = models.PositiveIntegerField(
        "Срок действия, дней",
        default=30,
    )

    device_limit = models.PositiveIntegerField(
        "Количество устройств",
        default=1,
    )

    allow_full_config = models.BooleanField(
        "Разрешить полный VPN",
        default=False,
    )

    is_active = models.BooleanField(
        default=True,
    )

    sort_order = models.PositiveIntegerField(
        "Порядок сортировки",
        default=100,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        verbose_name = "Тариф"
        verbose_name_plural = "Тарифы"
        ordering = (
            "sort_order",
            "price",
            "id",
        )

    def __str__(self):
        return self.name


class Client(models.Model):
    remnawave_server = models.ForeignKey(
        RemnawaveServer,
        verbose_name="Сервер Remnawave",
        on_delete=models.PROTECT,
        related_name="vpn_clients",
        blank=True,
        null=True,
    )

    username = models.CharField(
        max_length=150,
        unique=True,
    )

    name = models.CharField(
        max_length=100,
        default="Мой VPN",
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="vpn_clients",
        blank=True,
        null=True,
    )

    token = models.CharField(
        max_length=100,
        unique=True,
        editable=False,
    )

    remnawave_uuid = models.CharField(
        max_length=100,
        blank=True,
        null=True,
    )

    remnawave_short_uuid = models.CharField(
        max_length=100,
        blank=True,
        null=True,
    )

    allow_full_config = models.BooleanField(
        "Разрешить полный VPN",
        default=False,
    )

    is_active = models.BooleanField(
        default=True,
    )

    expires_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        verbose_name = "VPN-подписка"
        verbose_name_plural = "VPN-подписки"

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = generate_token()

        super().save(*args, **kwargs)

    @property
    def subscription_is_active(self):
        from django.utils import timezone

        if not self.is_active:
            return False

        try:
            subscription = self.subscription
        except Subscription.DoesNotExist:
            return False

        return (
            subscription.status == Subscription.STATUS_ACTIVE
            and subscription.expires_at is not None
            and subscription.expires_at > timezone.now()
        )

    def __str__(self):
        return self.username


class Subscription(models.Model):
    STATUS_PENDING = "pending"
    STATUS_ACTIVE = "active"
    STATUS_EXPIRED = "expired"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = (
        (STATUS_PENDING, "Ожидает оплаты"),
        (STATUS_ACTIVE, "Активна"),
        (STATUS_EXPIRED, "Истекла"),
        (STATUS_CANCELLED, "Отменена"),
    )

    client = models.OneToOneField(
        Client,
        on_delete=models.CASCADE,
        related_name="subscription",
    )

    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name="subscriptions",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )

    starts_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    expires_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    auto_renew = models.BooleanField(
        default=False,
    )

    # Цена фиксируется в момент назначения тарифа.
    # Последующее изменение Plan.price не меняет
    # уже существующую подписку.
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    # Момент последней успешной синхронизации срока
    # подписки между Django и Remnawave.
    expires_synced_at = models.DateTimeField(
        blank=True,
        null=True,
        editable=False,
    )

    # Значение expires_at, которое было согласовано
    # между Django и Remnawave при последней синхронизации.
    expires_synced_value = models.DateTimeField(
        blank=True,
        null=True,
        editable=False,
    )

    class Meta:
        verbose_name = "Параметры подписки"
        verbose_name_plural = "Параметры подписок"

    def __str__(self):
        return f"{self.client} — {self.plan}"


# =========================================================
# BILLING
# =========================================================

class SubscriptionReminder(models.Model):
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE)
    expires_at = models.DateTimeField()
    channel = models.CharField(max_length=20, choices=CustomerProfile.CHANNEL_CHOICES)
    sent_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=("subscription", "expires_at"),
            name="unique_subscription_expiry_reminder",
        )]


class Order(models.Model):
    TYPE_NEW = "new"
    TYPE_RENEWAL = "renewal"

    TYPE_CHOICES = (
        (TYPE_NEW, "Новая подписка"),
        (TYPE_RENEWAL, "Продление"),
    )

    STATUS_PENDING = "pending"
    STATUS_PAID = "paid"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = (
        (STATUS_PENDING, "Не оплачен"),
        (STATUS_PAID, "Оплачен"),
        (STATUS_CANCELLED, "Отказ"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="vpn_orders",
    )

    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name="orders",
    )

    # Для продления указывает существующее
    # VPN-подключение. Для новой подписки = NULL.
    client = models.ForeignKey(
        Client,
        on_delete=models.SET_NULL,
        related_name="orders",
        blank=True,
        null=True,
    )

    order_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default=TYPE_NEW,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )

    # Фиксируем параметры покупки.
    # Изменение тарифа в будущем не должно
    # менять уже созданный заказ.
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    duration_days = models.PositiveIntegerField()

    device_limit = models.PositiveIntegerField()

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    payment_expires_at = models.DateTimeField(
        blank=True,
        null=True,
        db_index=True,
        verbose_name="Оплатить до",
    )

    paid_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    FULFILLMENT_PENDING = "pending"
    FULFILLMENT_ACTIVATION_PENDING = "activation_pending"
    FULFILLMENT_SYNC_PENDING = "sync_pending"
    FULFILLMENT_COMPLETED = "completed"
    FULFILLMENT_ERROR = "error"

    FULFILLMENT_CHOICES = (
        (FULFILLMENT_PENDING, "Ожидает исполнения"),
        (FULFILLMENT_ACTIVATION_PENDING, "Ожидает активации"),
        (FULFILLMENT_SYNC_PENDING, "Ожидает синхронизации"),
        (FULFILLMENT_COMPLETED, "Исполнен"),
        (FULFILLMENT_ERROR, "Ошибка исполнения"),
    )

    fulfillment_status = models.CharField(
        max_length=30,
        choices=FULFILLMENT_CHOICES,
        default=FULFILLMENT_PENDING,
        db_index=True,
    )


    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        verbose_name = "Заказ"
        verbose_name_plural = "Заказы"
        ordering = ("-created_at",)

    def __str__(self):
        return (
            f"Заказ #{self.pk} — "
            f"{self.user} — {self.amount}"
        )


class Payment(models.Model):
    STATUS_PENDING = "pending"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_REFUNDED = "refunded"

    STATUS_CHOICES = (
        (STATUS_PENDING, "Ожидает"),
        (STATUS_SUCCEEDED, "Оплачен"),
        (STATUS_FAILED, "Ошибка"),
        (STATUS_CANCELLED, "Отменён"),
        (STATUS_REFUNDED, "Возвращён"),
    )

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="payments",
    )

    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )

    # Позволит менять платёжного провайдера
    # без изменения структуры БД.
    provider = models.CharField(
        max_length=50,
        blank=True,
    )

    # UUID счёта в едином платёжном шлюзе.
    # Не путать с ID отдельной платёжной операции.
    gateway_invoice_id = models.UUIDField(
        blank=True,
        null=True,
        db_index=True,
    )

    provider_payment_id = models.CharField(
        max_length=200,
        blank=True,
        db_index=True,
    )

    # Ключ идемпотентности.
    # Защищает от повторного создания операции.
    idempotency_key = models.CharField(
        max_length=100,
        unique=True,
    )

    paid_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        verbose_name = "Платёж"
        verbose_name_plural = "Платежи"
        ordering = ("-created_at",)

    def __str__(self):
        return (
            f"Платёж #{self.pk} — "
            f"заказ #{self.order_id} — "
            f"{self.amount}"
        )


# =========================================================
# BILLING


# =========================================================
# DEVICES
# =========================================================

class Device(models.Model):
    PLATFORM_IOS = "ios"
    PLATFORM_ANDROID = "android"
    PLATFORM_WINDOWS = "windows"
    PLATFORM_OTHER = "other"

    PLATFORM_CHOICES = (
        (PLATFORM_IOS, "iPhone / iPad"),
        (PLATFORM_ANDROID, "Android"),
        (PLATFORM_WINDOWS, "Windows"),
        (PLATFORM_OTHER, "Другое"),
    )

    client = models.ForeignKey(
        Client,
        verbose_name="VPN-подписка",
        on_delete=models.CASCADE,
        related_name="devices",
    )

    name = models.CharField(
        "Устройство",
        max_length=100,
        default="Моё устройство",
    )

    platform = models.CharField(
        "Тип устройства",
        max_length=20,
        choices=PLATFORM_CHOICES,
        default=PLATFORM_OTHER,
    )

    token = models.CharField(
        "Токен подключения",
        max_length=100,
        unique=True,
        editable=False,
    )

    # Каждый Device получает отдельного пользователя Remnawave.
    remnawave_username = models.CharField(
        "Пользователь Remnawave",
        max_length=150,
        unique=True,
        blank=True,
        null=True,
    )

    remnawave_user_id = models.PositiveIntegerField(
        "ID пользователя Remnawave",
        unique=True,
        blank=True,
        null=True,
    )

    remnawave_uuid = models.CharField(
        "UUID Remnawave",
        max_length=100,
        unique=True,
        blank=True,
        null=True,
    )

    remnawave_short_uuid = models.CharField(
        "Short UUID Remnawave",
        max_length=100,
        unique=True,
        blank=True,
        null=True,
    )

    is_active = models.BooleanField(
        "Активно",
        default=True,
    )

    # Если заполнено — устройство окончательно отвязано
    # пользователем. Remnawave User для него удалён,
    # повторное включение такого устройства запрещено.
    unlinked_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name="Отвязано",
    )

    windows_package = models.CharField(
        "Windows пакет",
        max_length=255,
        blank=True,
        null=True,
    )

    windows_package_created_at = models.DateTimeField(
        "Дата создания Windows пакета",
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(
        "Создано",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        "Изменено",
        auto_now=True,
    )

    class Meta:
        verbose_name = "Устройство VPN"
        verbose_name_plural = "Устройства VPN"

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = generate_token()

        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


# =========================================================
# REMNAWAVE NODES
# =========================================================

class RemnawaveNode(models.Model):
    """
    Нода, принадлежащая конкретному серверу Remnawave.

    Django хранит локальное представление ноды.
    Фактическое состояние синхронизируется с Remnawave API.
    """

    server = models.ForeignKey(
        RemnawaveServer,
        on_delete=models.PROTECT,
        related_name="nodes",
        verbose_name="Сервер Remnawave",
    )

    name = models.CharField(
        "Название",
        max_length=100,
    )

    address = models.GenericIPAddressField(
        "IP-адрес",
        protocol="IPv4",
    )

    country_code = models.CharField(
        "Страна",
        max_length=2,
        blank=True,
    )

    node_port = models.PositiveIntegerField(
        "Порт управления",
        default=2222,
    )

    vpn_port = models.PositiveIntegerField(
        "VPN-порт",
        default=443,
    )

    remnawave_uuid = models.UUIDField(
        "UUID ноды Remnawave",
        null=True,
        blank=True,
        unique=True,
    )

    remnawave_id = models.PositiveIntegerField(
        "ID ноды Remnawave",
        null=True,
        blank=True,
    )

    config_profile_uuid = models.UUIDField(
        "UUID Config Profile",
        null=True,
        blank=True,
    )

    inbound_uuid = models.UUIDField(
        "UUID Inbound",
        null=True,
        blank=True,
    )

    host_uuid = models.UUIDField(
        "UUID Host",
        null=True,
        blank=True,
    )

    is_enabled = models.BooleanField(
        "Используется",
        default=True,
    )

    is_connected = models.BooleanField(
        "Подключена",
        default=False,
    )

    last_status_message = models.TextField(
        "Последнее сообщение состояния",
        blank=True,
    )

    xray_uptime = models.BigIntegerField(
        "Xray uptime",
        default=0,
    )

    xray_version = models.CharField(
        "Версия Xray",
        max_length=50,
        blank=True,
    )

    node_version = models.CharField(
        "Версия Remnawave Node",
        max_length=50,
        blank=True,
    )

    users_online = models.PositiveIntegerField(
        "Пользователей онлайн",
        default=0,
    )

    last_sync_at = models.DateTimeField(
        "Последняя синхронизация",
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        "Создано",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        "Изменено",
        auto_now=True,
    )

    class Meta:
        verbose_name = "Нода Remnawave"
        verbose_name_plural = "Ноды Remnawave"
        ordering = (
            "server__priority",
            "server_id",
            "name",
        )

        constraints = (
            models.UniqueConstraint(
                fields=("server", "name"),
                name="unique_remnawave_node_name_per_server",
            ),
            models.UniqueConstraint(
                fields=("server", "address"),
                name="unique_remnawave_node_address_per_server",
            ),
        )

    def __str__(self):
        return f"{self.server.code} / {self.name} / {self.address}"
