"""Recuperação isolada: mídia sintética, Whisper simulado, nenhuma chamada Claude."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy  # carregar antes do bloqueio de imports de IA em sys.modules

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("recover_saved_cuts", ROOT / "scripts/recover_saved_cuts.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)
sys.path.insert(0, str(ROOT / "lib"))
import estudio


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe indisponível")
class RecoverSavedCutsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="atlas-recover-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project, self.backup = self.base / "project", self.base / "backup"
        for folder in ("fonte", "auto/conversas", "versoes/A"):
            (self.project / folder).mkdir(parents=True, exist_ok=True)
        self.state = dict(rodando=False, status="erro", mensagem="FFmpeg antigo", pid=1111,
            custo=dict(claude=1.23), inicio=10, fim=20, log=["erro anterior"],
            etapas=[dict(id="transcricao", nome="Transcrição", estado="ok", detalhe="fala"),
                    dict(id="cortes", nome="Cortes", estado="erro", detalhe="opção inválida", inicio=12),
                    dict(id="preparar", nome="Recorte", estado="pendente", detalhe=""),
                    dict(id="render", nome="Render", estado="pendente", detalhe="")])
        self.original = dict(nome="Recuperação", versoes=[], roteiro=None, estilo="ultradinamico", fonte_video=str(self.project / "fonte/src1440.mov"))
        self.whisper = {"segments": [{"start": 0, "end": 1, "text": " fala exemplo", "words": [
            {"word": " fala", "start": .05, "end": .2}, {"word": " exemplo", "start": .7, "end": .95}]}]}
        self.write("projeto.json", self.original)
        self.write("auto/estado.json", self.state)
        self.write("auto/pedido.json", {"nome": "Recuperação", "kanban": "C004"})
        self.write("custos.json", {"itens": [{"usd": 1.23}], "notas": []})
        self.write("fonte/cortes.json", {"versoes": [{"id": "A", "nome": "Versão A", "faixas": [[0, 1.5]], "segs": [[0, .5], [1, 1.5]]}], "frases": []})
        self.write("fonte/whisper.json", self.whisper)
        (self.project / "auto/conversas/01-cortes.md").write_text("Decisão já paga e preservada")
        (self.project / "versoes/A/_jc.txt").write_text("grafo anterior")
        self.media("-f", "lavfi", "-i", "testsrc2=s=160x90:r=30:d=2", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", self.project / "fonte/src1440.mov")
        self.media("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2", "-ac", "2", self.project / "fonte/voz48k.wav")
        self.media("-i", self.project / "fonte/voz48k.wav", "-ac", "1", "-ar", "16000", self.project / "fonte/voz16k.wav")

    def write(self, name, value):
        (self.project / name).write_text(json.dumps(value))

    def media(self, *args):
        result = subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def synthetic_execute(self, project, backup):
        with patch.object(estudio, "transcrever_arquivo", return_value=self.whisper), patch.dict(sys.modules, {"ia": None, "auto": None}):
            estudio.cmd_cortes(SimpleNamespace(projeto=str(project), aplicar=True, versoes=None, manter_copia=True))

    def prepare(self):
        recovery.prepare(self.project, self.backup)

    def test_real_media_recovers_only_cuts_preserves_decisions_costs_and_backup(self):
        cuts_before = (self.project / "fonte/cortes.json").read_bytes()
        self.prepare()
        copied = self.backup / "before/versoes/A/_jc.txt"
        self.assertNotEqual(copied.stat().st_ino, (self.project / "versoes/A/_jc.txt").stat().st_ino)
        with patch.object(recovery, "execute", side_effect=self.synthetic_execute):
            result = recovery.run(self.project, self.backup)
        self.assertTrue(result["ok"])
        self.assertFalse(result["claude_reexecuted"])
        self.assertEqual(result["outputs"]["A"]["frames"], 30)
        self.assertEqual(result["review"], "human_pending")
        after = recovery.read(self.project / "auto/estado.json")
        self.assertEqual(after["custo"], self.state["custo"])
        self.assertEqual([s for s in after["etapas"] if s["id"] != "cortes"], [s for s in self.state["etapas"] if s["id"] != "cortes"])
        self.assertEqual(next(s for s in after["etapas"] if s["id"] == "cortes")["estado"], "ok")
        self.assertFalse(after["rodando"])
        self.assertEqual(after["status"], "pausado")
        self.assertIn("Conferência Claude não reexecutada", after["mensagem"])
        self.assertEqual((self.project / "fonte/cortes.json").read_bytes(), cuts_before)
        self.assertEqual(copied.read_text(), "grafo anterior")
        self.assertTrue(recovery.verify(self.project, self.backup)["ok"])
        with self.assertRaisesRegex(RuntimeError, "já foi executado"):
            recovery.run(self.project, self.backup)

    def test_drift_before_execution_does_not_start_or_overwrite(self):
        self.prepare()
        changed = deepcopy(self.state)
        changed["mensagem"] = "alterado pela interface"
        self.write("auto/estado.json", changed)
        with patch.object(recovery, "execute") as execute, self.assertRaisesRegex(RuntimeError, "mudaram"):
            recovery.run(self.project, self.backup)
        execute.assert_not_called()
        self.assertEqual(recovery.read(self.project / "auto/estado.json"), changed)
        self.assertFalse((self.backup / "started.json").exists())

    def test_failure_does_not_mark_state_ok_and_keeps_backup(self):
        self.prepare()
        with patch.object(recovery, "execute", side_effect=RuntimeError("falha sintética")), self.assertRaisesRegex(RuntimeError, "falha sintética"):
            recovery.run(self.project, self.backup)
        self.assertEqual(recovery.read(self.project / "auto/estado.json"), self.state)
        self.assertFalse(recovery.read(self.backup / "result.json")["ok"])
        self.assertEqual(recovery.read(self.backup / "before/auto/estado.json"), self.state)

    def test_drift_during_execution_preserves_concurrent_state(self):
        self.prepare()
        changed = deepcopy(self.state)
        changed["mensagem"] = "mudança concorrente"
        def execute(project, backup):
            self.synthetic_execute(project, backup)
            self.write("auto/estado.json", changed)
        with patch.object(recovery, "execute", side_effect=execute), self.assertRaisesRegex(RuntimeError, "mudaram"):
            recovery.run(self.project, self.backup)
        self.assertEqual(recovery.read(self.project / "auto/estado.json"), changed)
        self.assertFalse(recovery.read(self.backup / "result.json")["ok"])

    def test_source_drift_during_execution_never_completes_stage(self):
        self.prepare()
        def execute(project, backup):
            self.synthetic_execute(project, backup)
            with (project / "fonte/voz48k.wav").open("ab") as file:
                file.write(b"drift")
        with patch.object(recovery, "execute", side_effect=execute), self.assertRaisesRegex(RuntimeError, "fontes mudaram"):
            recovery.run(self.project, self.backup)
        self.assertEqual(recovery.read(self.project / "auto/estado.json"), self.state)
        self.assertFalse(recovery.read(self.backup / "result.json")["ok"])

    def test_invalid_output_never_completes_stage(self):
        self.prepare()
        def execute(project, backup):
            self.synthetic_execute(project, backup)
            self.write("versoes/A/mapa.json", {"mapa": [[0, .5, 0]], "dur": .5})
        with patch.object(recovery, "execute", side_effect=execute), self.assertRaisesRegex(RuntimeError, "Mapa diverge"):
            recovery.run(self.project, self.backup)
        self.assertEqual(recovery.read(self.project / "auto/estado.json"), self.state)

    def test_active_or_downstream_completed_state_is_rejected(self):
        for mutation in (lambda s: s.update(rodando=True), lambda s: s["etapas"][2].update(estado="ok")):
            state = deepcopy(self.state)
            mutation(state)
            self.write("auto/estado.json", state)
            with self.assertRaises(RuntimeError):
                self.prepare()
            self.assertFalse(self.backup.exists())

    def test_actual_subprocess_contract_has_no_auto_or_provider_entrypoint(self):
        self.backup.mkdir(mode=0o700)
        with patch.object(recovery.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            recovery.execute(self.project, self.backup)
        args, options = run.call_args
        self.assertEqual(args[0], ["nice", "-n", "10", sys.executable, str(ROOT / "lib/estudio.py"), "cortes", str(self.project), "--aplicar", "--manter-copia"])
        self.assertEqual(options["env"]["HF_HUB_OFFLINE"], "1")
        self.assertFalse(any(k.startswith(("ANTHROPIC", "CLAUDE", "APIFY", "GEMINI")) for k in options["env"]))

    def test_vad_recovery_uses_same_map_and_cache_without_reopening_short_exclusions(self):
        import segmentacao_fala
        cuts = dict(versoes=[dict(id="A", nome="Versão única", faixas=[[0, .5], [.6, 1.1]])],
                    frases=[], cortes_cfg=dict(modo="fala_vad", pausa_min=.16, respiro=1/30, cauda=1/30))
        self.write("fonte/cortes.json", cuts)
        whisper_before = (self.project / "fonte/whisper.json").read_bytes()
        calls = []
        def vad(wav, ranges, words, cfg, *, cache_path=None, log=print):
            # A 100 ms explicit removal must not be joined by the old 250 ms rule.
            self.assertEqual(ranges, [[0, .5], [.6, 1.1]])
            self.assertEqual(cfg, dict(cuts["cortes_cfg"], fps=30))
            self.assertEqual(words, estudio.palavras_de(self.whisper))
            cache = Path(cache_path) if cache_path else Path(wav).with_name("whisper_refino_cortes.json")
            self.assertEqual(cache.resolve(), (self.project / "fonte/whisper_refino_cortes.json").resolve())
            if not cache.exists():
                cache.write_text(json.dumps({"janelas": {}, "teste": "cache separado"}))
            calls.append((str(wav), ranges))
            return ranges
        with patch.object(segmentacao_fala, "segmentar_arquivo", side_effect=vad):
            self.prepare()
            manifest = recovery.read(self.backup / "manifest.json")
            self.assertIn("fonte/whisper_refino_cortes.json", manifest["protected"])
            self.assertIn("lib/segmentacao_fala.py", manifest["code"])
            self.assertEqual(manifest["expected_maps"]["A"],
                             {"mapa": [[0, .5, 0], [.6, 1.1, .5]], "dur": 1.0})
            with patch.object(recovery, "execute", side_effect=self.synthetic_execute):
                result = recovery.run(self.project, self.backup)
            self.assertTrue(result["ok"])
            self.assertTrue(recovery.verify(self.project, self.backup)["ok"])
        self.assertEqual(len(calls), 2)
        self.assertEqual((self.project / "fonte/whisper.json").read_bytes(), whisper_before)
        self.assertEqual((self.project / "fonte/cortes.json").read_text(), json.dumps(cuts))

    def test_explicit_saved_segments_take_precedence_over_vad(self):
        import segmentacao_fala
        cuts = recovery.read(self.project / "fonte/cortes.json")
        cuts["cortes_cfg"] = {"modo": "fala_vad"}
        with patch.object(segmentacao_fala, "segmentar_arquivo") as vad:
            maps = recovery.expected_maps(self.project, cuts)
        vad.assert_not_called()
        self.assertEqual(maps["A"], {"mapa": [[0, .5, 0], [1, 1.5, .5]], "dur": 1.0})


if __name__ == "__main__":
    unittest.main()
