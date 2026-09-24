"""TikTok download contracts: real MP4 validation, isolated library, no paid services."""
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import tiktok


class Response(io.BytesIO):
    def __init__(self, body, declared=None):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body) if declared is None else declared)}
        self.read_sizes = []

    def read(self, size=-1):
        if not 0 < size <= 1024 * 1024:
            raise AssertionError("media must be read in bounded chunks")
        self.read_sizes.append(size)
        return super().read(size)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg and ffprobe required")
class TikTokDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = tempfile.TemporaryDirectory(prefix="atlas-tiktok-fixture-")
        cls.fixture = Path(cls.fixture_dir.name) / "tiny.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=blue:s=90x160:r=10:d=3",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.fixture)], check=True, timeout=20)
        cls.video = cls.fixture.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.fixture_dir.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="atlas-tiktok-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lib = self.root / "library"
        self.project = self.root / "project"
        self.project.mkdir()
        for target, value in (("RAIZ", str(self.lib)), ("IDX", str(self.lib / "biblioteca.json"))):
            p = patch.object(tiktok.biblioteca, target, value)
            p.start(); self.addCleanup(p.stop)
        token = patch.object(tiktok.custos, "token_apify", return_value="test-secret-never-log")
        token.start(); self.addCleanup(token.stop)
        self.real_run = subprocess.run

    def candidate(self, tid="123"):
        return dict(tiktok_id=tid, url=f"https://www.tiktok.com/@example/video/{tid}",
                    downloads=[f"https://api.apify.com/v2/key-value-stores/store/records/{tid}"])

    def actor_item(self, tid="123"):
        c = self.candidate(tid)
        return dict(id=tid, webVideoUrl=c["url"], mediaUrls=c["downloads"],
                    authorMeta={"name": "example"}, videoMeta={"duration": 3, "width": 90, "height": 160})

    def runner(self, args, **kwargs):
        if args[0] == "yt-dlp":
            return SimpleNamespace(returncode=1, stdout="", stderr="Sign in to confirm you are not a bot https://private/?token=SECRET")
        if len(args) > 1 and str(args[1]).endswith("folhas.py"):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return self.real_run(args, **kwargs)

    def test_apify_video_is_saved_and_registered_even_when_smaller_than_old_size_threshold(self):
        self.assertLess(len(self.video), 100_000)
        with patch.object(tiktok, "rodar_ator", return_value=([self.actor_item()], {"id": "run-test"})) as actor, \
             patch.object(tiktok.custos, "registrar_apify"), \
             patch.object(tiktok.urllib.request, "urlopen", side_effect=lambda *a, **k: Response(self.video)), \
             patch.object(tiktok.subprocess, "run", side_effect=self.runner) as commands:
            result = tiktok.buscar(str(self.project), "test", ["blue"], estudar=False, log=lambda *a: None)
        self.assertTrue(actor.call_args.args[0]["shouldDownloadVideos"])
        self.assertEqual(result["ids"], ["B0001"])
        self.assertEqual(result["novos"], ["B0001"])
        self.assertEqual(result["falhas"], 0)
        item = tiktok.biblioteca.ler()["itens"]["B0001"]
        self.assertEqual(Path(item["arquivo"]).read_bytes(), self.video)
        self.assertEqual(item["dur"], 3)
        self.assertFalse(any(c.args[0][0] == "yt-dlp" for c in commands.call_args_list))

    def test_false_success_becomes_error_and_keeps_safe_diagnostics(self):
        item = self.actor_item()
        item["mediaUrls"] = []
        with patch.object(tiktok, "rodar_ator", return_value=([item], {"id": "run-test"})), \
             patch.object(tiktok.custos, "registrar_apify"), \
             patch.object(tiktok.subprocess, "run", side_effect=self.runner), \
             self.assertRaises(tiktok.ErroBusca) as error:
            tiktok.buscar(str(self.project), "test", ["blue"], estudar=False, log=lambda *a: None)
        self.assertIn("nenhum vídeo foi salvo", str(error.exception))
        self.assertIn("verificação/login", str(error.exception))
        self.assertNotIn("SECRET", str(error.exception))
        saved = json.loads(next(self.project.glob("busca/*/busca.json")).read_text())
        self.assertEqual(saved["falhas_download"], 1)
        self.assertIn("erro_download", saved["candidatos"][0])
        self.assertEqual(tiktok.biblioteca.ler()["itens"], {})
        self.assertFalse(any((self.lib / "videos").iterdir()))

    def test_partial_success_retains_real_video_and_reports_failed_count(self):
        def response(req, **kw):
            if req.full_url.endswith("456"):
                raise urllib.error.HTTPError(req.full_url + "?token=SECRET", 403, "private", {}, None)
            return Response(self.video)
        logs = []
        with patch.object(tiktok, "rodar_ator", return_value=([self.actor_item(), self.actor_item("456")], {"id": "run-test"})), \
             patch.object(tiktok.custos, "registrar_apify"), \
             patch.object(tiktok.urllib.request, "urlopen", side_effect=response), \
             patch.object(tiktok.subprocess, "run", side_effect=self.runner):
            result = tiktok.buscar(str(self.project), "test", ["blue"], estudar=False, log=logs.append)
        self.assertEqual(result["ids"], ["B0001"])
        self.assertEqual(result["falhas"], 1)
        self.assertTrue(any("1 download(s) falharam" in x for x in logs))
        self.assertNotIn("SECRET", "\n".join(logs))

    def test_html_payload_never_replaces_existing_file_or_registers_as_video(self):
        videos = Path(tiktok.biblioteca.pasta("videos"))
        target = videos / "123.mp4"
        target.write_bytes(b"previous incomplete file")
        html = b"<html>Access denied</html>" * 20_000
        with patch.object(tiktok.urllib.request, "urlopen", side_effect=lambda *a, **k: Response(html)), \
             patch.object(tiktok.subprocess, "run", side_effect=self.runner), \
             self.assertRaises(tiktok.ErroDownload) as error:
            tiktok.baixar_video(self.candidate())
        self.assertIn("não contém vídeo válido", str(error.exception))
        self.assertEqual(target.read_bytes(), b"previous incomplete file")
        self.assertEqual(list(videos.iterdir()), [target])

    def test_truncated_download_keeps_previous_video_and_removes_temporary_file(self):
        target = self.root / "video.mp4"
        target.write_bytes(self.video)
        with patch.object(tiktok.urllib.request, "urlopen", return_value=Response(self.video, declared=len(self.video) + 100)), \
             self.assertRaises(tiktok.ErroDownload) as error:
            tiktok.pegar("https://cdn.example/video", str(target), tentativas=1, validar=tiktok.video_valido, estrito=True)
        self.assertIn("incompleto", str(error.exception))
        self.assertEqual(target.read_bytes(), self.video)
        self.assertFalse(list(self.root.glob(".download-*")))

    def test_cached_valid_video_needs_no_network(self):
        target = Path(tiktok.biblioteca.pasta("videos")) / "123.mp4"
        target.write_bytes(self.video)
        with patch.object(tiktok.urllib.request, "urlopen") as network:
            self.assertEqual(tiktok.baixar_video(self.candidate()), str(target))
        network.assert_not_called()

    def test_ytdlp_timeout_is_bounded_safe_and_cleans_its_partial_files(self):
        def timeout(args, **kwargs):
            if args[0] == "yt-dlp":
                self.assertEqual(kwargs["timeout"], 180)
                Path(args[args.index("-o") + 1] + ".part").write_bytes(b"partial")
                raise subprocess.TimeoutExpired(args, 180, stderr="https://private?token=SECRET")
            return self.real_run(args, **kwargs)
        candidate = self.candidate(); candidate["downloads"] = []
        with patch.object(tiktok.subprocess, "run", side_effect=timeout), self.assertRaises(tiktok.ErroDownload) as error:
            tiktok.baixar_video(candidate)
        self.assertIn("tempo permitido", str(error.exception))
        self.assertNotIn("SECRET", str(error.exception))
        self.assertFalse(any((self.lib / "videos").iterdir()))

    def test_apify_bearer_is_only_sent_to_exact_https_host_and_not_redirected(self):
        requests = []
        def response(req, **kwargs):
            requests.append(req)
            return Response(b"thumbnail")
        urls = ["https://api.apify.com/v2/record", "https://api.apify.com.attacker.example/record",
                "https://elsewhere.example/api.apify.com", "http://api.apify.com/v2/record"]
        with patch.object(tiktok.urllib.request, "urlopen", side_effect=response):
            for n, url in enumerate(urls):
                self.assertTrue(tiktok.pegar(url, str(self.root / f"cover{n}.jpg")))
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer test-secret-never-log")
        self.assertTrue(all(req.get_header("Authorization") is None for req in requests[1:]))
        redirect = urllib.request.HTTPRedirectHandler().redirect_request(requests[0], None, 302, "", {}, "https://cdn.example/file")
        self.assertIsNone(redirect.get_header("Authorization"))

    def test_url_contract_supports_direct_fallback_without_persisting_apify_token(self):
        self.assertEqual(tiktok.urls_video({"mediaUrls": ["https://api.apify.com/v2/record?token=SECRET", None, "file:///tmp/no"],
                                          "videoMeta": {"downloadAddr": "https://cdn.example/video?signature=SIGNED"}}),
                         ["https://api.apify.com/v2/record", "https://cdn.example/video?signature=SIGNED"])


if __name__ == "__main__":
    unittest.main()
