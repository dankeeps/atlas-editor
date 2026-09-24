"""HTTP regression checks for the cloud boundary and original browser upload flows."""
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = tempfile.TemporaryDirectory(prefix="atlas-http-")
BASE = Path(FIXTURE.name)
PORT = int(os.environ.get("ATLAS_TEST_PORT", "4123"))
ORIGIN = f"http://127.0.0.1:{PORT}"
PASSWORD = "atlas-test-password"
# Generated independently by node:crypto scryptSync; salt is ASCII, not decoded hex.
PASSWORD_HASH = "scrypt$32768$8$1$0123456789abcdef0123456789abcdef$c7af8af7fb03b44bd3b7cf7817ca8e7425ed5543d9c6087d07baf4467ce9d083"
os.environ.update(ESTUDIO_RAIZ=str(BASE / "projetos"), ESTUDIO_BROLLS=str(BASE / "brolls"),
                  ESTUDIO_BIBLIOTECA=str(BASE / "brolls" / "biblioteca"), ESTUDIO_CHAVES=str(BASE / "private" / "chaves.json"),
                  ESTUDIO_PORTA=str(PORT), EDITOR_IA_ADMIN_EMAIL="admin@example.test", EDITOR_IA_ADMIN_PASSWORD_HASH=PASSWORD_HASH,
                  EDITOR_IA_SESSION_SECRET="fixture-session-secret-32-bytes-minimum", EDITOR_IA_PUBLIC_URL=ORIGIN)
sys.path.insert(0, str(ROOT / "app"))
import cloud
import servidor


class CloudHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cloud.configuration("0.0.0.0")
        for path in (servidor.comum.RAIZ, servidor.biblioteca.BROLLS, servidor.biblioteca.RAIZ):
            Path(path).mkdir(parents=True, exist_ok=True)
        cls.server = servidor.ThreadingHTTPServer(("127.0.0.1", PORT), servidor.H)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        response, body = cls.request("POST", "/api/auth/login", dict(email="admin@example.test", password=PASSWORD), auth=False)
        assert response.status == 200, body
        cls.cookie = response.getheader("Set-Cookie").split(";", 1)[0]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
        FIXTURE.cleanup()

    @classmethod
    def request(cls, method, path, body=None, auth=True, headers=None):
        h = dict(Origin=ORIGIN)
        if auth and hasattr(cls, "cookie"): h["Cookie"] = cls.cookie
        if isinstance(body, dict): body = json.dumps(body).encode(); h["Content-Type"] = "application/json"
        h.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
        connection.request(method, path, body=body, headers=h)
        response = connection.getresponse(); data = response.read(); connection.close()
        try: data = json.loads(data)
        except (ValueError, UnicodeError): pass
        return response, data

    def test_auth_boundary_and_csrf(self):
        self.assertTrue(cloud.verify_password(PASSWORD, PASSWORD_HASH))
        self.assertFalse(cloud.verify_password("wrong", PASSWORD_HASH))
        self.assertEqual(self.request("GET", "/api/health", auth=False)[0].status, 200)
        self.assertEqual(self.request("GET", "/api/projetos", auth=False)[0].status, 401)
        self.assertEqual(self.request("GET", "/", auth=False)[0].status, 303)
        self.assertEqual(self.request("GET", "/login", auth=False)[0].status, 200)
        self.assertEqual(self.request("GET", "/api/auth/me")[1]["role"], "admin")
        self.assertEqual(self.request("POST", "/api/chat/novo", {}, headers={"Origin": "https://evil.test"})[0].status, 403)
        self.assertEqual(self.request("POST", "/api/chat/novo", {}, headers={"Origin": ""})[0].status, 403)
        self.assertEqual(self.request("GET", "/api/arquivos", headers={"Cookie": self.cookie + "tampered"})[0].status, 401)

    def test_upload_listing_reuse_range_and_delete(self):
        media = b"test-video-contents"
        response, result = self.request("POST", "/api/arquivos/upload?nome=meu-video.mp4", media)
        self.assertEqual(response.status, 200, result)
        item = result["arquivo"]
        self.assertEqual(Path(item["path"]).read_bytes(), media)
        self.assertTrue(any(f["path"] == item["path"] for f in self.request("GET", "/api/arquivos")[1]["arquivos"]))
        response, data = self.request("GET", item["url"], headers={"Range": "bytes=5-9"})
        self.assertEqual((response.status, data), (206, media[5:10]))
        self.assertEqual(self.request("GET", item["url"], headers={"Range": "bytes=999-"})[0].status, 416)
        self.assertEqual(self.request("GET", item["url"], headers={"Range": "bytes=-4"})[1], media[-4:])
        response, result = self.request("POST", "/api/arquivos/usar", dict(arquivo=item["path"], destino="kanban", id="reuse1234", n=0))
        self.assertEqual(response.status, 200, result)
        self.assertEqual(Path(result["arquivo"]).read_bytes(), media)
        # Kanban only records new demands, so this creates no paid API job.
        response, result = self.request("POST", "/api/kanban/criar", dict(id="reuse1234", expert="Teste", oferta="Oferta", anuncios=[dict(n=0, nome="Meu anúncio")]))
        self.assertEqual(response.status, 200, result)
        files = self.request("GET", "/api/arquivos")[1]["arquivos"]
        integrated = next(f for f in files if f["origem"] == "Anúncio" and "/lotes/reuse1234/" in f["path"])
        self.assertFalse(integrated["pode_apagar"])
        self.assertEqual(self.request("POST", "/api/arquivos/apagar", dict(tipo="arquivo", arquivo=integrated["path"]))[0].status, 400)
        self.assertEqual(self.request("POST", "/api/arquivos/apagar", dict(tipo="arquivo", arquivo=item["path"]))[0].status, 200)
        self.assertFalse(Path(item["path"]).exists())
        self.assertTrue(Path(integrated["path"]).exists())

    def test_original_uploads_and_safe_paths(self):
        for endpoint in ("/api/novo/arquivo?id=novotest1&tipo=video&nome=video.mp4", "/api/busca/arquivo?id=buscatest1&n=0&nome=video.mov"):
            response, body = self.request("POST", endpoint, b"sample-media")
            self.assertEqual(response.status, 200, body)
        _, chat = self.request("POST", "/api/chat/novo", {})
        response, result = self.request("POST", f"/api/chat/arquivo?id={chat['id']}&nome=nota.txt", b"a note")
        self.assertEqual(response.status, 200, result)
        self.assertEqual(self.request("GET", "/f?" + urlencode(dict(p=result["caminho"])))[1], b"a note")
        for path in (str(ROOT / "app" / "servidor.py"), "/etc/passwd", str(BASE / "private" / "chaves.json"), str(ROOT / "config.json")):
            self.assertEqual(self.request("GET", "/f?" + urlencode(dict(p=path)))[0].status, 404)
        for name in ("..", "../escape", "/absolute", "../projetos-evil/x"):
            with self.assertRaises(ValueError): servidor.pasta_proj(name)
        with self.assertRaises(ValueError): servidor.slug_nome("..")
        self.assertEqual(self.request("POST", "/api/arquivos/usar", dict(arquivo="/etc/passwd", id="reuse1234", destino="novo"))[0].status, 400)
        self.assertEqual(self.request("POST", "/api/arquivos/upload?nome=attack.html", b"<script>")[0].status, 400)

    def test_kanban_upload_creates_stopped_demand_and_lists_original_file(self):
        token = "kanbanupload1234"
        media = b"kanban upload fixture: original video bytes"
        endpoint = "/api/kanban/arquivo?" + urlencode(dict(id=token, n=1, nome="Anúncio grande.mp4"))
        response, result = self.request("POST", endpoint, media)
        self.assertEqual(response.status, 200, result)
        staged = Path(servidor.ENTRADA) / token / "anuncio_01.mp4"
        self.assertEqual(staged.read_bytes(), media)
        files = self.request("GET", "/api/arquivos")[1]["arquivos"]
        entry = next(item for item in files if item["path"] == str(staged.resolve()))
        self.assertEqual(entry["origem"], "Aguardando projeto")
        # No kanban worker is started by this HTTP fixture, and creation itself
        # must not start paid generation, transcription, or another subprocess.
        with patch.object(servidor.subprocess, "Popen", side_effect=AssertionError("Creating a demand must remain idle")):
            response, result = self.request("POST", "/api/kanban/criar",
                dict(id=token, expert="Upload", oferta="Grande", nome="Lote de upload",
                     estilo="ultradinamico", fontes=["youtube"], anuncios=[dict(n=1, nome="Anúncio upload")]))
        self.assertEqual(response.status, 200, result)
        cards = self.request("GET", "/api/kanban")[1]["cards"]
        card = next(item for item in cards if item["nome"] == "Anúncio upload")
        self.assertEqual((card["coluna"], card["estado"], card["projeto"]), ("broll", "espera", None))
        self.assertFalse(staged.exists())
        self.assertEqual(Path(card["video"]).read_bytes(), media)
        files = self.request("GET", "/api/arquivos")[1]["arquivos"]
        saved = next(item for item in files if item["path"] == str(Path(card["video"]).resolve()))
        self.assertEqual(saved["origem"], "Anúncio")
        self.assertEqual(self.request("GET", saved["url"])[1], media)
        self.assertFalse(any(item["path"].endswith(".part") for item in files))

    def test_upload_limit_is_published_and_oversize_headers_return_413_without_body(self):
        limit = 20 * 1024**3
        with patch.object(cloud, "MAX_UPLOAD", limit):
            response, config = self.request("GET", "/api/config")
            self.assertEqual(response.status, 200, config)
            self.assertEqual(config["max_upload_bytes"], limit)
            for endpoint in ("/api/kanban/arquivo?id=hugeheader01&n=1&nome=large.mp4",
                             "/api/busca/arquivo?id=hugeheader02&n=1&nome=large.mp4",
                             "/api/novo/arquivo?id=hugeheader03&tipo=video&nome=large.mp4",
                             "/api/arquivos/upload?nome=large.mp4"):
                with self.subTest(endpoint=endpoint):
                    response, result = self.request("POST", endpoint, headers={"Content-Length": str(limit + 1)})
                    self.assertEqual(response.status, 413, result)
                    self.assertEqual(result["max_upload_bytes"], limit)
                    self.assertTrue(result["erro"])
        self.assertEqual(list(BASE.rglob("*.part")), [])

    def test_large_kanban_header_with_insufficient_disk_returns_507_without_body(self):
        token = "diskfullkanban1"
        folder = Path(servidor.ENTRADA) / token
        folder.mkdir(parents=True)
        existing = folder / "anuncio_01.mp4"
        existing.write_bytes(b"previous complete upload")
        with patch.object(cloud.shutil, "disk_usage", return_value=SimpleNamespace(total=100, used=100, free=0)):
            response, result = self.request("POST", f"/api/kanban/arquivo?id={token}&n=1&nome=large.mp4",
                                            headers={"Content-Length": "11570000000"})
        self.assertEqual(response.status, 507, result)
        self.assertTrue(result["erro"])
        self.assertEqual(existing.read_bytes(), b"previous complete upload")
        self.assertEqual(list(folder.glob("*.part")), [])

    def test_interrupted_upload_does_not_replace_file(self):
        target = BASE / "existing.mp4"; target.write_bytes(b"complete")
        class Interrupted:
            headers = {"Content-Length": "30"}
            rfile = io.BytesIO(b"short")
        with self.assertRaises(ValueError): cloud.receive(Interrupted(), str(target))
        self.assertEqual(target.read_bytes(), b"complete")
        self.assertEqual(list(BASE.glob("existing.mp4.*.part")), [])

    def test_migrated_users_and_disabled_account(self):
        path = BASE / "users.json"
        path.write_text(json.dumps([dict(email="team@example.test", password_hash=PASSWORD_HASH, role="editor", active=True),
                                    dict(email="admin@example.test", password_hash=PASSWORD_HASH, role="admin", active=False)]))
        with patch.dict(os.environ, {"ESTUDIO_USERS_FILE": str(path)}):
            self.assertEqual([u["email"] for u in cloud.users()], ["team@example.test"])
            response, body = self.request("POST", "/api/auth/login", dict(email="team@example.test", password=PASSWORD), auth=False)
            self.assertEqual(response.status, 200, body)
            team_cookie = response.getheader("Set-Cookie").split(";", 1)[0]
            self.assertEqual(self.request("POST", "/api/config", {}, headers={"Cookie": team_cookie})[0].status, 403)


if __name__ == "__main__": unittest.main(verbosity=2)
