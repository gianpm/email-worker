# email-worker

Python + FastAPI. SMTP SSL/TLS na porta 465, com validacao do certificado.
O envio e a montagem de anexos usam a biblioteca padrao do Python.

## Variaveis de ambiente

Configure os valores em runtime; nunca inclua credenciais no Dockerfile, em build arguments ou no repositorio.

| Variavel | Obrigatoria | Valor recomendado / descricao |
|---|---|---|
| `API_TOKEN` | Sim | Token aleatorio com pelo menos 32 bytes, compartilhado com o cliente HTTP |
| `SMTP_HOST` | Sim | Hostname real do servidor SMTP, por exemplo `smtp.example.com` |
| `SMTP_PORT` | Nao | `465`; esta versao aceita apenas TLS implicito nessa porta |
| `SMTP_USER` | Sim | Usuario SMTP, geralmente o endereco de e-mail completo |
| `SMTP_PASSWORD` | Sim | Senha SMTP ou senha de aplicativo fornecida pelo provedor |
| `SMTP_FROM` | Nao | Remetente autorizado pelo provedor; usa `SMTP_USER` quando omitido |

A conexao valida o certificado TLS do servidor. O endpoint de envio e `POST /enviar-email` e exige `Authorization: Bearer SEU_API_TOKEN`.

## Deploy no Coolify

1. Coloque os arquivos deste projeto em um repositorio Git acessível.
2. Crie uma Application com esse repositorio e selecione o build pack **Dockerfile**.
3. Base Directory: raiz do projeto. Dockerfile Location: `/Dockerfile`. Ports Exposes: `8000`.
4. Configure um dominio HTTPS para a aplicacao. Nao e necessario publicar a porta 8000 diretamente no host.
5. Adicione as variaveis abaixo como variaveis de **runtime**. Nao coloque senhas no Dockerfile nem em build arguments.
6. Faca Deploy. Se habilitar o Healthcheck do Coolify, use GET `/health`, porta `8000`, status esperado `200`.

```dotenv
API_TOKEN=SUBSTITUA_POR_UM_TOKEN_ALEATORIO
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_USER=noreply@example.com
SMTP_PASSWORD=SUBSTITUA_PELA_SENHA_SMTP
SMTP_FROM=noreply@example.com
```

Gere um token com `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
A aplicacao recusa iniciar se faltar uma variavel obrigatoria. `/health` verifica o processo HTTP; nao envia e-mail nem testa o SMTP.
O servidor/VPS precisa permitir conexoes de saida para `smtp.example.com:465`.
Configure o proxy com limite de corpo de pelo menos 8 MiB e timeout de pelo menos 60 segundos.

## SMTP personalizado por request

O objeto `smtp` e opcional. Quando omitido ou `null`, o envio usa as variaveis `SMTP_*` do container. A configuracao padrao do container continua obrigatoria para iniciar a aplicacao.

Quando `smtp` estiver presente, envie `host`, `user` e `password` completos: os campos nao sao combinados com as credenciais padrao. `port` pode ser omitido e assume `465`. `from` pode ser omitido quando `user` for um e-mail; nesse caso, o remetente sera o proprio `user` da request, sem herdar `SMTP_FROM` do container.

```json
{
  "smtp": {
    "host": "smtp.example.com",
    "port": 465,
    "user": "financeiro@example.com",
    "password": "SENHA_DESSA_CONTA",
    "from": "financeiro@example.com"
  },
  "para": "destinatario@example.com",
  "assunto": "Documentos em anexo",
  "texto": "Seguem os documentos.",
  "attachments": [
    {"filename": "teste.txt", "contentType": "text/plain", "base64": "T2xhIQ=="}
  ]
}
```

O Bearer token continua obrigatorio para todos os envios. Somente a porta **465 com TLS direto** e aceita; a porta 587 e STARTTLS nao estao implementados. O certificado do SMTP e validado contra o hostname informado. Os campos `smtp.user` e `smtp.password` devem usar caracteres ASCII, conforme o suporte de autenticacao de `smtplib.login`; caracteres nao suportados retornam HTTP 400 antes da conexao. Texto e nomes de anexos continuam aceitando acentos.

Hosts SMTP fornecidos por request devem resolver apenas para enderecos publicos. Destinos locais, privados, link-local e multicast sao rejeitados antes da conexao; a conexao usa o IP validado, preservando o hostname para TLS. A configuracao `SMTP_HOST` do container e considerada confiavel e pode apontar para um servidor interno quando isso for necessario ao deploy.

Envie credenciais por HTTPS. A API nao registra o corpo das requests nem devolve as senhas nas respostas. Configure tambem o n8n e o proxy para nao armazenar corpos de requests com senhas em historicos ou logs. As configuracoes de uma request nao alteram os envios seguintes.

## Request do n8n

- HTTP Request: `POST https://SEU-DOMINIO/enviar-email`.
- Authentication: Generic Credential Type > Header Auth.
- Credencial: Name `Authorization`, Value `Bearer SEU_API_TOKEN`.
- Send Body: JSON > Using JSON. Campo em modo Expression: `{{ $json }}`.
- Timeout sugerido: 60000 ms. Deixe Retry On Fail desativado para evitar duplicar um envio de resultado incerto.

Corpo:

```json
{
  "para": "destinatario@example.com",
  "assunto": "Documentos em anexo",
  "texto": "Ola! Seguem os documentos.",
  "attachments": [
    {
      "filename": "teste.txt",
      "contentType": "text/plain",
      "base64": "T2xhIQ=="
    }
  ]
}
```

`attachments` e opcional. Cada request aceita um destinatario, ate 10 arquivos e ate 5 MiB de anexos decodificados no total.
Base64 puro: sem prefixo `data:` e sem quebras de linha. JSON inteiro limitado a 8 MiB.
Texto limitado a 100000 caracteres e assunto a 150 bytes UTF-8.
O limite do servidor SMTP tambem se aplica. A API envia texto simples e anexos; nao aceita HTML nesta versao.

Code node anterior ao HTTP Request, modo **Run Once for All Items**:

```javascript
const items = $input.all();
const saida = [];
for (let i = 0; i < items.length; i++) {
  const item = items[i];
  if (!item.json.para) throw new Error(`Informe json.para no item ${i}`);
  const attachments = [];
  for (const [campo, arquivo] of Object.entries(item.binary ?? {})) {
    const buffer = await this.helpers.getBinaryDataBuffer(i, campo);
    attachments.push({
      filename: arquivo.fileName || `${campo}.bin`,
      contentType: arquivo.mimeType || 'application/octet-stream',
      base64: buffer.toString('base64'),
    });
  }
  saida.push({
    json: {
      para: item.json.para,
      assunto: item.json.assunto || 'Documentos em anexo',
      texto: item.json.texto || 'Ola! Seguem os documentos.',
      ...(item.json.smtp ? { smtp: item.json.smtp } : {}),
      attachments,
    },
    pairedItem: { item: i },
  });
}
return saida;
```

Cada item gera um e-mail com todos os seus arquivos binarios. Para enviar varios itens em um unico e-mail, agrupe os arquivos antes.

Retorno HTTP 200:

```json
{"aceitoPeloServidor": true, "para": "destinatario@example.com", "messageId": "<identificador@example.com>"}
```

O retorno indica que o SMTP aceitou a mensagem, nao confirma a entrega na caixa de entrada.
A API envia durante a request, sem fila persistente nem deduplicacao. Um timeout pode ocorrer depois de o SMTP aceitar a mensagem; confirme antes de reenviar.
Erros: 400 dados invalidos; 401 token invalido; 413 JSON grande; 415 Content-Type incorreto; 502 falha SMTP/TLS; 504 timeout; 503 limite de concorrencia HTTP.

## Docker e Docker Compose

Docker Desktop para Windows 11 inclui Docker Engine, CLI e Compose. Se a instalacao do WSL 2 solicitar reinicializacao, reinicie antes de executar os comandos.

Copie `.env.example` para `.env`, preencha as credenciais reais e execute:

```sh
docker compose up -d --build
docker compose logs -f
```

A API fica em `http://localhost:8000`. O Compose publica a porta apenas em `127.0.0.1` para uso local. Para parar, execute `docker compose down`.

No Coolify, use o Dockerfile e o dominio HTTPS configurado na plataforma; nao e necessario publicar a porta 8000 no host.

### Sem Compose

Copie `.env.example` para `.env` e preencha os valores.

```sh
docker build -t email-worker .
docker run --rm --env-file .env -p 127.0.0.1:8000:8000 email-worker
```

Teste sem anexos:

```sh
curl -X POST http://localhost:8000/enviar-email \
  -H 'Authorization: Bearer SEU_API_TOKEN' \
  -H 'Content-Type: application/json' \
  --data '{"para":"destinatario@example.com","assunto":"Teste","texto":"Ola!"}'
```

## Testes

Requer Python 3.14, a mesma versao utilizada na imagem Docker.

```sh
python -m venv .venv
# Linux/macOS: .venv/bin/python; Windows: .venv\Scripts\python.exe
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```

Os testes simulam o transporte SMTP: exercitam a API HTTP, token, configuracao por request, isolamento das credenciais, validacao, MIME real e preservacao dos bytes. Tambem verificam o bloqueio de destinos internos e a conexao ao IP validado com o hostname original no TLS. Nao enviam e-mails.

Referencias: https://coolify.io/docs/applications/builds/dockerfile e https://fastapi.tiangolo.com/deployment/docker/

No PowerShell, use:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
