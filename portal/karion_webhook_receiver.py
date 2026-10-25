"""Authenticated Karion Payments event receiver. Independent invoice verification."""
import hashlib
import hmac
import json
import logging
import os
import uuid
from decimal import Decimal
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .models import Payment, Order
from .karion_payments_api import get_invoice
from .cabinet_payment_reconciliation import _verify_invoice
from .payment_confirmation import confirm_succeeded_payment
from .paid_new_order_execution import activate_paid_new_order
from .paid_order_execution import apply_paid_order_entitlement
from .paid_order_sync import sync_paid_order

log = logging.getLogger(__name__)

@csrf_exempt
@require_POST
def receive_payment_event(request):
    secret = os.environ.get('KARION_PAYMENTS_WEBHOOK_SECRET', '')
    if len(secret) < 32:
        return JsonResponse({'error': 'receiver not configured'}, status=503)
    if len(request.body) > 32768:
        return JsonResponse({'error': 'too large'}, status=413)
    signature = request.headers.get('X-Karion-Signature', '')
    expected = 'sha256=' + hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return JsonResponse({'error': 'unauthorized'}, status=401)
    try:
        event = json.loads(request.body)
        if (event.get('project') != 'karion_vpn' or
            event.get('event_type') != 'payment.succeeded' or
            event.get('payment', {}).get('status') != 'succeeded' or
            event.get('invoice', {}).get('status') != 'paid'):
            return JsonResponse({'error': 'invalid event'}, status=400)
        event_id = uuid.UUID(event['event_id'])
        invoice_id = uuid.UUID(event['invoice']['public_id'])
        payment = Payment.objects.select_related('order').get(
            gateway_invoice_id=invoice_id, provider='karion_payments')
        if event['invoice'].get('external_id') != f'karion-vpn-order-{payment.order_id}':
            return JsonResponse({'error': 'invoice mismatch'}, status=400)
        response = get_invoice(invoice_id)
        if response.get('success') is not True or not isinstance(response.get('invoice'), dict):
            raise ValueError('gateway response not confirmed')
        invoice = response['invoice']
        amount = _verify_invoice(payment, invoice)
        if invoice.get('status') != 'paid':
            return JsonResponse({'error': 'invoice not paid'}, status=409)
        if Decimal(str(event['payment']['amount'])) != amount or event['payment'].get('currency') != 'RUB':
            return JsonResponse({'error': 'amount mismatch'}, status=400)
        confirm_succeeded_payment(payment_id=payment.pk, confirmed_amount=amount)
        order = Order.objects.get(pk=payment.order_id)
        if order.status == Order.STATUS_CANCELLED:
            log.warning('Payment on cancelled order %s', order.pk)
            return JsonResponse({'ok': True, 'activation': 'cancelled'})
        if order.fulfillment_status == Order.FULFILLMENT_ACTIVATION_PENDING:
            if order.order_type == Order.TYPE_NEW:
                activate_paid_new_order(order.pk)
            elif order.order_type == Order.TYPE_RENEWAL:
                apply_paid_order_entitlement(order.pk)
        order.refresh_from_db()
        if (order.order_type == Order.TYPE_RENEWAL and
            order.fulfillment_status == Order.FULFILLMENT_SYNC_PENDING):
            sync_paid_order(order.pk)
        return JsonResponse({'ok': True, 'event_id': str(event_id)})
    except Payment.DoesNotExist:
        return JsonResponse({'error': 'unknown invoice'}, status=404)
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        log.warning('Invalid payment webhook: %s', type(exc).__name__)
        return JsonResponse({'error': 'invalid payment'}, status=400)
    except Exception:
        log.exception('Webhook processing failed')
        return JsonResponse({'error': 'temporary failure'}, status=503)
