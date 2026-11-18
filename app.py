"""HTTP endpoint for authenticated SMTP SSL/TLS email delivery."""

import base64
import binascii
import hmac
import ipaddress
import logging
import os
import re
import smtplib
import ssl
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, SecretStr, ValidationError, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from smtp_transport import PublicSMTP_SSL, SMTPDestinationError

MAX_BODY = 8 * 1024 * 1024
MAX_ATTACHMENTS = 5 * 1024 * 1024
EMAIL = re.compile(r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?\.[a-zA-Z]{2,}")
logger = logging.getLogger('email-worker')


@dataclass(frozen=True)
class Settings:
    token: str
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    sender: str

    @classmethod
    def load(cls):
        required = ('API_TOKEN', 'SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD')
        missing = [key for key in required if not os.environ.get(key)]
        if missing:
            raise RuntimeError('Configure as variaveis: ' + ', '.join(missing))
        port = int(os.environ.get('SMTP_PORT', '465'))
        if port != 465:
            raise RuntimeError('Esta API usa SMTP SSL/TLS implicito; SMTP_PORT deve ser 465')
        sender = os.environ.get('SMTP_FROM') or os.environ['SMTP_USER']
        if not EMAIL.fullmatch(sender):
            raise RuntimeError('SMTP_FROM deve ser um endereco de e-mail simples')
        return cls(os.environ['API_TOKEN'], os.environ['SMTP_HOST'], port,
                   os.environ['SMTP_USER'], os.environ['SMTP_PASSWORD'], sender)


class Attachment(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    filename: str = Field(min_length=1, max_length=180)
    contentType: str = Field(default='application/octet-stream', max_length=100)
    base64: str = Field(max_length=6990508)

    @field_validator('filename')
    @classmethod
    def filename_valid(cls, value):
        if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('Nome de arquivo invalido')
        return value

    @field_validator('contentType')
    @classmethod
    def content_type_valid(cls, value):
        if not re.fullmatch(r'[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+', value):
            raise ValueError('contentType invalido')
        return value


class SMTPConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=465, ge=465, le=465)
    user: str = Field(min_length=1, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=4096)
    sender: str | None = Field(default=None, alias='from', max_length=254)

    @field_validator('host')
    @classmethod
    def host_valid(cls, value):
        if value != value.strip() or '%' in value:
            raise ValueError('Host SMTP invalido')
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            pass
        try:
            hostname = value.removesuffix('.').encode('idna').decode('ascii').lower()
        except UnicodeError:
            raise ValueError('Host SMTP invalido') from None
        labels = hostname.split('.')
        if len(labels) < 2 or len(hostname) > 253 or any(
            not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label)
            for label in labels
        ):
            raise ValueError('Informe o hostname SMTP ou um IP publico, sem URL ou porta')
        return hostname

    @field_validator('user')
    @classmethod
    def user_valid(cls, value):
        if not value.isascii() or not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('Usuario SMTP invalido')
        return value

    @field_validator('password')
    @classmethod
    def password_valid(cls, value):
        if not value.get_secret_value().isascii():
            raise ValueError('A senha SMTP deve conter somente caracteres ASCII nesta versao')
        return value

    @model_validator(mode='after')
    def sender_valid(self):
        self.sender = self.user if self.sender is None else self.sender
        if not EMAIL.fullmatch(self.sender):
            raise ValueError('Informe smtp.from como e-mail valido quando smtp.user nao for um e-mail')
        return self


class EmailRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    para: str = Field(max_length=254)
    assunto: str = Field(min_length=1, max_length=150)
    texto: str = Field(max_length=100000)
    attachments: list[Attachment] = Field(default_factory=list, max_length=10)
    smtp: SMTPConfig | None = None
    _files: list[bytes] = PrivateAttr(default_factory=list)

    @field_validator('para')
    @classmethod
    def recipient_valid(cls, value):
        if not EMAIL.fullmatch(value):
            raise ValueError('Informe um unico endereco de e-mail valido')
        return value

    @field_validator('assunto')
    @classmethod
    def subject_valid(cls, value):
        if not value.strip() or '\r' in value or '\n' in value or len(value.encode()) > 150:
            raise ValueError('Assunto invalido ou maior que 150 bytes')
        return value

    @model_validator(mode='after')
    def decode_files(self):
        files = []
        total = 0
        for attachment in self.attachments:
            try:
                raw = base64.b64decode(attachment.base64, validate=True)
            except (ValueError, binascii.Error) as error:
                raise ValueError('Anexo com Base64 invalido; envie sem prefixo data:') from error
            total += len(raw)
            if total > MAX_ATTACHMENTS:
                raise ValueError('Os anexos juntos devem ter no maximo 5 MiB')
            files.append(raw)
        self._files = files
        return self


def send_email(settings: Settings, payload: EmailRequest):
    custom = payload.smtp
    if custom is not None:
        settings = replace(settings, host=custom.host, port=custom.port, user=custom.user,
                           password=custom.password.get_secret_value(), sender=custom.sender)
    message = EmailMessage()
    message['From'] = settings.sender
    message['To'] = payload.para
    message['Subject'] = payload.assunto
    message['Date'] = formatdate(localtime=True)
    message['Message-ID'] = make_msgid(domain=settings.sender.rsplit('@', 1)[1])
    message.set_content(payload.texto, charset='utf-8')
    for attachment, raw in zip(payload.attachments, payload._files):
        maintype, subtype = attachment.contentType.split('/', 1)
        message.add_attachment(raw, maintype=maintype, subtype=subtype, filename=attachment.filename)

    transport = PublicSMTP_SSL if custom is not None else smtplib.SMTP_SSL
    smtp = transport(settings.host, settings.port, context=ssl.create_default_context(), timeout=25)
    try:
        smtp.login(settings.user, settings.password)
        refused = smtp.send_message(message, from_addr=settings.sender, to_addrs=[payload.para])
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
    finally:
        # QUIT failing after DATA acceptance must not turn a successful send into a failure.
        try:
            smtp.quit()
        except (smtplib.SMTPException, OSError):
            pass
        finally:
            try:
                smtp.close()
            except (smtplib.SMTPException, OSError):
                pass
    return {'aceitoPeloServidor': True, 'para': payload.para, 'messageId': str(message['Message-ID'])}


@asynccontextmanager
async def lifespan(app):
    app.state.settings = Settings.load()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.post('/enviar-email')
async def email_endpoint(request: Request):
    settings = request.app.state.settings
    received = request.headers.get('authorization', '').encode('utf-8')
    expected = f'Bearer {settings.token}'.encode('utf-8')
    if not hmac.compare_digest(received, expected):
        raise HTTPException(401, 'Token invalido', headers={'WWW-Authenticate': 'Bearer'})
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise HTTPException(415, 'Use Content-Type: application/json')
    content_length = request.headers.get('content-length')
    if content_length:
        try:
            length = int(content_length)
        except ValueError:
            raise HTTPException(400, 'Content-Length invalido') from None
        if length > MAX_BODY:
            raise HTTPException(413, 'JSON maior que 8 MiB')
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY:
            raise HTTPException(413, 'JSON maior que 8 MiB')
        body.extend(chunk)
    try:
        payload = EmailRequest.model_validate_json(body)
    except ValidationError as error:
        # Do not echo attachment contents, passwords or complete request bodies.
        errors = [{'campo': list(e['loc']), 'mensagem': e['msg']} for e in error.errors(include_input=False)]
        raise HTTPException(400, errors) from None
    try:
        return await run_in_threadpool(send_email, settings, payload)
    except SMTPDestinationError as error:
        raise HTTPException(400, str(error)) from None
    except TimeoutError:
        raise HTTPException(504, 'Timeout SMTP; confirme a entrega antes de reenviar') from None
    except (smtplib.SMTPException, OSError) as error:
        logger.error('Falha SMTP: %s (codigo %s)', type(error).__name__, getattr(error, 'smtp_code', '-'))
        raise HTTPException(502, 'Falha no envio SMTP; verifique as credenciais e a conectividade do servidor') from None
