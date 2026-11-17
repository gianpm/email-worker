import base64
import os
import smtplib
import ssl
import unittest
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


if __name__ == '__main__':
    unittest.main()
