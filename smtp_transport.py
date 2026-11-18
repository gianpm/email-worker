"""SMTP TLS transport for untrusted, request-supplied destination hosts."""

import ipaddress
import smtplib
import socket
import ssl
import time


class SMTPDestinationError(ValueError):
    """The request points to an address that must not be contacted."""


class PublicSMTP_SSL(smtplib.SMTP_SSL):
    def _get_socket(self, host, port, timeout):
        # Resolve once and connect to the checked numeric address: do not re-resolve
        # the hostname at connect time, which would allow DNS rebinding.
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
        if not records:
            raise OSError('SMTP DNS retornou uma lista vazia')
        for _, _, _, _, address in records:
            ip = ipaddress.ip_address(address[0])
            mapped = getattr(ip, 'ipv4_mapped', None)
            if not ip.is_global or ip.is_multicast or (mapped and not mapped.is_global):
                raise SMTPDestinationError('O host SMTP da request deve resolver apenas para enderecos publicos')

        deadline = time.monotonic() + timeout
        last_error = None
        for family, kind, protocol, _, address in records:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Timeout ao conectar no SMTP')
            raw = socket.socket(family, kind, protocol)
            try:
                raw.settimeout(remaining)
                raw.connect(address)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Timeout ao negociar TLS')
                raw.settimeout(remaining)
                secure = self.context.wrap_socket(raw, server_hostname=host)
                secure.settimeout(timeout)
                return secure
            except ssl.SSLError:
                raw.close()
                raise
            except OSError as error:
                raw.close()
                last_error = error
        raise last_error
