import socket
import ssl
import unittest
from unittest.mock import Mock, patch

from smtp_transport import PublicSMTP_SSL, SMTPDestinationError


def ipv4(address):
    return (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (address, 465))


class SMTPTransportTests(unittest.TestCase):
    def setUp(self):
        self.context = Mock()
        self.smtp = PublicSMTP_SSL(local_hostname='client.example', context=self.context)

    def test_private_loopback_linklocal_and_mixed_dns_are_blocked(self):
        for address in ('127.0.0.1', '10.0.0.1', '172.16.0.1', '192.168.1.1', '169.254.169.254', '0.0.0.0', '224.0.0.1'):
            with self.subTest(address=address), patch('smtp_transport.socket.getaddrinfo', return_value=[ipv4('8.8.8.8'), ipv4(address)]), patch('smtp_transport.socket.socket') as sock:
                with self.assertRaises(SMTPDestinationError):
                    self.smtp._get_socket('mail.example', 465, 5)
                sock.assert_not_called()

    def test_ipv6_private_and_ipv4_mapped_private_are_blocked(self):
        for address in ('::1', 'fc00::1', 'fe80::1', '::ffff:127.0.0.1', 'ff02::1'):
            record = (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (address, 465, 0, 0))
            with self.subTest(address=address), patch('smtp_transport.socket.getaddrinfo', return_value=[record]), patch('smtp_transport.socket.socket') as sock:
                with self.assertRaises(SMTPDestinationError):
                    self.smtp._get_socket('mail.example', 465, 5)
                sock.assert_not_called()

    def test_connects_to_validated_ip_and_preserves_hostname_for_tls(self):
        with patch('smtp_transport.socket.getaddrinfo', return_value=[ipv4('8.8.8.8')]) as resolve, patch('smtp_transport.socket.socket') as factory:
            result = self.smtp._get_socket('mail.example', 465, 5)
        resolve.assert_called_once()
        factory.return_value.connect.assert_called_once_with(('8.8.8.8', 465))
        self.context.wrap_socket.assert_called_once_with(factory.return_value, server_hostname='mail.example')
        self.assertIs(result, self.context.wrap_socket.return_value)

    def test_failed_tls_closes_socket_and_is_not_silently_downgraded(self):
        self.context.wrap_socket.side_effect = ssl.SSLCertVerificationError('certificate mismatch')
        with patch('smtp_transport.socket.getaddrinfo', return_value=[ipv4('8.8.8.8')]), patch('smtp_transport.socket.socket') as factory:
            with self.assertRaises(ssl.SSLCertVerificationError):
                self.smtp._get_socket('mail.example', 465, 5)
            factory.return_value.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
