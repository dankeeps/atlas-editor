# Rodar numa VPS própria

Este caminho é para quem já tem (ou vai criar) uma VPS própria e quer o Atlas Editor
rodando nela para acessar de qualquer lugar, em vez de só localmente no seu computador.
Sem EasyPanel, sem nenhuma automação de terceiros: é Docker puro.

## Requisitos da VPS

- Linux com Docker + Docker Compose instalados.
- Pelo menos 4 vCPU / 8 GB RAM para transcrição e render funcionarem com folga
  (o processamento é pesado: FFmpeg, Whisper, remoção de fundo).
- Uma porta liberada (4123 por padrão, ou a que você mapear).

## Subir

```sh
git clone https://github.com/dankeeps/atlas-editor.git
cd atlas-editor/deploy
docker compose up -d --build
```

A build já baixa os modelos de IA (remoção de fundo, pose) durante a construção da
imagem — não precisa rodar `--instalar` separado como na instalação local. Acompanhe com:

```sh
docker compose logs -f web
```

Quando o healthcheck ficar `healthy`, a aplicação responde em `http://SEU-IP:4123`.

## Domínio e HTTPS

O compose não inclui um proxy com TLS — isso depende de como você já administra a
VPS. O jeito mais simples costuma ser o [Caddy](https://caddyserver.com/): aponte o
domínio para o IP da VPS e rode

```sh
caddy reverse-proxy --from seudominio.com --to localhost:4123
```

e o Caddy cuida do certificado sozinho. Se preferir nginx, `deploy/nginx.conf` já
tem um `proxy_pass` pronto para `http://web:4123` — só falta você adicionar o bloco
de TLS (certbot ou equivalente) por cima.

## Dados

Tudo fica no volume nomeado `atlas_data` (projetos, B-rolls, chaves de API, biblioteca).
Ele sobrevive a `docker compose down` e a rebuilds — só é perdido se você apagar o
volume explicitamente (`docker volume rm`).

## Atualizar

```sh
git pull
docker compose up -d --build
```

## Admin master (opcional)

Se essa VPS vai ter mais de uma pessoa usando workspaces separados (ex.: você
oferece o Atlas Editor para vários clientes seus), defina `ATLAS_ADMIN_MASTER` no
ambiente antes de subir — esse e-mail sempre terá acesso a todos os workspaces,
mesmo que a lista de acesso de algum deles seja mexida por engano. Para uso
pessoal (só você usando), deixe em branco — não tem necessidade nenhuma.
