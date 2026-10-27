from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, override_settings

from portal.notification_backends import NotificationDeliveryError, send_max, send_telegram


@override_settings(
    TELEGRAM_BOT_SERVER_URL="https://tg.example.test",
    TELEGRAM_BOT_SERVER_TOKEN="test-gateway-key",
    TELEGRAM_BOT_TOKEN="test-bot-token",
    MAX_BOT_TOKEN="test-max-key",
    TELEGRAM_BOT_SERVER_RESPONSE_VALIDATOR="portal.notification_backends.telegram_gateway_dash_response",
)
class NotificationBackendTests(SimpleTestCase):
    def response(self, payload, status=200):
        return Mock(status_code=status, json=Mock(return_value=payload))

    @patch("portal.notification_backends.requests.post")
    def test_telegram_gateway_contract(self, post):
        post.return_value = self.response({"ok": True, "message_id": 12})
        send_telegram(recipient="123", message="Тест", idempotency_key="one")
        self.assertEqual(post.call_args.args, ("https://tg.example.test/send-message",))
        self.assertEqual(post.call_args.kwargs["json"], {"chat_id": 123, "text": "Тест"})
        self.assertEqual(post.call_args.kwargs["headers"], {"X-KARION-TOKEN": "test-gateway-key"})
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertEqual(post.call_args.kwargs["timeout"], 25)

    @override_settings(TELEGRAM_BOT_SERVER_URL="")
    @patch("portal.notification_backends.requests.post")
    def test_direct_telegram_contract(self, post):
        post.return_value = self.response({"ok": True, "result": {"message_id": 1}})
        send_telegram(recipient="123", message="Тест", idempotency_key="one")
        self.assertEqual(post.call_args.args, ("https://api.telegram.org/bottest-bot-token/sendMessage",))
        self.assertEqual(post.call_args.kwargs["json"]["chat_id"], 123)

    @patch("portal.notification_backends.requests.post")
    def test_max_contract(self, post):
        post.return_value = self.response({"message": {"body": {"mid": "mid-123"}}})
        send_max(recipient="456", message="Тест", idempotency_key="one")
        self.assertEqual(post.call_args.args, ("https://platform-api.max.ru/messages",))
        self.assertEqual(post.call_args.kwargs["params"], {"user_id": 456})
        self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "test-max-key"})
        self.assertEqual(post.call_args.kwargs["json"], {"text": "Тест"})

    @patch("portal.notification_backends.requests.post")
    def test_provider_failure_and_malformed_response(self, post):
        for response in [self.response({"ok": False}), self.response({}, 302), self.response({}, 500), self.response([])]:
            post.return_value = response
            with self.assertRaises(NotificationDeliveryError):
                send_telegram(recipient="123", message="Тест", idempotency_key="one")
        # MAX matches dash.zip: even empty/non-JSON HTTP 2xx is accepted.
        post.return_value = self.response({})
        send_max(recipient="123", message="Тест", idempotency_key="one")
        post.return_value.json.side_effect = ValueError("provider body")
        send_max(recipient="123", message="Тест", idempotency_key="one")

    @patch("portal.notification_backends.requests.post")
    def test_transport_errors_are_redacted(self, post):
        post.side_effect = requests.Timeout("secret URL and token")
        with self.assertRaises(NotificationDeliveryError) as caught:
            send_telegram(recipient="123", message="Тест", idempotency_key="one")
        self.assertNotIn("secret", str(caught.exception))

    @patch("portal.notification_backends.requests.post")
    def test_invalid_contacts_never_send(self, post):
        for recipient in ["@name", "+79991234567", "0", "-123", "１２３"]:
            for sender in [send_telegram, send_max]:
                with self.assertRaises(ValueError):
                    sender(recipient=recipient, message="Тест", idempotency_key="one")
        post.assert_not_called()

    @override_settings(TELEGRAM_BOT_SERVER_TOKEN="", MAX_BOT_TOKEN="")
    @patch("portal.notification_backends.requests.post")
    def test_missing_credentials_never_send(self, post):
        for sender in [send_telegram, send_max]:
            with self.assertRaises(NotificationDeliveryError):
                sender(recipient="123", message="Тест", idempotency_key="one")
        post.assert_not_called()

    @override_settings(TELEGRAM_BOT_SERVER_URL="http://tg.example.test")
    @patch("portal.notification_backends.requests.post")
    def test_insecure_gateway_never_receives_token(self, post):
        with self.assertRaises(NotificationDeliveryError):
            send_telegram(recipient="123", message="Тест", idempotency_key="one")
        post.assert_not_called()

    @override_settings(TELEGRAM_BOT_SERVER_RESPONSE_VALIDATOR="")
    @patch("portal.notification_backends.requests.post")
    def test_unverified_gateway_contract_blocks_before_sending(self, post):
        with self.assertRaises(NotificationDeliveryError):
            send_telegram(recipient="123", message="Тест", idempotency_key="one")
        post.assert_not_called()

    @override_settings(TELEGRAM_BOT_SERVER_RESPONSE_VALIDATOR="example.verified_response")
    @patch("portal.notification_backends.import_string")
    @patch("portal.notification_backends.requests.post")
    def test_gateway_validator_can_confirm_response_without_ok(self, post, loader):
        post.return_value = self.response({"message_id": 42})
        loader.return_value.return_value = True
        send_telegram(recipient="123", message="Тест", idempotency_key="one")
        loader.return_value.assert_called_once_with(post.return_value)

    @patch("portal.notification_backends.requests.post")
    def test_unknown_gateway_response_is_not_safe_to_retry(self, post):
        from portal.notification_backends import NotificationDeliveryUnknown
        post.return_value = self.response({"message_id": 42})
        with self.assertRaises(NotificationDeliveryUnknown):
            send_telegram(recipient="123", message="Тест", idempotency_key="one")

    @patch("portal.notification_backends.requests.post")
    def test_read_timeout_is_unknown_but_connect_timeout_is_not_sent(self, post):
        from portal.notification_backends import NotificationDeliveryUnknown
        post.side_effect = requests.ReadTimeout()
        with self.assertRaises(NotificationDeliveryUnknown):
            send_max(recipient="123", message="Тест", idempotency_key="one")
        post.side_effect = requests.ConnectTimeout()
        with self.assertRaises(NotificationDeliveryError) as caught:
            send_max(recipient="123", message="Тест", idempotency_key="one")
        self.assertNotIsInstance(caught.exception, NotificationDeliveryUnknown)
