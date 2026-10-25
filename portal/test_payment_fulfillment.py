from unittest.mock import patch

from django.test import SimpleTestCase

from portal.models import Order
from portal.payment_fulfillment import (
    has_pending_paid_fulfillment,
)


class PaidFulfillmentProtectionTests(SimpleTestCase):

    def check_query(self, expected, **order_fields):
        with patch("portal.payment_fulfillment.Order.objects") as manager:
            queryset = manager.filter.return_value
            queryset.filter.return_value.exists.return_value = expected

            result = has_pending_paid_fulfillment(42)

            self.assertEqual(result, expected)

            manager.filter.assert_called_once_with(
                client_id=42,
                order_type=Order.TYPE_RENEWAL,
                fulfillment_status__in=(
                    Order.FULFILLMENT_ACTIVATION_PENDING,
                    Order.FULFILLMENT_SYNC_PENDING,
                Order.FULFILLMENT_ERROR,
                ),
            )

            queryset.filter.assert_called_once()
            queryset.filter.return_value.exists.assert_called_once()

    def test_protected_order_found(self):
        self.check_query(True)

    def test_no_protected_order(self):
        self.check_query(False)

    def test_missing_client_is_not_protected(self):
        with patch("portal.payment_fulfillment.Order.objects") as manager:
            self.assertFalse(has_pending_paid_fulfillment(None))
            manager.filter.assert_not_called()
