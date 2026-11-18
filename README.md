# email-worker

API HTTP para envio de e-mails por SMTP com TLS na porta **465**. Python + FastAPI, com `smtplib` e `email` da biblioteca padrão para autenticação SMTP, montagem MIME e anexos.

**Somente `API_TOKEN` é obrigatório no deploy.** O SMTP pode ser informado em cada request ou configurado como um perfil padrão opcional no container.

## Funcionalidades e escopo

- Um destinatário por request, com autenticação Bearer.
- Corpo em texto simples UTF-8 e anexos opcionais de qualquer formato.
- SMTP personalizado por request, sem alterar a configuração de outros envios.
- TLS direto na porta 465, com validação do certificado.
- Dockerfile, Docker Compose e endpoint de saúde para Coolify.

**Não há suporte a corpo HTML nesta versão.** O campo `html` é desconhecido e retorna HTTP 400. Se enviar tags HTML dentro de `texto`, elas serão enviadas como texto literal, sem renderizar o design. Não há suporte a STARTTLS/587, CC/BCC, múltiplos destinatários, arquivos por URL, imagens inline/CID, fila persistente ou deduplicação.

## Deploy mínimo

Configure apenas:

```dotenv
API_TOKEN=SUBSTITUA_POR_UM_TOKEN_ALEATORIO
```

Gere um token com:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

O token deve ser mantido em segredo. Recomenda-se gerar pelo menos 32 bytes aleatórios. Não existe token padrão: sem `API_TOKEN`, a aplicação recusa iniciar.

Nesse modo, informe um objeto `smtp` completo em cada envio. `/health` funciona sem configuração SMTP.

## Variáveis de ambiente

Todas são variáveis de **runtime**. Não coloque senhas no Dockerfile, em argumentos de build ou no repositório.

| Variável | Obrigatória no deploy | Descrição |
|---|---|---|
| `API_TOKEN` | **Sim** | Token compartilhado com os clientes HTTP |
| `SMTP_HOST` | Não | Hostname do SMTP padrão, como `smtp.example.com` |
| `SMTP_PORT` | Não | Porta padrão: `465`; vazio também assume `465`; outros valores não são suportados |
| `SMTP_USER` | Não | Usuário do SMTP padrão |
| `SMTP_PASSWORD` | Não | Senha SMTP ou senha de aplicativo do perfil padrão |
| `SMTP_FROM` | Não | Remetente padrão; quando omitido ou vazio, usa `SMTP_USER` |

Para enviar sem o objeto `smtp`, configure um perfil padrão completo:

```dotenv
API_TOKEN=SUBSTITUA_POR_UM_TOKEN_ALEATORIO
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_USER=noreply@example.com
SMTP_PASSWORD=SUBSTITUA_PELA_SENHA_SMTP
SMTP_FROM=noreply@example.com
```

Os três campos `SMTP_HOST`, `SMTP_USER` e `SMTP_PASSWORD` precisam estar preenchidos para usar o perfil padrão. Um perfil ausente ou parcial não impede a aplicação de iniciar: requests com SMTP próprio continuam funcionando. Uma request que tente usar um perfil incompleto retorna HTTP 400, antes de abrir conexão.

Se `SMTP_USER` não for um endereço de e-mail, preencha também `SMTP_FROM` com um endereço válido. Usuário e senha devem conter caracteres ASCII. Valores opcionais fornecidos ainda precisam respeitar o formato esperado: por exemplo, `SMTP_PORT=587` ou uma porta não numérica impedem a inicialização.

## Deploy no Coolify

### Docker Compose

1. Crie uma **Application** a partir deste repositório Git e selecione **Build Pack: Docker Compose**.
2. Use `/` como **Base Directory** e `/compose.yaml` como **Docker Compose Location**.
3. Em **Domains**, selecione o serviço `email-worker` e configure o domínio com a porta interna, por exemplo `https://email-api.example.com:8000`. O proxy recebe as chamadas públicas em `https://email-api.example.com` e encaminha para a porta `8000` do container.
4. Adicione `API_TOKEN` em **Environment Variables** como variável de runtime. Adicione `SMTP_*` se quiser um perfil SMTP padrão. O Coolify gera o `.env` usado pelo serviço.
5. Faça **Deploy** e verifique `https://email-api.example.com/health`. O container usa o `HEALTHCHECK` do Dockerfile.

### Dockerfile

1. Crie uma **Application** a partir deste repositório Git.
2. Selecione **Build Pack: Dockerfile**.
3. Use a raiz do projeto como **Base Directory** e `/Dockerfile` como **Dockerfile Location**.
4. Configure **Ports Exposes: 8000** e associe um domínio HTTPS. O processo escuta em `0.0.0.0:8000`.
5. Adicione `API_TOKEN` como variável de runtime. Adicione as variáveis `SMTP_*` somente se quiser um perfil padrão. Marque token e senhas como secretos quando essa opção estiver disponível.
6. Faça **Deploy**.
7. Se habilitar o Healthcheck do Coolify, use `GET /health`, porta `8000`, status esperado `200`. A imagem já inclui um `HEALTHCHECK` equivalente.

### Requisitos de rede

O servidor precisa permitir saída TCP para os hosts SMTP na porta 465. Configure o proxy para aceitar pelo menos **8 MiB** de corpo e um timeout de pelo menos **60 segundos**. Não é necessário publicar a porta 8000 diretamente no host ao usar o proxy do Coolify.

Depois de atualizar o código ou as variáveis, faça um novo deploy para aplicar a configuração.

## Docker local

Copie `.env.example` para `.env` e preencha `API_TOKEN`. As linhas opcionais de SMTP estão comentadas; descomente e preencha apenas se quiser um perfil padrão. O arquivo `.env` é ignorado pelo Git e pelo build Docker.

```sh
docker compose run --rm --build --publish 127.0.0.1:8000:8000 email-worker
```

A opção `--publish` torna a API acessível em `http://localhost:8000` durante esse teste local. Pressione `Ctrl+C` para encerrar o container.

Sem Compose:

```sh
docker build -t email-worker .
docker run --rm --env-file .env -p 127.0.0.1:8000:8000 email-worker
```

No Windows 11, Docker Desktop inclui Engine, CLI e Compose. Se a instalação do WSL solicitar reinicialização, reinicie antes de executar esses comandos.

## Endpoints

| Método | Caminho | Autenticação | Função |
|---|---|---|---|
| `GET` | `/health` | Não | Verificar o processo HTTP |
| `POST` | `/enviar-email` | Bearer token | Enviar um e-mail |

### GET /health

```sh
curl https://SEU-DOMINIO/health
```

Resposta HTTP 200:

```json
{"status":"ok"}
```

Esse endpoint não testa conectividade, credenciais SMTP ou entrega de e-mails.

### POST /enviar-email: autenticação e headers

```http
Authorization: Bearer SEU_API_TOKEN
Content-Type: application/json
```

Use exatamente `Bearer`, seguido de um espaço e do mesmo token configurado em `API_TOKEN`. Token ausente ou incorreto retorna HTTP 401 antes de processar o corpo. O token da API é independente do usuário e da senha SMTP.

A API não tem usuários individuais, permissões por conta ou expiração automática do token. Para trocá-lo, altere `API_TOKEN`, faça redeploy e atualize os clientes.

### Campos do JSON

| Campo | Tipo | Obrigatório | Regra |
|---|---|---|---|
| `para` | string | Sim | Um único e-mail, até 254 caracteres; listas, nomes de exibição e destinatários separados por vírgula não são aceitos |
| `assunto` | string | Sim | Não vazio, sem CR/LF e até 150 bytes UTF-8 |
| `texto` | string | Sim | Corpo em texto simples, até 100.000 caracteres; pode ser vazio |
| `attachments` | array | Não | Até 10 arquivos; omitido ou `[]` envia sem anexos; `null` não é aceito |
| `smtp` | objeto ou null | Não | Perfil completo para esta request; omitido ou `null` usa o perfil padrão do container |

Os tipos são validados estritamente e campos desconhecidos retornam HTTP 400. Por exemplo, `port: "465"` não substitui `port: 465`.

### Campos de smtp

| Campo | Tipo | Obrigatório dentro de smtp | Regra |
|---|---|---|---|
| `host` | string | Sim | Hostname ou IP público, sem `https://`, caminho ou porta; até 253 caracteres |
| `port` | inteiro | Não | Somente `465`; assume `465` quando omitido |
| `user` | string | Sim | Usuário SMTP, entre 1 e 320 caracteres ASCII, sem caracteres de controle |
| `password` | string | Sim | Senha da conta ou de aplicativo, entre 1 e 4096 caracteres ASCII |
| `from` | string | Não | Um endereço de e-mail simples, até 254 caracteres; assume `smtp.user` quando omitido ou null |

Se `user` for um identificador de login em vez de um e-mail, `from` é obrigatório. O servidor SMTP também precisa autorizar esse remetente.

Quando o objeto `smtp` estiver presente, seus campos **não são combinados** com os defaults do container. Por exemplo, informar apenas `smtp.host` retorna HTTP 400; não reaproveita a senha padrão para outro host. O remetente da request também não herda `SMTP_FROM`.

Credenciais não ASCII retornam HTTP 400 por limitação da autenticação de `smtplib.login`. Isso não restringe acentos no corpo, no assunto ou nos nomes de anexos.

### Campos de cada attachment

| Campo | Tipo | Obrigatório | Regra |
|---|---|---|---|
| `filename` | string | Sim | Nome do arquivo, entre 1 e 180 caracteres, sem caracteres de controle |
| `contentType` | string | Não | MIME no formato `tipo/subtipo`, até 100 caracteres; assume `application/octet-stream` |
| `base64` | string | Sim | Bytes do arquivo codificados em Base64 puro, sem prefixo `data:` ou quebras de linha; pode representar um arquivo vazio |

Exemplos de MIME:

| Formato | contentType |
|---|---|
| PDF | `application/pdf` |
| XML | `application/xml` |
| DOCX | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| XLSX | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| DOC | `application/msword` |
| XLS | `application/vnd.ms-excel` |
| CSV | `text/csv` |
| TXT | `text/plain` |
| ZIP | `application/zip` |
| Outros | `application/octet-stream` |

A API preserva os bytes; não converte documentos nem verifica se a extensão corresponde ao conteúdo. O provedor ou o destinatário pode bloquear determinados tipos de arquivo.

## Exemplo 1: sem anexos, SMTP por request

Funciona com apenas `API_TOKEN` no deploy:

```sh
curl -X POST https://SEU-DOMINIO/enviar-email \
  -H 'Authorization: Bearer SEU_API_TOKEN' \
  -H 'Content-Type: application/json' \
  --data '{"smtp":{"host":"smtp.example.com","user":"noreply@example.com","password":"SENHA_SMTP"},"para":"destinatario@example.com","assunto":"Teste sem anexos","texto":"Olá! Esta mensagem não possui anexos."}'
```

Corpo equivalente, formatado:

```json
{
  "smtp": {
    "host": "smtp.example.com",
    "user": "noreply@example.com",
    "password": "SENHA_SMTP"
  },
  "para": "destinatario@example.com",
  "assunto": "Teste sem anexos",
  "texto": "Olá! Esta mensagem não possui anexos."
}
```

## Exemplo 2: múltiplos anexos, SMTP por request

Envie o mesmo método, URL e headers, com o JSON:

```json
{
  "smtp": {
    "host": "smtp.example.com",
    "port": 465,
    "user": "financeiro@example.com",
    "password": "SENHA_SMTP",
    "from": "financeiro@example.com"
  },
  "para": "destinatario@example.com",
  "assunto": "Documentos em anexo",
  "texto": "Olá! Seguem os documentos.",
  "attachments": [
    {"filename":"mensagem.txt","contentType":"text/plain","base64":"T2xhIQ=="},
    {"filename":"nota.xml","contentType":"application/xml","base64":"PG5vdGE+MTIzPC9ub3RhPg=="}
  ]
}
```

Os arquivos do exemplo contém `Ola!` e `<nota>123</nota>`. Para PDF, DOCX, XLSX ou outros documentos reais, substitua por seus bytes codificados em Base64; não envie o caminho do arquivo no campo `base64`.

Para gerar Base64 com Python:

```python
import base64
from pathlib import Path

conteudo = base64.b64encode(Path("documento.pdf").read_bytes()).decode("ascii")
```

## Exemplo 3: usando o SMTP padrão do container

Exige `SMTP_HOST`, `SMTP_USER` e `SMTP_PASSWORD` configurados no deploy, além do token. Sem anexos:

```json
{
  "para": "destinatario@example.com",
  "assunto": "Teste com perfil padrão",
  "texto": "Este envio usa as variáveis SMTP do container."
}
```

Com anexo:

```json
{
  "para": "destinatario@example.com",
  "assunto": "Documento em anexo",
  "texto": "Segue o documento.",
  "attachments": [
    {"filename":"teste.txt","contentType":"text/plain","base64":"T2xhIQ=="}
  ]
}
```

Sem perfil padrão completo, ambos retornam HTTP 400 com orientação para informar `smtp` na request ou preencher os defaults.

## Respostas e erros

Envio aceito pelo SMTP: HTTP 200.

```json
{
  "aceitoPeloServidor": true,
  "para": "destinatario@example.com",
  "messageId": "<identificador@example.com>"
}
```

Esse resultado confirma a aceitação pelo servidor SMTP, **não a entrega na caixa de entrada**.

| Status | Significado |
|---|---|
| `400` | JSON/campos inválidos, HTML não suportado, SMTP incompleto, defaults ausentes, porta não suportada ou destino por request bloqueado |
| `401` | Token ausente ou incorreto |
| `404` | Caminho inexistente |
| `405` | Método HTTP incorreto |
| `413` | JSON maior que 8 MiB |
| `415` | Content-Type diferente de application/json |
| `502` | Falha de autenticação SMTP, DNS, TLS, conectividade ou rejeição da mensagem |
| `503` | Limite de concorrência HTTP atingido |
| `504` | Timeout SMTP; confirme a entrega antes de repetir |

Os erros de validação podem retornar `detail` como uma lista de campos e mensagens:

```json
{"detail":[{"campo":["smtp","password"],"mensagem":"Field required"}]}
```

Os erros operacionais retornam `detail` como string. Por exemplo, sem SMTP padrão nem objeto `smtp`:

```json
{"detail":"Informe o objeto smtp na request ou configure os defaults SMTP_HOST, SMTP_USER e SMTP_PASSWORD"}
```

Os status gerados pelo servidor HTTP ou proxy podem ter um corpo diferente, como texto no erro de concorrência. Use também o status HTTP para tratar falhas.

A API envia durante a request, sem fila persistente ou tentativas automáticas. Um timeout pode ocorrer depois de o SMTP aceitar a mensagem; repetir automaticamente pode gerar duplicatas. Confirme a entrega antes de reenviar.

## Limites

| Limite | Valor |
|---|---|
| Destinatários | Um por request |
| Anexos | Até 10 por request |
| Total dos anexos originais | 5 MiB = 5.242.880 bytes |
| Corpo JSON inteiro | 8 MiB = 8.388.608 bytes |
| Assunto | Até 150 bytes UTF-8 |
| Texto | Até 100.000 caracteres |
| Concorrência HTTP do Uvicorn | Limite configurado de 20 |

O tamanho dos anexos é calculado **após decodificar o Base64**, somando todos os arquivos. O Base64 aumenta o tamanho transmitido em aproximadamente um terço; os metadados e as credenciais também contam no limite do JSON. Limites do SMTP, do destinatário e do proxy também se aplicam.

## Configuração no n8n

No **HTTP Request**:

| Campo | Valor |
|---|---|
| Method | `POST` |
| URL | `https://SEU-DOMINIO/enviar-email` |
| Authentication | Generic Credential Type → Header Auth |
| Credencial: Name | `Authorization` |
| Credencial: Value | `Bearer SEU_API_TOKEN` |
| Send Body | Ativado |
| Body Content Type | JSON |
| Specify Body | Using JSON |
| Campo JSON em modo Expression | `{{ $json }}` |
| Timeout | `60000` ms |
| Retry On Fail | Desativado |

Para enviar arquivos da aba **Binary**, use antes um **Code node**, modo **Run Once for All Items**:

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
      texto: item.json.texto ?? 'Olá! Seguem os documentos.',
      ...(item.json.smtp ? { smtp: item.json.smtp } : {}),
      attachments,
    },
    pairedItem: { item: i },
  });
}

return saida;
```

Cada item gera um e-mail com todos os arquivos binários daquele item. Sem binários, gera um e-mail sem anexos. Para usar o deploy com apenas `API_TOKEN`, o JSON de entrada também precisa conter `smtp`. Se os arquivos estiverem espalhados em vários itens e você quiser um único e-mail, reúna-os antes desse nó.

## Segurança e operação

- Use HTTPS entre o cliente e a API. O token da API e as credenciais SMTP são autenticações independentes.
- Senhas não são devolvidas nas respostas. O código não registra corpos de requests; configure também o n8n e o proxy para não armazenar credenciais em históricos ou logs.
- Hosts SMTP por request precisam resolver apenas para endereços públicos. Endereços privados, loopback, link-local e multicast são bloqueados; o transporte conecta ao IP validado e preserva o hostname para validar TLS.
- `SMTP_HOST` configurado no container é confiável e pode apontar para um servidor interno, conforme as necessidades do deploy.
- Esta versão usa um único token compartilhado, sem rate limit por cliente, expiração automática ou segregação de contas. Restrinja o acesso ao endpoint aos clientes autorizados.
- O container roda com usuário sem root. Não é necessário persistir volumes para o funcionamento da API.

## Desenvolvimento e testes

Requer Python 3.14, também utilizado na imagem Docker.

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```

No PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Os testes não enviam e-mails reais. Simulam o transporte SMTP e verificam inicialização só com token, autenticação, defaults opcionais, SMTP por request, validação de entradas, TLS, montagem MIME e integridade dos anexos.

## Referências

- [Dockerfile no Coolify](https://coolify.io/docs/applications/builds/dockerfile)
- [Docker Compose no Coolify](https://coolify.io/docs/applications/builds/docker-compose)
- [FastAPI em containers](https://fastapi.tiangolo.com/deployment/docker/)
- [Arquivos binários no Code node do n8n](https://docs.n8n.io/code/cookbook/code-node/get-binary-data-buffer/)
