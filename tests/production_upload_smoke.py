"""Public upload smoke; run inside the deployed container with its environment.

Uploads 2 MiB over 75 seconds, creates one idle Kanban demand, then removes only
this run's artifacts. No AI jobs are started: a new demand now enters the B-roll
queue immediately on creation, so this script pauses the queue for its own
duration (kanban.pausar) and always resumes it afterwards. Credentials stay in memory.
ATLAS_UPLOAD_SMOKE_DELAY_SECONDS=0 enables a fast run; loopback HTTP is accepted
only with that explicit zero delay. Production always uses EDITOR_IA_PUBLIC_URL.
"""
import argparse
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import secrets
import sys
import time
from urllib.parse import urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 20 * 1024**3
SIZE = 2 * 1024**2
CHUNK = 64 * 1024


def emit(**data):
    print(json.dumps(data, ensure_ascii=False), flush=True)


def delay_value(value):
    try:
        delay = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("A duração deve ser 0 ou entre 61 e 300 segundos.") from None
    if not math.isfinite(delay) or (delay != 0 and not 61 <= delay <= 300):
        raise argparse.ArgumentTypeError("A duração deve ser 0 ou entre 61 e 300 segundos.")
    return delay


class PublicClient:
    def __init__(self, origin, cookie, delay):
        url = urlsplit(origin)
        local = url.hostname in ("localhost", "127.0.0.1", "::1")
        if (url.scheme != "https" and not (url.scheme == "http" and local and delay == 0)) or not url.hostname:
            raise RuntimeError("URL HTTPS obrigatória; HTTP local exige duração zero.")
        if url.username or url.password or url.path or url.query or url.fragment:
            raise RuntimeError("A URL pública deve conter apenas a origem.")
        self.origin, self.cookie, self.url = origin, cookie, url

    def connection(self, timeout=150):
        cls = http.client.HTTPSConnection if self.url.scheme == "https" else http.client.HTTPConnection
        return cls(self.url.hostname, self.url.port, timeout=timeout)

    def headers(self):
        return {"Cookie": self.cookie, "Origin": self.origin}

    def call(self, path, body=None, headers=None, timeout=150):
        if not path.startswith("/") or path.startswith("//"):
            raise RuntimeError("Caminho HTTP inválido.")
        h = self.headers()
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        connection = self.connection(timeout)
        try:
            method = "POST" if body is not None or "Content-Length" in h else "GET"
            connection.request(method, path, body=body, headers=h)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def json(self, path, body=None):
        status, data = self.call(path, body)
        if status != 200:
            raise RuntimeError("Resposta HTTP inesperada: " + str(status))
        return json.loads(data)

    def upload(self, path, payload, delay):
        connection = self.connection()
        start = time.monotonic()
        try:
            connection.putrequest("POST", path)
            for key, value in self.headers().items():
                connection.putheader(key, value)
            connection.putheader("Content-Type", "application/octet-stream")
            connection.putheader("Content-Length", str(len(payload)))
            connection.endheaders()
            count = math.ceil(len(payload) / CHUNK)
            next_progress = 20
            emit(stage="upload_started", total_bytes=len(payload), duration_seconds=delay)
            for index in range(count):
                target = start + delay * index / max(1, count - 1)
                while time.monotonic() < target:
                    time.sleep(max(0, min(0.5, target - time.monotonic())))
                connection.send(payload[index * CHUNK:(index + 1) * CHUNK])
                elapsed = time.monotonic() - start
                if elapsed >= next_progress:
                    emit(stage="upload_progress", elapsed_seconds=round(elapsed, 1),
                         sent_bytes=min((index + 1) * CHUNK, len(payload)))
                    next_progress += 20
            response = connection.getresponse()
            result = response.read()
            elapsed = time.monotonic() - start
            emit(stage="upload_eof", elapsed_seconds=round(elapsed, 1), status=response.status)
            return response.status, result, elapsed
        finally:
            connection.close()


def artifact_paths(root, token, name):
    if not re.fullmatch(r"smokeupload[a-f0-9]{24}", token) or name != "Atlas upload smoke " + token:
        raise RuntimeError("Identidade do teste inválida.")
    root = Path(root).resolve()
    return root / ".entrada" / token / "anuncio_01.mp4", root / ".kanban" / "lotes" / token / (name + ".mp4")


def cleanup_owned(kanban, root, token, name):
    """Locked, exact cleanup; avoids apagar(), which prunes unrelated orphan lots.

    Also handles failures between moving the uploaded file and creating its card.
    Any externally started or reassigned card makes cleanup refuse deletion.
    """
    staged, saved = artifact_paths(root, token, name)
    targets = []
    for expected in (staged, saved):
        folder = expected.parent
        if folder.resolve() != folder or folder.is_symlink():
            raise RuntimeError("Limpeza recusada: diretório do teste foi redirecionado.")
        if not folder.exists():
            continue
        for item in folder.iterdir():
            partial = expected == staged and re.fullmatch(r"anuncio_01\.mp4\.[a-f0-9]+\.part", item.name)
            if (item != expected and not partial) or item.is_symlink() or not item.is_file():
                raise RuntimeError("Limpeza recusada: conteúdo inesperado no diretório do teste.")
            targets.append(item)
    with kanban.mexer() as board:
        cards = [c for c in board["cards"].values() if Path(c.get("video") or "").resolve().parent in (saved.parent, staged.parent)]
        ids = {c["id"] for c in cards}
        lots = {c["lote"] for c in cards}
        lots.update(lid for lid, lot in board["lotes"].items() if lot.get("nome") == name)
        for card in cards:
            if (Path(card.get("video") or "").resolve() != saved or card.get("nome") != name or card.get("projeto")
                    or card.get("coluna") != "broll" or card.get("estado") != "espera" or card.get("leva")):
                raise RuntimeError("Limpeza recusada: demanda do teste foi alterada externamente.")
        if any(c.get("lote") in lots and c.get("id") not in ids for c in board["cards"].values()):
            raise RuntimeError("Limpeza recusada: lote do teste contém outro anúncio.")
        if any(board["lotes"].get(lid, {}).get("nome") != name for lid in lots):
            raise RuntimeError("Limpeza recusada: lote do teste foi alterado.")
        for path in targets:
            path.unlink(missing_ok=True)
        for cid in ids:
            board["cards"].pop(cid, None)
        for lid in lots:
            board["lotes"].pop(lid, None)
    for folder in (staged.parent, saved.parent):
        if folder.exists():
            folder.rmdir()  # Never recursively remove a directory.


def run(delay):
    sys.path[:0] = [str(ROOT / "app"), str(ROOT / "lib")]
    import cloud
    import comum
    import kanban

    if not cloud.enabled() or not os.environ.get("EDITOR_IA_PUBLIC_URL"):
        raise RuntimeError("Configuração cloud ausente; nenhuma requisição foi enviada.")
    cloud.configuration("0.0.0.0")
    admin = next((u for u in cloud.users() if u.get("role") == "admin"), None)
    if admin is None:
        raise RuntimeError("Nenhum administrador ativo disponível.")
    client = PublicClient(os.environ["EDITOR_IA_PUBLIC_URL"].rstrip("/"), cloud.COOKIE + "=" + cloud.token(admin), delay)
    token = "smokeupload" + secrets.token_hex(12)
    name = "Atlas upload smoke " + token
    staged, saved = artifact_paths(comum.RAIZ, token, name)
    if staged.parent.exists() or saved.parent.exists():
        raise RuntimeError("Identidade do teste já existe; nenhuma alteração foi feita.")
    checks = []

    def require(label, condition):
        if not condition:
            raise RuntimeError("Falhou: " + label)
        checks.append(label)

    def find_file(expected, source):
        files = client.json("/api/arquivos")["arquivos"]
        item = next((f for f in files if f.get("path") == str(expected)), None)
        require("arquivo_" + source, item is not None and item.get("tamanho") == SIZE)
        status, data = client.call(item["url"])
        require("hash_" + source, status == 200 and len(data) == SIZE and hashlib.sha256(data).hexdigest() == digest)
        return item

    pattern = b"Atlas upload smoke synthetic fixture\n"
    payload = (pattern * (SIZE // len(pattern) + 1))[:SIZE]
    require("payload_2_mib", len(payload) == SIZE)
    digest = hashlib.sha256(payload).hexdigest()
    path = "/api/kanban/arquivo?" + urlencode(dict(id=token, n=1, nome="smoke.mp4"))
    error = None
    kanban.pausar(True)  # Uma demanda nova já cai direto na fila do B-roll; pausamos para o teste continuar sem IA.
    try:
        require("limite_publicado_20_gib", client.json("/api/config").get("max_upload_bytes") == LIMIT)
        emit(stage="limit_probe", declared_bytes=LIMIT + 1, sent_bytes=0)
        # The proxy must reject the headers before waiting for the advertised body.
        # Expect + close prevents HTTP/1 proxy draining of an intentionally absent body.
        status, _ = client.call(path, headers={"Content-Length": str(LIMIT + 1),
                               "Expect": "100-continue", "Connection": "close"}, timeout=12)
        require("limite_413_sem_corpo", status == 413)  # A proxy may return HTML.
        status, data, elapsed = client.upload(path, payload, delay)
        require("upload_200", status == 200 and json.loads(data).get("ok") is True)
        require("upload_duracao", delay == 0 or elapsed >= delay)
        find_file(staged, "entrada")
        result = client.json("/api/kanban/criar", dict(id=token, expert="Verificacao Atlas", oferta="Upload smoke",
                             nome=name, fontes=["youtube"], anuncios=[dict(n=1, nome=name)]))
        require("demanda_criada", result.get("ok") is True and bool(result.get("lote")))
        board = client.json("/api/kanban")
        cards = [c for c in board["cards"] if c.get("lote") == result["lote"]]
        require("card_parado", len(cards) == 1 and cards[0].get("nome") == name
                and Path(cards[0].get("video") or "").resolve() == saved and cards[0].get("coluna") == "broll"
                and cards[0].get("estado") == "espera" and not cards[0].get("projeto") and not cards[0].get("leva"))
        require("lote_visivel", any(l.get("id") == result["lote"] and l.get("nome") == name for l in board["lotes"]))
        saved_item = find_file(saved, "kanban")
        require("origem_anuncio", saved_item.get("origem") == "Anúncio")
    except Exception as exc:
        error = exc
    finally:
        try:
            cleanup_owned(kanban, comum.RAIZ, token, name)
            emit(stage="cleanup", ok=True)
        except Exception:
            emit(stage="cleanup", ok=False, artifact_token=token)
            raise RuntimeError("A limpeza exclusiva do teste não pôde ser concluída.") from None
        finally:
            kanban.pausar(False)
    if error is not None:
        raise error
    require("arquivos_removidos", not staged.parent.exists() and not saved.parent.exists())
    board = client.json("/api/kanban")
    require("demanda_removida", all(c.get("lote") != result["lote"] for c in board["cards"])
            and all(l.get("id") != result["lote"] for l in board["lotes"]))
    require("download_removido", client.call(saved_item["url"])[0] == 404)
    emit(ok=True, checks=checks, total=len(checks), uploaded_bytes=SIZE, elapsed_seconds=round(elapsed, 1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=delay_value, default=os.environ.get("ATLAS_UPLOAD_SMOKE_DELAY_SECONDS", "75"))
    args = parser.parse_args()
    try:
        run(args.duration)
    except Exception as exc:
        # Never expose response bodies, cookies, request objects, or stack locals.
        emit(ok=False, erro=str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
