"""Recuperação offline: nenhum termo novo, planejamento ou ator pago real."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def setUpModule():
    global recovery
    sys.path.insert(0, str(ROOT / "scripts"))
    import recover_cached_brolls as recovery


class FakeApify:
    def __init__(self):
        self.posts = []; self.runs = {}; self.input = {}; self.data = {}; self.lose_response = False

    def add_run(self, run_id, ids):
        self.runs[run_id] = dict(id=run_id, status="SUCCEEDED", defaultDatasetId=run_id, defaultKeyValueStoreId=run_id,
            usageTotalUsd=0.01, chargedEventCounts={"result": len(ids)}, options={"maxTotalChargeUsd": 0.5})
        self.data[run_id] = [dict(id=i, mediaUrls=["https://api.apify.com/v2/media/" + i]) for i in ids]

    def __call__(self, path, data=None):
        if data is not None:
            assert path.startswith("acts/clockworks~tiktok-scraper/runs?")
            assert "maxTotalChargeUsd=0.5" in path and "restartOnError=false" in path
            assert "searchQueries" not in data and "hashtags" not in data and "profiles" not in data
            assert data["scrapeRelatedVideos"] is False and data["shouldDownloadVideos"] is True
            ids = [url.rsplit("/", 1)[1] for url in data["postURLs"]]
            run_id = "run" + str(len(self.posts) + 1)
            self.posts.append(data); self.input[run_id] = data; self.add_run(run_id, ids)
            if self.lose_response: raise TimeoutError("request response lost")
            return {"data": self.runs[run_id]}
        if path.startswith("actor-runs/"): return {"data": self.runs[path.split("/")[1]]}
        if path.startswith("datasets/"): return self.data[path.split("/")[1]]
        if path.startswith("key-value-stores/"): return self.input[path.split("/")[1]]
        raise AssertionError("Unexpected Apify endpoint: " + path)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="atlas-recovery-test-"); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.d = self.root / "levas" / "saved"; self.d.mkdir(parents=True)
        self.library = self.root / "library"; self.brolls = self.root / "brolls"
        self.write("pedido.json", dict(expert="Expert", oferta="Oferta", fontes=["tiktok", "youtube"],
            anuncios=[dict(nome="A", arquivo="/preserved/source.mp4"), dict(nome="B", arquivo="/preserved/source2.mp4")]))
        self.write("termos.json", dict(buscas=[dict(nome="one", categoria="Cat1", anuncios=["A"]), dict(nome="two", categoria="Cat2", anuncios=["B"])],
            categorias=[dict(nome="Cat1"), dict(nome="Cat2")], por_anuncio=[dict(anuncio=n, broll_estimados=1, imagens=[]) for n in ("A", "B")]))
        self.write("estado.json", dict(etapas=[dict(id="transcricao", estado="ok", detalhe="preserved"), dict(id="termos", estado="ok")], log=[]))
        self.write("busca/a/busca.json", dict(nome="one", candidatos=[self.cand("1"), self.cand("2")]))
        self.write("busca/b/busca.json", dict(nome="two", candidatos=[self.cand("2"), self.cand("3")]))
        self.write("transcricoes/01.json", dict(texto="preserved transcript"))
        self.originals = {str(p.relative_to(self.d)): p.read_bytes() for p in self.d.rglob("*.json") if p.name != "estado.json"}
        self.api = FakeApify(); self.downloads = []; self.fail_ids = set(); self.studies = []
        patches = [patch.object(recovery.biblioteca, "RAIZ", str(self.library)),
                   patch.object(recovery.biblioteca, "IDX", str(self.library / "biblioteca.json")),
                   patch.object(recovery.biblioteca, "BROLLS", str(self.brolls)),
                   patch.object(recovery.biblioteca, "sonda", return_value={"dur": 3, "w": 90, "h": 160}),
                   patch.object(recovery.biblioteca, "capa", return_value=""),
                   patch.object(recovery.tiktok, "api", side_effect=self.api),
                   patch.object(recovery.tiktok, "baixar_video", side_effect=self.download),
                   patch.object(recovery.biblioteca, "estudar_pendentes", side_effect=self.study),
                   patch.object(recovery.biblioteca, "motor_estudo", return_value="gemini"),
                   patch.object(recovery.leva, "main", side_effect=AssertionError("Planning forbidden"))]
        for p in patches: p.start(); self.addCleanup(p.stop)

    def write(self, name, data):
        p = self.d / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(data))

    def cand(self, tid):
        return dict(tiktok_id=tid, url="https://www.tiktok.com/@fixture/video/" + tid, titulo="saved", autor="fixture", views=1, termo="existing")

    def download(self, c):
        tid = c["tiktok_id"]; self.downloads.append(tid)
        if tid in self.fail_ids: raise recovery.tiktok.ErroDownload("Vídeo indisponível (HTTP 404)")
        p = Path(recovery.biblioteca.pasta("videos")) / (tid + ".mp4"); p.write_bytes(b"validated by downloader")
        return str(p)

    def study(self, ids, ao_cobrar, **kwargs):
        self.studies.extend(ids); ao_cobrar(dict(usd=0.02, modelo="fixture"))
        with recovery.biblioteca.mexer() as b:
            for i in ids: b["itens"][i]["estudo"] = {"descricao": "studied"}
        return ids

    def create(self, **kwargs):
        return recovery.Recovery(str(self.d), workers=1, **kwargs)

    def run_silently(self, obj, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()): return obj.run(*args, **kwargs)

    def assert_sources_untouched(self):
        for name, content in self.originals.items(): self.assertEqual((self.d / name).read_bytes(), content, name)

    def test_deduplication_download_study_and_all_saved_relations(self):
        r = self.create(batch_size=2)
        summary = self.run_silently(r, "tudo")
        self.assertEqual(len(self.api.posts), 2)
        self.assertEqual(sum(len(p["postURLs"]) for p in self.api.posts), 3)
        self.assertEqual(sorted(self.downloads), ["1", "2", "3"])
        library = recovery.biblioteca.ler()["itens"]
        shared = next(x for x in library.values() if x["tiktok_id"] == "2")
        self.assertEqual(shared["anuncios"], ["A", "B"])
        self.assertEqual({x["categoria"] for x in shared["recuperacao_refs"]}, {"Cat1", "Cat2"})
        self.assertEqual(shared["categoria"], "Cat1")
        found = recovery.read_json(self.d / "achados.json")
        self.assertIn(shared["id"], found["one"]); self.assertIn(shared["id"], found["two"])
        self.assertEqual(summary["por_anuncio"], {"A": 2, "B": 2})
        self.assertEqual(recovery.read_json(self.d / "estado.json")["status"], "pronto")
        self.assert_sources_untouched()
        self.assertEqual(r.cp_path.stat().st_mode & 0o777, 0o600)

    def test_resume_uses_completed_batch_without_new_charge_or_study(self):
        r = self.create(); self.run_silently(r, "tudo")
        self.api.posts.clear(); self.downloads.clear(); self.studies.clear()
        self.run_silently(self.create(), "tudo")
        self.assertEqual(self.api.posts, []); self.assertEqual(self.downloads, []); self.assertEqual(self.studies, [])
        self.assertEqual(len(recovery.custos.ler(str(self.d))["itens"]), 4)

    def test_resume_run_and_cached_response_before_library_insertion(self):
        r = self.create(); r.fetch_batch(["1", "2", "3"])
        self.assertEqual(len(self.api.posts), 1)
        self.run_silently(self.create(), "download")
        self.assertEqual(len(self.api.posts), 1)
        self.assertEqual(sorted(self.downloads), ["1", "2", "3"])
        self.assertEqual(recovery.read_json(self.d / "estado.json")["status"], "aguardando_estudo")

    def test_lost_post_response_requires_association_not_duplicate_post(self):
        r = self.create(); self.api.lose_response = True
        with self.assertRaises(recovery.RecoveryError): r.fetch_batch(["1", "2", "3"])
        self.api.lose_response = False
        again = self.create(); key = next(iter(again.cp["batches"]))
        with self.assertRaisesRegex(recovery.RecoveryError, "ambíguo"): again.fetch_batch(["1", "2", "3"])
        self.assertEqual(len(self.api.posts), 1)
        again.associate(key, "run1")
        self.run_silently(again, "download")
        self.assertEqual(len(self.api.posts), 1)

    def test_reuses_diagnostic_run_and_ignores_unrequested_output(self):
        self.api.add_run("previous", ["1", "999"])
        self.run_silently(self.create(), "download", reuse_runs=["previous"])
        submitted = [url.rsplit("/", 1)[1] for p in self.api.posts for url in p["postURLs"]]
        self.assertEqual(submitted, ["2", "3"])
        self.assertEqual(sorted(self.downloads), ["1", "2", "3"])
        self.assertNotIn("999", self.downloads)

    def test_partial_failure_keeps_files_and_does_not_auto_retry_failed_candidate(self):
        self.fail_ids.add("2")
        self.run_silently(self.create(), "download")
        self.assertEqual(len(recovery.biblioteca.ler()["itens"]), 2)
        self.assertEqual(recovery.read_json(self.d / "estado.json")["status"], "aguardando_estudo")
        self.downloads.clear()
        self.run_silently(self.create(), "download")
        self.assertEqual(self.downloads, [])
        self.assertEqual(len(self.api.posts), 1)
        self.fail_ids.clear()
        self.run_silently(self.create(), "download", retry_failed=True)
        self.assertEqual(self.downloads, ["2"])
        self.assertEqual(len(self.api.posts), 1)
        self.assert_sources_untouched()

    def test_partial_usable_is_ready_with_explicit_unavailable_warning(self):
        self.fail_ids.add("2")
        summary = self.run_silently(self.create(), "tudo")
        self.assertEqual(summary["por_anuncio"], {"A": 1, "B": 1})
        state = recovery.read_json(self.d / "estado.json")
        self.assertEqual(state["status"], "pronto")
        self.assertEqual(state["avisos_recuperacao"]["falhas_download"], 1)
        self.assertEqual(len(self.api.posts), 1)

    def test_all_downloads_fail_and_never_mark_ready(self):
        self.fail_ids.update(("1", "2", "3"))
        with self.assertRaisesRegex(recovery.RecoveryError, "Nenhum candidato"):
            self.run_silently(self.create(), "tudo")
        self.assertEqual(recovery.read_json(self.d / "estado.json")["status"], "erro")
        self.assertEqual(self.studies, [])

    def test_budget_prevents_post_and_source_drift_stops_resume(self):
        with self.assertRaisesRegex(recovery.RecoveryError, "Orçamento Apify"):
            self.run_silently(self.create(apify_budget=0.4), "download")
        self.assertEqual(self.api.posts, [])
        plan = recovery.read_json(self.d / "termos.json"); plan["new"] = True; self.write("termos.json", plan)
        with self.assertRaisesRegex(recovery.RecoveryError, "mudaram"): self.create()

    def test_study_budget_stops_between_clips_and_resume_skips_finished(self):
        self.run_silently(self.create(), "download")
        with self.assertRaisesRegex(recovery.RecoveryError, "Orçamento de estudo"):
            self.run_silently(self.create(study_budget=0.01), "estudo")
        self.assertEqual(len(self.studies), 1)
        self.run_silently(self.create(study_budget=1), "estudo")
        self.assertEqual(len(self.studies), 3)
        self.assertEqual(len(set(self.studies)), 3)
        self.assertEqual(len(self.api.posts), 1)


if __name__ == "__main__": unittest.main()
