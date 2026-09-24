"""Post-deploy HTTPS smoke test. Uses an in-memory session; never prints credentials.

Run inside the deployed container with the production environment. Only creates
and removes its own tiny text upload; does not trigger paid AI or modify projects.
"""
import json
import os
from pathlib import Path
import secrets
import sys
from urllib import request, error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import cloud


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    origin = os.environ.get("EDITOR_IA_PUBLIC_URL", "").rstrip("/")
    if not origin.startswith("https://"):
        raise RuntimeError("A URL pública HTTPS é obrigatória.")
    cloud.configuration("0.0.0.0")
    admin = next((u for u in cloud.users() if u.get("role") == "admin"), None)
    if not admin:
        raise RuntimeError("Nenhum administrador ativo disponível.")
    cookie = cloud.COOKIE + "=" + cloud.token(admin)
    opener = request.build_opener(NoRedirect())
    checks = []
    uploaded = None

    def call(path, body=None, authenticated=True, origin_header=True, headers=None):
        h = {}
        if authenticated: h["Cookie"] = cookie
        if origin_header: h["Origin"] = origin
        if isinstance(body, dict):
            body = json.dumps(body).encode(); h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = request.Request(origin + path, data=body, headers=h)
        try: response = opener.open(req, timeout=150)
        except error.HTTPError as e: response = e
        with response:
            data = response.read()
            return response.status, response.headers, data

    def require(name, condition):
        if not condition: raise RuntimeError("Falhou: " + name)
        checks.append(name)

    try:
        status, _, data = call("/api/health", authenticated=False)
        require("saude_publica", status == 200 and json.loads(data) == {"ok": True})
        status, headers, _ = call("/", authenticated=False)
        require("login_obrigatorio", status in (302, 303) and headers.get("Location") == "/login")
        require("api_protegida", call("/api/projetos", authenticated=False)[0] == 401)
        status, _, data = call("/login", authenticated=False)
        require("pagina_login", status == 200 and b"Atlas Editor" in data)
        status, _, data = call("/")
        require("pagina_atlas", status == 200 and b"Atlas Editor" in data)
        for label, path in (("usuario", "/api/auth/me"), ("projetos", "/api/projetos"),
                            ("kanban", "/api/kanban"), ("arquivos", "/api/arquivos"),
                            ("anuncios", "/api/anuncios-arquivos"), ("ofertas", "/api/ofertas"),
                            ("buscas", "/api/buscas"), ("biblioteca", "/api/biblioteca"),
                            ("custos", "/api/custos"), ("configuracao", "/api/config"),
                            ("chat", "/api/chats"), ("status", "/api/status"),
                            ("templates", "/api/estilos?completo=1")):
            status, _, data = call(path)
            require(label, status == 200)
            json.loads(data)
        status, _, data = call("/api/estilo/amostra?e=ultradinamico")
        require("amostra_template", status == 200 and data.startswith(b"\xff\xd8"))
        status, _, data = call("/fontes/AvenirNext-Bold.ttf")
        require("fonte_original", status == 200 and len(data) > 1000)
        require("csrf", call("/api/chat/novo", {}, origin_header=False)[0] == 403)
        require("terminal_antigo_ausente", call("/claude-code/term")[0] == 404)
        require("arquivo_sistema_bloqueado", call("/f?p=%2Fetc%2Fpasswd")[0] == 404)
        payload = b"Atlas Editor post-deploy smoke test\n"
        name = "atlas-verificacao-" + secrets.token_hex(12) + ".txt"
        status, _, data = call("/api/arquivos/upload?nome=" + name, payload)
        require("upload", status == 200)
        uploaded = json.loads(data)["arquivo"]
        status, _, data = call(uploaded["url"])
        require("download_identico", status == 200 and data == payload)
        status, headers, data = call(uploaded["url"], headers={"Range": "bytes=6-11"})
        require("download_range", status == 206 and data == payload[6:12] and headers.get("Content-Range", "").startswith("bytes 6-11/"))
        status, _, data = call("/api/arquivos")
        require("upload_na_aba_arquivos", status == 200 and any(f["id"] == uploaded["id"] for f in json.loads(data)["arquivos"]))
        status, _, _ = call("/api/arquivos/apagar", dict(tipo="arquivo", arquivo=uploaded["path"]))
        require("remocao_teste", status == 200)
        require("remocao_confirmada", call(uploaded["url"])[0] == 404)
        uploaded = None
        print(json.dumps(dict(ok=True, checks=checks, total=len(checks)), ensure_ascii=False))
    finally:
        if uploaded:
            call("/api/arquivos/apagar", dict(tipo="arquivo", arquivo=uploaded["path"]))


if __name__ == "__main__":
    try: main()
    except Exception as exc:
        # HTTP URLs/cookies and stack locals are deliberately excluded from logs.
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print(json.dumps(dict(ok=False, erro=message), ensure_ascii=False))
        sys.exit(1)
