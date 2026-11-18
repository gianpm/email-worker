import base64
import os
import smtplib
import ssl
import unittest
from smtplib import SMTP_SSL as StdlibSMTP_SSL
from email import policy
from email.parser import BytesParser
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import app


class EmailApiTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'API_TOKEN': 'token-test', 'SMTP_HOST': 'smtp.example.com',
            'SMTP_PORT': '465', 'SMTP_USER': 'sender@example.com',
            'SMTP_PASSWORD': 'test-only-password', 'SMTP_FROM': 'sender@example.com',
        })
        self.env.start()
        self.smtp_patch = patch('app.smtplib.SMTP_SSL')
        self.smtp_class = self.smtp_patch.start()
        self.smtp = self.smtp_class.return_value
        self.smtp.send_message.return_value = {}
        self.context = TestClient(app)
        self.client = self.context.__enter__()
        self.headers = {'Authorization': 'Bearer token-test'}
        self.payload = {'para': 'recipient@example.com', 'assunto': 'Olá', 'texto': 'Texto com acentos e .\n.'}

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.smtp_patch.stop()
        self.env.stop()

    def post(self, payload=None):
        return self.client.post('/enviar-email', json=self.payload if payload is None else payload, headers=self.headers)

    def test_health_does_not_connect_to_smtp(self):
        self.assertEqual(self.client.get('/health').status_code, 200)
        self.smtp_class.assert_not_called()

    def test_authentication_happens_before_body_parsing(self):
        result = self.client.post('/enviar-email', content='invalid JSON', headers={'Authorization': 'Bearer errado'})
        self.assertEqual(result.status_code, 401)
        self.smtp_class.assert_not_called()

    def test_send_preserves_binary_attachments_and_checks_tls(self):
        binary = bytes([0, 255, 10, 13, 128, 46])
        self.payload['attachments'] = [{
            'filename': 'contribuição.pdf', 'contentType': 'application/pdf',
            'base64': base64.b64encode(binary).decode(),
        }]
        result = self.post()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['aceitoPeloServidor'])
        args, kwargs = self.smtp_class.call_args
        self.assertEqual(args, ('smtp.example.com', 465))
        self.assertEqual(kwargs['context'].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(kwargs['context'].check_hostname)
        self.smtp.login.assert_called_once_with('sender@example.com', 'test-only-password')
        msg = self.smtp.send_message.call_args.args[0]
        parsed = BytesParser(policy=policy.default).parsebytes(msg.as_bytes())
        self.assertEqual(str(parsed['Subject']), 'Olá')
        attachments = list(parsed.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), 'contribuição.pdf')
        self.assertEqual(attachments[0].get_payload(decode=True), binary)
        self.assertIn('Texto com acentos', parsed.get_body().get_content())

    def test_bad_attachments_are_rejected_before_smtp(self):
        for attachments in [
            [{'filename': 'test.txt', 'base64': 'invalid'}],
            [{'filename': 'test.txt\r\nBcc: x@y.com', 'base64': ''}],
            [{'filename': 'test.txt', 'contentType': 'text/plain\r\nX: y', 'base64': ''}],
            [{'filename': 'test.txt', 'base64': ''}] * 11,
            [{'filename': 'big.bin', 'base64': base64.b64encode(b'x' * (5 * 1024 * 1024 + 1)).decode()}],
        ]:
            with self.subTest(attachments_count=len(attachments)):
                result = self.post({**self.payload, 'attachments': attachments})
                self.assertEqual(result.status_code, 400)
        self.smtp_class.assert_not_called()

    def test_invalid_recipient_and_subject_are_rejected(self):
        for changes in [{'para': 'a@example.com\n'}, {'para': 'a@example.com,b@example.com'}, {'assunto': 'test\r\nBcc: a@example.com'}]:
            self.assertEqual(self.post({**self.payload, **changes}).status_code, 400)
        self.smtp_class.assert_not_called()

    def test_body_larger_than_limit_is_rejected(self):
        result = self.client.post('/enviar-email', content=b'x' * (8 * 1024 * 1024 + 1),
            headers={**self.headers, 'Content-Type': 'application/json'})
        self.assertEqual(result.status_code, 413)
        self.smtp_class.assert_not_called()

    def test_auth_failure_does_not_leak_password(self):
        self.smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b'rejected')
        result = self.post()
        self.assertEqual(result.status_code, 502)
        self.assertNotIn('test-only-password', result.text)

    def test_failure_during_quit_does_not_undo_accepted_message(self):
        self.smtp.quit.side_effect = smtplib.SMTPServerDisconnected('closed')
        self.assertEqual(self.post().status_code, 200)
        self.smtp.send_message.assert_called_once()

    def test_failure_during_close_does_not_undo_accepted_message(self):
        self.smtp.close.side_effect = OSError('cleanup failure')
        self.assertEqual(self.post().status_code, 200)
        self.smtp.send_message.assert_called_once()

    def test_refused_recipient_is_not_reported_as_success(self):
        self.smtp.send_message.return_value = {'recipient@example.com': (550, b'rejected')}
        self.assertEqual(self.post().status_code, 502)

    def test_custom_smtp_uses_request_credentials_and_preserves_attachment(self):
        custom = {'host': 'mail.other.example', 'user': 'smtp-login', 'password': 'request-only-secret',
                  'from': 'billing@other.example', 'port': 465}
        binary = bytes([0, 255, 128, 10])
        payload = {**self.payload, 'smtp': custom, 'attachments': [{
            'filename': 'documento.bin', 'base64': base64.b64encode(binary).decode(),
        }]}
        with patch('app.PublicSMTP_SSL') as transport:
            transport.return_value.send_message.return_value = {}
            result = self.post(payload)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(transport.call_args.args, ('mail.other.example', 465))
            context = transport.call_args.kwargs['context']
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
            transport.return_value.login.assert_called_once_with('smtp-login', 'request-only-secret')
            msg = transport.return_value.send_message.call_args.args[0]
            self.assertEqual(str(msg['From']), 'billing@other.example')
            parsed = BytesParser(policy=policy.default).parsebytes(msg.as_bytes())
            self.assertEqual(next(parsed.iter_attachments()).get_payload(decode=True), binary)
            self.assertNotIn('request-only-secret', result.text)
        self.smtp_class.assert_not_called()

    def test_custom_sender_defaults_to_request_user_without_affecting_defaults(self):
        custom = {'host': 'mail.other.example', 'user': 'account@other.example', 'password': 'custom-secret'}
        with patch('app.PublicSMTP_SSL') as transport:
            transport.return_value.send_message.return_value = {}
            self.assertEqual(self.post({**self.payload, 'smtp': custom}).status_code, 200)
            msg = transport.return_value.send_message.call_args.args[0]
            self.assertEqual(str(msg['From']), 'account@other.example')
        self.assertEqual(self.post().status_code, 200)
        self.smtp.login.assert_called_once_with('sender@example.com', 'test-only-password')
        self.assertEqual(str(self.smtp.send_message.call_args.args[0]['From']), 'sender@example.com')

    def test_custom_smtp_requires_complete_valid_config_and_only_port_465(self):
        custom = {'host': 'mail.other.example', 'user': 'sender@other.example', 'password': 'request-secret'}
        invalid = [
            {'host': 'mail.other.example'},
            {**custom, 'password': ''},
            {**custom, 'port': 587},
            {**custom, 'port': '465'},
            {**custom, 'host': 'https://mail.other.example'},
            {**custom, 'host': 'mail.other.example\r\nX: y'},
            {**custom, 'from': 'a@example.com\nBcc: b@example.com'},
            {**custom, 'user': 'login-id'},
        ]
        for config in invalid:
            with self.subTest(config_keys=list(config)):
                result = self.post({**self.payload, 'smtp': config})
                self.assertEqual(result.status_code, 400)
                self.assertNotIn('request-secret', result.text)
        self.smtp_class.assert_not_called()

    def test_custom_smtp_still_requires_bearer_token(self):
        result = self.client.post('/enviar-email', json={**self.payload, 'smtp': {
            'host': 'mail.other.example', 'user': 'user@other.example', 'password': 'secret',
        }})
        self.assertEqual(result.status_code, 401)
        self.smtp_class.assert_not_called()

    def test_blocked_custom_destination_returns_400(self):
        from smtp_transport import SMTPDestinationError
        with patch('app.PublicSMTP_SSL', side_effect=SMTPDestinationError('Host SMTP deve ser publico')):
            result = self.post({**self.payload, 'smtp': {
                'host': 'localhost.example', 'user': 'a@example.com', 'password': 'secret',
            }})
        self.assertEqual(result.status_code, 400)

    def test_custom_smtp_auth_failure_does_not_leak_request_credentials(self):
        with patch('app.PublicSMTP_SSL') as transport:
            transport.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b'rejected')
            result = self.post({**self.payload, 'smtp': {
                'host': 'mail.other.example', 'user': 'a@example.com', 'password': 'request-only-secret',
            }})
        self.assertEqual(result.status_code, 502)
        self.assertNotIn('request-only-secret', result.text)

    def test_non_ascii_custom_credentials_are_rejected_before_connect(self):
        # Real stdlib AUTH PLAIN encoding fails before any network I/O.
        client = StdlibSMTP_SSL(local_hostname='client.example')
        client.ehlo_resp = b'ok'
        client.esmtp_features = {'auth': 'PLAIN'}
        with self.assertRaises(UnicodeEncodeError):
            client.login('user', 'senha-\u00e9')
        custom = {'host': 'mail.other.example', 'user': 'a@other.example',
                  'password': 'ascii-secret', 'from': 'a@other.example'}
        for changes in ({'password': 'senha-\u00e9'}, {'user': 'usu\u00e1rio'}):
            with self.subTest(fields=list(changes)), patch('app.PublicSMTP_SSL') as transport:
                transport.return_value.send_message.return_value = {}
                result = self.post({**self.payload, 'smtp': {**custom, **changes}})
                self.assertEqual(result.status_code, 400)
                self.assertNotIn('senha-\u00e9', result.text)
                transport.assert_not_called()


class TokenOnlyDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'API_TOKEN': 'token-only-test', 'SMTP_HOST': '', 'SMTP_PORT': '',
            'SMTP_USER': '', 'SMTP_PASSWORD': '', 'SMTP_FROM': '',
        })
        self.env.start()
        self.headers = {'Authorization': 'Bearer token-only-test'}
        self.payload = {'para': 'recipient@example.com', 'assunto': 'Teste', 'texto': 'Texto simples'}

    def tearDown(self):
        self.env.stop()

    def test_starts_with_only_api_token_and_health_is_public(self):
        with TestClient(app) as client:
            self.assertEqual(client.get('/health').json(), {'status': 'ok'})

    def test_api_token_is_still_required_at_startup(self):
        with patch.dict(os.environ, {'API_TOKEN': ''}), self.assertRaisesRegex(RuntimeError, 'API_TOKEN'):
            with TestClient(app):
                pass

    def test_missing_default_smtp_returns_actionable_400_without_connecting(self):
        with TestClient(app) as client, patch('app.smtplib.SMTP_SSL') as transport:
            for payload in (self.payload, {**self.payload, 'smtp': None}):
                result = client.post('/enviar-email', json=payload, headers=self.headers)
                self.assertEqual(result.status_code, 400)
                self.assertIn('smtp', result.json()['detail'])
            transport.assert_not_called()

    def test_missing_token_is_rejected_before_missing_smtp(self):
        with TestClient(app) as client:
            self.assertEqual(client.post('/enviar-email', json=self.payload).status_code, 401)

    def test_custom_smtp_with_and_without_attachments_needs_no_smtp_env(self):
        custom = {'host': 'mail.other.example', 'user': 'sender@other.example', 'password': 'request-secret'}
        with TestClient(app) as client, patch('app.PublicSMTP_SSL') as transport:
            transport.return_value.send_message.return_value = {}
            for attachments in ([], [{'filename': 'teste.txt', 'contentType': 'text/plain', 'base64': 'T2xhIQ=='}]):
                result = client.post('/enviar-email', headers=self.headers,
                    json={**self.payload, 'smtp': custom, 'attachments': attachments})
                self.assertEqual(result.status_code, 200, result.text)
                msg = transport.return_value.send_message.call_args.args[0]
                parsed = BytesParser(policy=policy.default).parsebytes(msg.as_bytes())
                self.assertEqual(len(list(parsed.iter_attachments())), len(attachments))
                self.assertIsNone(parsed.get_body(preferencelist=('html',)))

    def test_partial_defaults_do_not_prevent_startup(self):
        with patch.dict(os.environ, {'SMTP_HOST': 'smtp.example.com'}), TestClient(app) as client:
            self.assertEqual(client.get('/health').status_code, 200)
            self.assertEqual(client.post('/enviar-email', json=self.payload, headers=self.headers).status_code, 400)

    def test_invalid_default_sender_returns_400_on_send(self):
        with patch.dict(os.environ, {'SMTP_HOST': 'smtp.example.com', 'SMTP_USER': 'smtp-login',
                                    'SMTP_PASSWORD': 'default-secret'}), TestClient(app) as client, patch('app.smtplib.SMTP_SSL') as transport:
            result = client.post('/enviar-email', json=self.payload, headers=self.headers)
            self.assertEqual(result.status_code, 400)
            self.assertIn('SMTP_FROM', result.json()['detail'])
            transport.assert_not_called()

    def test_html_body_is_explicitly_unsupported(self):
        with TestClient(app) as client:
            result = client.post('/enviar-email', headers=self.headers,
                json={**self.payload, 'html': '<h1>Teste</h1>'})
            self.assertEqual(result.status_code, 400)
            self.assertEqual(result.json()['detail'][0]['campo'], ['html'])


if __name__ == '__main__':
    unittest.main()
