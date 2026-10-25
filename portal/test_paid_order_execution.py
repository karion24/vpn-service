"""
Будущие проверки исполнения оплаченных заказов.

Пока не подключены к рабочей логике.
"""

from django.test import SimpleTestCase


class PaidOrderExecutionRequirements(SimpleTestCase):

    def test_payment_and_fulfillment_are_separate(self):
        from portal.models import Order, Payment

        self.assertNotEqual(
            Payment.STATUS_SUCCEEDED,
            Order.FULFILLMENT_COMPLETED,
        )

    def test_activation_and_sync_have_separate_states(self):
        from portal.models import Order

        self.assertNotEqual(
            Order.FULFILLMENT_ACTIVATION_PENDING,
            Order.FULFILLMENT_SYNC_PENDING,
        )

    def test_completed_state_exists(self):
        from portal.models import Order

        self.assertIn(
            (
                Order.FULFILLMENT_COMPLETED,
                "Исполнен",
            ),
            Order.FULFILLMENT_CHOICES,
        )
