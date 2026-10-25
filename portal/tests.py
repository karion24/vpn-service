from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import Client, Order, Plan, Subscription


class OrderPaymentDeadlineTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="payment_test_user",
            password="test-password",
        )

        self.plan = Plan.objects.create(
            name="Тестовый VPN",
            code="test-vpn",
            price=Decimal("300.00"),
            duration_days=30,
            device_limit=2,
        )

    def test_new_order_deadline_and_no_activation(self):
        before = timezone.now()

        order = Order.objects.create(
            user=self.user,
            plan=self.plan,
            order_type=Order.TYPE_NEW,
            status=Order.STATUS_PENDING,
            payment_expires_at=(
                timezone.now() + timedelta(minutes=10)
            ),
            amount=self.plan.price,
            duration_days=self.plan.duration_days,
            device_limit=self.plan.device_limit,
        )

        order.refresh_from_db()

        self.assertGreaterEqual(
            order.payment_expires_at,
            before + timedelta(minutes=10),
        )
        self.assertLess(
            order.payment_expires_at,
            before + timedelta(minutes=11),
        )
        self.assertEqual(order.status, Order.STATUS_PENDING)
        self.assertFalse(
            Client.objects.filter(owner=self.user).exists()
        )
        self.assertEqual(Subscription.objects.count(), 0)


class NewOrderHTTPTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="http_payment_test",
            password="test-password",
        )
        self.plan = Plan.objects.create(
            name="HTTP тест VPN",
            code="http-test-vpn",
            price=Decimal("450.00"),
            duration_days=30,
            device_limit=2,
        )
        self.client.force_login(self.user)

    def test_create_order_has_ten_minute_deadline(self):
        from django.urls import reverse

        before = timezone.now()

        response = self.client.post(
            reverse("create_order", args=[self.plan.pk]),
        )

        self.assertEqual(response.status_code, 302)

        order = Order.objects.get(user=self.user)

        self.assertEqual(order.order_type, Order.TYPE_NEW)
        self.assertEqual(order.status, Order.STATUS_PENDING)
        self.assertIsNotNone(order.payment_expires_at)

        self.assertGreaterEqual(
            order.payment_expires_at,
            before + timedelta(minutes=10),
        )
        self.assertLess(
            order.payment_expires_at,
            before + timedelta(minutes=11),
        )

        self.assertFalse(
            Client.objects.filter(owner=self.user).exists()
        )
        self.assertEqual(Subscription.objects.count(), 0)


class RenewalOrderHTTPTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="renewal_payment_test",
            password="test-password",
        )

        self.plan = Plan.objects.create(
            name="Тест продления VPN",
            code="renewal-test-vpn",
            price=Decimal("500.00"),
            duration_days=30,
            device_limit=3,
        )

        self.original_expires_at = (
            timezone.now() + timedelta(days=15)
        )

        self.vpn_client = Client.objects.create(
            username="renewal_test_client",
            owner=self.user,
            is_active=True,
            expires_at=self.original_expires_at,
        )

        self.subscription = Subscription.objects.create(
            client=self.vpn_client,
            plan=self.plan,
            status=Subscription.STATUS_ACTIVE,
            starts_at=timezone.now() - timedelta(days=15),
            expires_at=self.original_expires_at,
            price=self.plan.price,
        )

        self.client.force_login(self.user)

    def test_renewal_order_does_not_extend_subscription(self):
        from django.urls import reverse

        before = timezone.now()

        response = self.client.post(
            reverse(
                "renew_subscription",
                args=[self.vpn_client.pk],
            ),
        )

        self.assertEqual(response.status_code, 302)

        order = Order.objects.get(user=self.user)

        self.assertEqual(order.order_type, Order.TYPE_RENEWAL)
        self.assertEqual(order.status, Order.STATUS_PENDING)
        self.assertEqual(order.client_id, self.vpn_client.pk)

        self.assertIsNotNone(order.payment_expires_at)
        self.assertGreaterEqual(
            order.payment_expires_at,
            before + timedelta(minutes=10),
        )
        self.assertLess(
            order.payment_expires_at,
            before + timedelta(minutes=11),
        )

        self.vpn_client.refresh_from_db()
        self.subscription.refresh_from_db()

        self.assertEqual(
            self.vpn_client.expires_at,
            self.original_expires_at,
        )
        self.assertEqual(
            self.subscription.expires_at,
            self.original_expires_at,
        )
        self.assertEqual(
            self.subscription.status,
            Subscription.STATUS_ACTIVE,
        )
        self.assertEqual(Client.objects.count(), 1)
        self.assertEqual(Subscription.objects.count(), 1)


class CabinetPendingOrdersTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="cabinet_deadline_test",
            password="test-password",
        )

        self.plan = Plan.objects.create(
            name="Тест отображения заказов",
            code="cabinet-deadline-test",
            price=Decimal("450.00"),
            duration_days=30,
            device_limit=2,
        )

        self.client.force_login(self.user)

    def test_cabinet_hides_expired_orders_without_deleting_them(self):
        from django.urls import reverse

        now = timezone.now()

        valid_order = Order.objects.create(
            user=self.user,
            plan=self.plan,
            order_type=Order.TYPE_NEW,
            status=Order.STATUS_PENDING,
            payment_expires_at=now + timedelta(minutes=5),
            amount=self.plan.price,
            duration_days=self.plan.duration_days,
            device_limit=self.plan.device_limit,
        )

        expired_order = Order.objects.create(
            user=self.user,
            plan=self.plan,
            order_type=Order.TYPE_NEW,
            status=Order.STATUS_PENDING,
            payment_expires_at=now - timedelta(minutes=1),
            amount=self.plan.price,
            duration_days=self.plan.duration_days,
            device_limit=self.plan.device_limit,
        )

        response = self.client.get(reverse("cabinet"))

        self.assertEqual(response.status_code, 200)

        visible_ids = [
            order.id
            for order in response.context["pending_orders"]
        ]

        self.assertIn(valid_order.id, visible_ids)
        self.assertNotIn(expired_order.id, visible_ids)

        self.assertEqual(Order.objects.count(), 2)

        expired_order.refresh_from_db()
        self.assertEqual(
            expired_order.status,
            Order.STATUS_PENDING,
        )

        self.assertEqual(Client.objects.count(), 0)
        self.assertEqual(Subscription.objects.count(), 0)


class CabinetPaymentCardTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="payment_card_test",
            password="test-password",
        )

        self.plan = Plan.objects.create(
            name="Karion VPN Тестовый тариф",
            code="payment-card-test",
            price=Decimal("450.00"),
            duration_days=30,
            device_limit=2,
        )

        self.client.force_login(self.user)

    def test_pending_subscription_payment_card(self):
        from django.urls import reverse

        deadline = timezone.now() + timedelta(minutes=10)

        order = Order.objects.create(
            user=self.user,
            plan=self.plan,
            order_type=Order.TYPE_NEW,
            status=Order.STATUS_PENDING,
            payment_expires_at=deadline,
            amount=self.plan.price,
            duration_days=self.plan.duration_days,
            device_limit=self.plan.device_limit,
        )

        response = self.client.get(reverse("cabinet"))

        self.assertEqual(response.status_code, 200)

        self.assertContains(response, "Ожидают оплаты")
        self.assertContains(response, self.plan.name)
        self.assertContains(response, "Оплатить до:")
        self.assertContains(response, "Оплатить")

        self.assertContains(
            response,
            reverse("order_detail", args=[order.pk]),
        )

        self.assertNotContains(response, "Открыть заявку")
        self.assertNotContains(response, "<h2>Заявки</h2>")

        self.assertEqual(order.status, Order.STATUS_PENDING)
        self.assertEqual(Client.objects.count(), 0)
        self.assertEqual(Subscription.objects.count(), 0)


class OrderCabinetRedirectTests(TestCase):

    def setUp(self):
        from django.urls import reverse

        self.cabinet_url = reverse("cabinet")

        self.user = get_user_model().objects.create_user(
            username="redirect_payment_test",
            password="test-password",
        )

        self.plan = Plan.objects.create(
            name="VPN тест перенаправления",
            code="redirect-payment-test",
            price=Decimal("450.00"),
            duration_days=30,
            device_limit=2,
        )

        self.client.force_login(self.user)

    def test_new_order_redirects_to_cabinet(self):
        from django.urls import reverse

        response = self.client.post(
            reverse("create_order", args=[self.plan.pk]),
        )

        self.assertRedirects(
            response,
            self.cabinet_url,
            fetch_redirect_response=False,
        )

        order = Order.objects.get(user=self.user)

        self.assertEqual(order.status, Order.STATUS_PENDING)
        self.assertIsNotNone(order.payment_expires_at)
        self.assertEqual(Client.objects.count(), 0)
        self.assertEqual(Subscription.objects.count(), 0)

    def test_renewal_redirects_to_cabinet(self):
        from django.urls import reverse

        original_expiry = timezone.now() + timedelta(days=15)

        vpn_client = Client.objects.create(
            username="redirect_renewal_client",
            owner=self.user,
            is_active=True,
            expires_at=original_expiry,
        )

        subscription = Subscription.objects.create(
            client=vpn_client,
            plan=self.plan,
            status=Subscription.STATUS_ACTIVE,
            starts_at=timezone.now() - timedelta(days=15),
            expires_at=original_expiry,
            price=self.plan.price,
        )

        response = self.client.post(
            reverse("renew_subscription", args=[vpn_client.pk]),
        )

        self.assertRedirects(
            response,
            self.cabinet_url,
            fetch_redirect_response=False,
        )

        order = Order.objects.get(user=self.user)

        self.assertEqual(order.order_type, Order.TYPE_RENEWAL)
        self.assertEqual(order.status, Order.STATUS_PENDING)
        self.assertIsNotNone(order.payment_expires_at)

        subscription.refresh_from_db()
        vpn_client.refresh_from_db()

        self.assertEqual(subscription.expires_at, original_expiry)
        self.assertEqual(vpn_client.expires_at, original_expiry)
        self.assertEqual(Client.objects.count(), 1)
        self.assertEqual(Subscription.objects.count(), 1)


class ExpiredRenewalOrderTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="expired_renewal_test",
            password="test-password",
        )

        self.plan = Plan.objects.create(
            name="VPN тест повторного продления",
            code="expired-renewal-test",
            price=Decimal("450.00"),
            duration_days=30,
            device_limit=2,
        )

        self.original_expiry = timezone.now() + timedelta(days=15)

        self.vpn_client = Client.objects.create(
            username="expired_renewal_client",
            owner=self.user,
            is_active=True,
            expires_at=self.original_expiry,
        )

        self.subscription = Subscription.objects.create(
            client=self.vpn_client,
            plan=self.plan,
            status=Subscription.STATUS_ACTIVE,
            starts_at=timezone.now() - timedelta(days=15),
            expires_at=self.original_expiry,
            price=self.plan.price,
        )

        self.client.force_login(self.user)

    def test_expired_renewal_allows_new_order(self):
        from django.urls import reverse

        old_order = Order.objects.create(
            user=self.user,
            client=self.vpn_client,
            plan=self.plan,
            order_type=Order.TYPE_RENEWAL,
            status=Order.STATUS_PENDING,
            payment_expires_at=timezone.now() - timedelta(minutes=1),
            amount=self.plan.price,
            duration_days=self.plan.duration_days,
            device_limit=self.plan.device_limit,
        )

        before = timezone.now()

        response = self.client.post(
            reverse(
                "renew_subscription",
                args=[self.vpn_client.pk],
            ),
        )

        self.assertRedirects(
            response,
            reverse("cabinet"),
            fetch_redirect_response=False,
        )

        orders = Order.objects.filter(
            user=self.user,
            order_type=Order.TYPE_RENEWAL,
        )

        self.assertEqual(orders.count(), 2)

        new_order = orders.exclude(pk=old_order.pk).get()

        self.assertEqual(new_order.status, Order.STATUS_PENDING)
        self.assertGreaterEqual(
            new_order.payment_expires_at,
            before + timedelta(minutes=10),
        )

        old_order.refresh_from_db()
        self.assertEqual(old_order.status, Order.STATUS_PENDING)

        self.vpn_client.refresh_from_db()
        self.subscription.refresh_from_db()

        self.assertEqual(
            self.vpn_client.expires_at,
            self.original_expiry,
        )
        self.assertEqual(
            self.subscription.expires_at,
            self.original_expiry,
        )

        self.assertEqual(Client.objects.count(), 1)
        self.assertEqual(Subscription.objects.count(), 1)


class ExpiredOrdersDiagnosticTests(TestCase):

    def test_classification_without_modifying_data(self):
        from io import StringIO
        from uuid import uuid4

        from django.core.management import call_command
        from portal.models import Payment

        now = timezone.now()
        orders = {}

        for category in (
            "without_payments",
            "pending",
            "succeeded",
            "failed",
        ):
            orders[category] = Order.objects.create(
                user=get_user_model().objects.create_user(
                    username=f"diagnostic_{category}",
                ),
                plan=Plan.objects.create(
                    name=f"Диагностика {category}",
                    code=f"diagnostic-{category}",
                    price=Decimal("450.00"),
                    duration_days=30,
                    device_limit=2,
                ),
                order_type=Order.TYPE_NEW,
                status=Order.STATUS_PENDING,
                payment_expires_at=now - timedelta(minutes=1),
                amount=Decimal("450.00"),
                duration_days=30,
                device_limit=2,
            )

        for category, status in (
            ("pending", Payment.STATUS_PENDING),
            ("succeeded", Payment.STATUS_SUCCEEDED),
            ("failed", Payment.STATUS_FAILED),
        ):
            Payment.objects.create(
                order=orders[category],
                amount=Decimal("450.00"),
                status=status,
                idempotency_key=str(uuid4()),
            )

        output = StringIO()

        call_command(
            "check_expired_orders",
            stdout=output,
        )

        result = output.getvalue()

        self.assertIn("Без платежей: 1", result)
        self.assertIn("Требуют сверки: 2", result)
        self.assertIn("Завершённые без успеха: 1", result)
        self.assertIn("Всего просроченных: 4", result)

        for order in orders.values():
            order.refresh_from_db()
            self.assertEqual(
                order.status,
                Order.STATUS_PENDING,
            )

        self.assertEqual(
            Payment.objects.filter(
                status=Payment.STATUS_PENDING,
            ).count(),
            1,
        )

        self.assertEqual(
            Payment.objects.filter(
                status=Payment.STATUS_SUCCEEDED,
            ).count(),
            1,
        )


class OrderExpirationSafetyTests(TestCase):

    def setUp(self):
        from portal.models import Payment

        self.Payment = Payment
        self.user = get_user_model().objects.create_user(
            username="expiration_safety_test",
        )
        self.plan = Plan.objects.create(
            name="Тест безопасной отмены",
            code="expiration-safety-test",
            price=Decimal("450.00"),
            duration_days=30,
            device_limit=2,
        )

    def create_order(self, expires_at, status=Order.STATUS_PENDING):
        return Order.objects.create(
            user=self.user,
            plan=self.plan,
            order_type=Order.TYPE_NEW,
            status=status,
            payment_expires_at=expires_at,
            amount=self.plan.price,
            duration_days=self.plan.duration_days,
            device_limit=self.plan.device_limit,
        )

    def test_expiration_safety(self):
        from uuid import uuid4
        from portal.order_expiration import can_expire_order

        now = timezone.now()

        expired = now - timedelta(minutes=1)
        future = now + timedelta(minutes=5)

        # Просроченный заказ без платежей.
        order = self.create_order(expired)
        self.assertTrue(can_expire_order(order, now))

        # Срок оплаты ещё не истёк.
        order = self.create_order(future)
        self.assertFalse(can_expire_order(order, now))

        # Заказ уже оплачен.
        order = self.create_order(
            expired,
            status=Order.STATUS_PAID,
        )
        self.assertFalse(can_expire_order(order, now))

        # Заказ уже отменён.
        order = self.create_order(
            expired,
            status=Order.STATUS_CANCELLED,
        )
        self.assertFalse(can_expire_order(order, now))

        # Нет срока оплаты: старый заказ.
        order = self.create_order(None)
        self.assertFalse(can_expire_order(order, now))

        # Проверяем все статусы платежей.
        cases = [
            (self.Payment.STATUS_PENDING, False),
            (self.Payment.STATUS_SUCCEEDED, False),
            (self.Payment.STATUS_REFUNDED, False),
            (self.Payment.STATUS_FAILED, True),
            (self.Payment.STATUS_CANCELLED, True),
        ]

        for payment_status, expected in cases:
            order = self.create_order(expired)

            self.Payment.objects.create(
                order=order,
                amount=self.plan.price,
                status=payment_status,
                idempotency_key=str(uuid4()),
            )

            self.assertEqual(
                can_expire_order(order, now),
                expected,
                msg=f"Ошибка для статуса {payment_status}",
            )

        # Проверяем смешанные платежи:
        # failed + pending = отменять нельзя.
        order = self.create_order(expired)

        for status in (
            self.Payment.STATUS_FAILED,
            self.Payment.STATUS_PENDING,
        ):
            self.Payment.objects.create(
                order=order,
                amount=self.plan.price,
                status=status,
                idempotency_key=str(uuid4()),
            )

        self.assertFalse(can_expire_order(order, now))

        # Функция ничего не изменяет в базе.
        self.assertEqual(
            Order.objects.filter(status=Order.STATUS_PENDING).count(),
            9,
        )


class ExpirePaymentOrdersCommandTests(TestCase):

    def setUp(self):
        from uuid import uuid4

        self.uuid4 = uuid4
        self.user = get_user_model().objects.create_user(
            username="expire_command_test",
        )
        self.plan = Plan.objects.create(
            name="Тест команды отмены",
            code="expire-command-test",
            price=Decimal("450.00"),
            duration_days=30,
            device_limit=2,
        )

    def make_order(self, minutes_ago=15):
        return Order.objects.create(
            user=self.user,
            plan=self.plan,
            order_type=Order.TYPE_NEW,
            status=Order.STATUS_PENDING,
            payment_expires_at=(
                timezone.now() - timedelta(minutes=minutes_ago)
            ),
            amount=Decimal("450.00"),
            duration_days=30,
            device_limit=2,
        )

    def make_payment(self, order, status):
        from portal.models import Payment

        return Payment.objects.create(
            order=order,
            amount=order.amount,
            status=status,
            idempotency_key=str(self.uuid4()),
        )

    def test_dry_run_does_not_change_orders(self):
        from io import StringIO
        from django.core.management import call_command
        from portal.models import Payment

        safe = self.make_order()
        protected = self.make_order()
        self.make_payment(protected, Payment.STATUS_PENDING)

        output = StringIO()
        call_command("expire_payment_orders", stdout=output)

        safe.refresh_from_db()
        protected.refresh_from_db()

        self.assertEqual(safe.status, Order.STATUS_PENDING)
        self.assertEqual(protected.status, Order.STATUS_PENDING)

        result = output.getvalue()
        self.assertIn("РЕЖИМ: DRY-RUN", result)
        self.assertIn("Допускают отмену: 1", result)
        self.assertIn("Защищены от отмены: 1", result)
        self.assertIn("Отменено: 0", result)

    def test_apply_cancels_only_safe_orders(self):
        from io import StringIO
        from django.core.management import call_command
        from portal.models import Payment

        no_payment = self.make_order()

        failed_payment = self.make_order()
        self.make_payment(
            failed_payment,
            Payment.STATUS_FAILED,
        )

        pending_payment = self.make_order()
        self.make_payment(
            pending_payment,
            Payment.STATUS_PENDING,
        )

        succeeded_payment = self.make_order()
        self.make_payment(
            succeeded_payment,
            Payment.STATUS_SUCCEEDED,
        )

        output = StringIO()
        call_command(
            "expire_payment_orders",
            apply=True,
            stdout=output,
        )

        for order in (no_payment, failed_payment):
            order.refresh_from_db()
            self.assertEqual(
                order.status,
                Order.STATUS_CANCELLED,
            )

        for order in (pending_payment, succeeded_payment):
            order.refresh_from_db()
            self.assertEqual(
                order.status,
                Order.STATUS_PENDING,
            )

        result = output.getvalue()
        self.assertIn("РЕЖИМ: APPLY", result)
        self.assertIn("Допускают отмену: 2", result)
        self.assertIn("Защищены от отмены: 2", result)
        self.assertIn("Отменено: 2", result)


class ManualOrderCancellationSafetyTests(TestCase):

    def setUp(self):
        from portal.models import Payment

        self.Payment = Payment
        self.user = get_user_model().objects.create_user(
            username="manual_cancel_safety_test",
        )
        self.plan = Plan.objects.create(
            name="Тест ручной отмены",
            code="manual-cancel-safety-test",
            price=Decimal("450.00"),
            duration_days=30,
            device_limit=2,
        )

    def make_order(self, status=Order.STATUS_PENDING):
        return Order.objects.create(
            user=self.user,
            plan=self.plan,
            order_type=Order.TYPE_NEW,
            status=status,
            payment_expires_at=timezone.now() + timedelta(minutes=5),
            amount=Decimal("450.00"),
            duration_days=30,
            device_limit=2,
        )

    def add_payment(self, order, status):
        from uuid import uuid4

        return self.Payment.objects.create(
            order=order,
            amount=order.amount,
            status=status,
            idempotency_key=str(uuid4()),
        )

    def test_manual_cancellation_safety(self):
        from portal.order_expiration import can_cancel_order

        # Заказ без платежей можно отменить до истечения срока.
        order = self.make_order()
        self.assertTrue(can_cancel_order(order))

        # Оплаченный или отменённый заказ отменять нельзя.
        for status in (
            Order.STATUS_PAID,
            Order.STATUS_CANCELLED,
        ):
            order = self.make_order(status=status)
            self.assertFalse(can_cancel_order(order))

        # Проверяем каждый статус платежа.
        cases = (
            (self.Payment.STATUS_PENDING, False),
            (self.Payment.STATUS_SUCCEEDED, False),
            (self.Payment.STATUS_REFUNDED, False),
            (self.Payment.STATUS_FAILED, True),
            (self.Payment.STATUS_CANCELLED, True),
        )

        for payment_status, expected in cases:
            order = self.make_order()
            self.add_payment(order, payment_status)

            self.assertEqual(
                can_cancel_order(order),
                expected,
                msg=f"Ошибка для статуса {payment_status}",
            )

        # Смешанные платежи: failed + pending.
        order = self.make_order()
        self.add_payment(order, self.Payment.STATUS_FAILED)
        self.add_payment(order, self.Payment.STATUS_PENDING)

        self.assertFalse(can_cancel_order(order))

        # Проверка не должна менять статусы заказов.
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_PENDING)


class ManualCancellationHTTPTests(TestCase):

    def setUp(self):
        from django.urls import reverse

        self.reverse = reverse

        self.user = get_user_model().objects.create_user(
            username="manual_cancel_http_test",
            password="test-password-123",
        )
        self.client.force_login(self.user)

        self.plan = Plan.objects.create(
            name="HTTP тест отмены",
            code="manual-cancel-http-test",
            price=Decimal("450.00"),
            duration_days=30,
            device_limit=2,
        )

    def make_order(self):
        return Order.objects.create(
            user=self.user,
            plan=self.plan,
            order_type=Order.TYPE_NEW,
            status=Order.STATUS_PENDING,
            payment_expires_at=timezone.now() + timedelta(minutes=5),
            amount=Decimal("450.00"),
            duration_days=30,
            device_limit=2,
        )

    def add_payment(self, order, status):
        from uuid import uuid4
        from portal.models import Payment

        return Payment.objects.create(
            order=order,
            amount=order.amount,
            status=status,
            idempotency_key=str(uuid4()),
        )

    def cancel(self, order):
        return self.client.post(
            self.reverse(
                "cancel_order",
                kwargs={"order_id": order.pk},
            ),
            {"confirmed": "yes"},
        )

    def test_cancel_order_without_payments(self):
        order = self.make_order()

        response = self.cancel(order)

        self.assertEqual(response.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(
            order.status,
            Order.STATUS_CANCELLED,
        )

    def test_pending_payment_blocks_cancellation(self):
        from portal.models import Payment

        order = self.make_order()
        self.add_payment(order, Payment.STATUS_PENDING)

        response = self.cancel(order)

        self.assertEqual(response.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(
            order.status,
            Order.STATUS_PENDING,
        )

    def test_succeeded_payment_blocks_cancellation(self):
        from portal.models import Payment

        order = self.make_order()
        self.add_payment(order, Payment.STATUS_SUCCEEDED)

        response = self.cancel(order)

        self.assertEqual(response.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(
            order.status,
            Order.STATUS_PENDING,
        )


class AdminOrderCancellationTests(TestCase):

    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory
        from portal.admin import OrderAdmin
        from portal.models import Payment

        self.Payment = Payment
        self.user = get_user_model().objects.create_user(
            username="admin_cancel_test",
        )
        self.admin_user = get_user_model().objects.create_superuser(
            username="admin_cancel_superuser",
            email="admin-test@example.invalid",
            password="test-password-123",
        )
        self.request = RequestFactory().post("/admin/")
        self.request.user = self.admin_user

        self.plan = Plan.objects.create(
            name="Тест отмены администратором",
            code="admin-cancel-test",
            price=Decimal("450.00"),
            duration_days=30,
            device_limit=2,
        )

        self.admin_instance = OrderAdmin(Order, AdminSite())

    def make_order(self, status=Order.STATUS_PENDING):
        return Order.objects.create(
            user=self.user,
            plan=self.plan,
            order_type=Order.TYPE_NEW,
            status=status,
            payment_expires_at=timezone.now() + timedelta(minutes=5),
            amount=Decimal("450.00"),
            duration_days=30,
            device_limit=2,
        )

    def add_payment(self, order, status):
        from uuid import uuid4

        return self.Payment.objects.create(
            order=order,
            amount=order.amount,
            status=status,
            idempotency_key=str(uuid4()),
        )

    def test_admin_cancellation_protects_payments(self):
        from unittest.mock import patch

        safe = self.make_order()
        pending = self.make_order()
        succeeded = self.make_order()
        already_paid = self.make_order(
            status=Order.STATUS_PAID,
        )

        self.add_payment(pending, self.Payment.STATUS_PENDING)
        self.add_payment(succeeded, self.Payment.STATUS_SUCCEEDED)

        queryset = Order.objects.filter(
            pk__in=[
                safe.pk,
                pending.pk,
                succeeded.pk,
                already_paid.pk,
            ]
        )

        with patch.object(self.admin_instance, "message_user"):
            self.admin_instance.mark_as_refused(
                self.request,
                queryset,
            )

        safe.refresh_from_db()
        pending.refresh_from_db()
        succeeded.refresh_from_db()
        already_paid.refresh_from_db()

        self.assertEqual(safe.status, Order.STATUS_CANCELLED)
        self.assertEqual(pending.status, Order.STATUS_PENDING)
        self.assertEqual(succeeded.status, Order.STATUS_PENDING)
        self.assertEqual(already_paid.status, Order.STATUS_PAID)



class PaymentCancellationRaceTests(TestCase):
    """Проверки защиты просроченных заказов."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_user(
            username="payment_race_test_user",
        )

        self.plan = Plan.objects.create(
            name="Тест защиты оплаты",
            code="payment-race-test",
            price=Decimal("450.00"),
            duration_days=30,
            device_limit=2,
        )

    def make_expired_order(self):
        return Order.objects.create(
            user=self.user,
            plan=self.plan,
            order_type=Order.TYPE_NEW,
            status=Order.STATUS_PENDING,
            payment_expires_at=(
                timezone.now() - timedelta(minutes=1)
            ),
            amount=self.plan.price,
            duration_days=self.plan.duration_days,
            device_limit=self.plan.device_limit,
        )

    def make_payment(self, order, status):
        from uuid import uuid4
        from .models import Payment

        return Payment.objects.create(
            order=order,
            amount=order.amount,
            status=status,
            idempotency_key=str(uuid4()),
        )

    def test_pending_payment_prevents_expiration(self):
        from .models import Payment
        from .order_expiration import can_expire_order

        order = self.make_expired_order()
        self.make_payment(order, Payment.STATUS_PENDING)

        self.assertFalse(can_expire_order(order))

    def test_succeeded_payment_prevents_expiration(self):
        from .models import Payment
        from .order_expiration import can_expire_order

        order = self.make_expired_order()
        self.make_payment(order, Payment.STATUS_SUCCEEDED)

        self.assertFalse(can_expire_order(order))

    def test_expired_order_without_payments_is_eligible(self):
        from .order_expiration import can_expire_order

        order = self.make_expired_order()

        self.assertTrue(can_expire_order(order))
