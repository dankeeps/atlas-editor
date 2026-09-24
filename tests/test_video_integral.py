"""Uploads já cortados: mídia sintética, sem modelos/contas/mídias do usuário."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("video_integral", ROOT / "lib/video_integral.py")
video = importlib.util.module_from_spec(spec)
spec.loader.exec_module(video)


def funcoes_auto(nomes, contexto=None):
    tree = ast.parse((ROOT / "lib/auto.py").read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in nomes]
    env = dict(os=os, json=json, threading=threading, Falha=RuntimeError)
    env.update(contexto or {})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "auto.py", "exec"), env)
    return env


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg indisponível")
class VideoIntegralTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.TemporaryDirectory(prefix="atlas-integral-")
        cls.addClassCleanup(cls.base.cleanup)
        cls.fixture = Path(cls.base.name) / "fixture"
        (cls.fixture / "fonte").mkdir(parents=True)
        cls.upload = Path(cls.base.name) / "upload.mov"
        # Pausas nas pontas e no meio; qualquer corte, fade ou mudança de volume altera o PCM.
        video._ff("-f", "lavfi", "-i", "color=c=navy:s=180x320:r=25:d=1.44", "-f", "lavfi", "-i",
            "aevalsrc=if(between(t\\,0.2\\,0.6)+between(t\\,0.8\\,1.2)\\,0.2*sin(2*PI*220*t)+0.1*sin(2*PI*660*t)\\,0):s=48000:d=1.413",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-ac", "2", cls.upload)
        video._ff("-i", cls.upload, "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", cls.fixture / "fonte/voz48k.wav")
        video._ff("-i", cls.upload, "-vn", "-ac", "1", "-ar", "16000", cls.fixture / "fonte/voz16k.wav")
        video._json(cls.fixture / "fonte/whisper.json", dict(segments=[dict(words=[
            dict(word=" primeira", start=.2, end=.6), dict(word=" segunda", start=.8, end=1.2)])]))
        video._json(cls.fixture / "projeto.json", dict(nome="Teste", fonte_video=str(cls.upload),
            fonte_identidade=video.identidade_arquivo(cls.upload), versoes=[]))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="atlas-integral-test-")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        shutil.copytree(self.fixture, self.project)

    def test_normalizes_whole_video_preserving_audio_words_and_single_identity_map(self):
        original = (self.project / "fonte/whisper.json").read_bytes()
        dur = video.preparar(self.project, log=lambda _: None)
        vd = self.project / "versoes/A"
        self.assertEqual(video._pcm(vd / "voz.wav"), video._pcm(self.project / "fonte/voz48k.wav"))
        self.assertEqual(video._pcm(vd / "voz16k.wav"), video._pcm(self.project / "fonte/voz16k.wav"))
        self.assertEqual((vd / "whisper.json").read_bytes(), original)
        self.assertEqual(video._json(vd / "mapa.json")["mapa"], [[0.0, dur, 0.0]])
        self.assertEqual(video._json(self.project / "fonte/cortes.json")["versoes"][0]["segs"], [[0.0, dur]])
        p = video._json(self.project / "projeto.json")
        self.assertEqual(p["versoes"], [dict(id="A", nome="Versão única")])
        self.assertTrue(p["video_ja_cortado"])
        self.assertGreaterEqual(dur, 1.413)
        self.assertLess(abs(dur - 1.44), 1 / 30 + .002)
        self.assertTrue(video.versao_pronta(self.project, p["fonte_identidade"]))
        # Retomada reaproveita só esta versão confirmada; não renderiza nem transcreve de novo.
        with patch.object(video, "_ff", side_effect=AssertionError("não renderizar novamente")):
            self.assertEqual(video.preparar(self.project), dur)
        (self.project / "fonte/whisper.json").write_text('{"segments":[]}')
        self.assertFalse(video.versao_pronta(self.project, p["fonte_identidade"]))

    def test_rejects_other_upload_stale_identity_and_existing_old_version(self):
        p = video._json(self.project / "projeto.json")
        other = Path(self.temp.name) / "other.mov"
        shutil.copy2(self.upload, other)
        with self.assertRaisesRegex(ValueError, "diferentes"):
            video.validar_fonte(p, dict(video=str(other)))
        p["fonte_identidade"]["tamanho"] += 1
        with self.assertRaisesRegex(ValueError, "identidade"):
            video.validar_fonte(p)
        vd = self.project / "versoes/A"
        vd.mkdir(parents=True)
        (vd / "jc.mov").write_bytes(b"original old version")
        with self.assertRaisesRegex(ValueError, "projeto novo"):
            video.preparar(self.project)
        self.assertEqual((vd / "jc.mov").read_bytes(), b"original old version")

    def test_aac_upload_at_44100_preserves_decoded_audio_without_fades(self):
        upload = Path(self.temp.name) / "upload.mp4"
        video._ff("-i", self.upload, "-c:v", "copy", "-c:a", "aac", "-ar", "44100", upload)
        p = video._json(self.project / "projeto.json")
        p.update(fonte_video=str(upload), fonte_identidade=video.identidade_arquivo(upload))
        video._json(self.project / "projeto.json", p)
        video._ff("-i", upload, "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", self.project / "fonte/voz48k.wav")
        video._ff("-i", upload, "-vn", "-ac", "1", "-ar", "16000", self.project / "fonte/voz16k.wav")
        video.preparar(self.project, log=lambda _:None)
        self.assertEqual(video._pcm(self.project / "versoes/A/voz.wav"), video._pcm(self.project / "fonte/voz48k.wav"))

    def test_failed_normalization_leaves_no_partial_version_or_project_change(self):
        before = (self.project / "projeto.json").read_bytes()
        with patch.object(video, "_ff", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                video.preparar(self.project, log=lambda _: None)
        self.assertEqual((self.project / "projeto.json").read_bytes(), before)
        self.assertFalse((self.project / "versoes/A").exists())
        self.assertEqual(list((self.project / "versoes").iterdir()), [])


class PipelineIntegralTests(unittest.TestCase):
    def test_original_announcement_wins_without_inferring_from_new_transcript(self):
        env = funcoes_auto({"anuncio_da_leva"}, dict(filtro=Mock(side_effect=AssertionError("não inferir"))))
        ctx = SimpleNamespace(ped=dict(anuncio="novo nome", anuncio_origem=dict(nome="Parte 01", leva="20260921-055808")))
        self.assertEqual(env["anuncio_da_leva"](ctx), ("Parte 01", "20260921-055808"))
        for origem in (dict(nome="Parte 01", leva="../old"), dict(nome="", leva="old"), "old"):
            ctx.ped["anuncio_origem"] = origem
            with self.assertRaises(RuntimeError): env["anuncio_da_leva"](ctx)

    def test_no_new_searches_blocks_review_even_when_model_requests_downloads(self):
        no_network = Mock(side_effect=AssertionError("rede não permitida"))
        state = dict(encaixe=dict(ids=[], brolls=[], faltas=[]))
        ctx = SimpleNamespace(d="unused", ped=dict(sem_novas_buscas=True, buscar_tiktok=True, buscar_youtube=True),
            P=lambda: dict(nome="Parte 01"), est=SimpleNamespace(log=lambda _: None),
            aux=lambda nome, dados=None: state.__setitem__(nome, dados) if dados is not None else state[nome],
            pedir=lambda *a, **kw: dict(adicionar=[], buscas=[dict(nome="requested", termos=["foo"])], deixar=[]), grava_P=lambda _: None)
        env = funcoes_auto({"buscas_permitidas", "et_revisao", "youtube_para_biblioteca"}, dict(
            custos=SimpleNamespace(token_apify=no_network), tiktok=SimpleNamespace(buscar=no_network),
            youtube=SimpleNamespace(buscar=no_network, baixar_para=no_network), conv_bp=lambda _: None,
            conferir_brolls=lambda *a: ([], [], [dict(texto="lacuna")]), tira_pre=lambda _: [], texto=lambda x:x,
            pasta_da_oferta=lambda _: ("Expert", "Oferta", []), MAX_BUSCAS_REVISAO=3, sch_revisao=lambda _: None,
            corrigir_brolls=lambda c,v,b,i,r:b, reservar=lambda *a:None, rodar=lambda *a:None, PY="python", ESTUDIO="unused"))
        result = env["et_revisao"](ctx)
        self.assertIn("0 clipe(s) novo(s)", result)
        no_network.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "novas buscas"):
            env["youtube_para_biblioteca"](ctx, {}, {}, "")
        no_network.assert_not_called()

    def test_pipeline_chooses_integral_preparation_and_waits_before_review(self):
        chamadas = []
        etapas = [(n, n) for n in ("transcricao", "cortes", "preparar", "encaixe", "revisao", "letreiros", "render")]
        estado = SimpleNamespace(et=lambda _: dict(estado="pendente"), log=lambda _:None)
        ctx = SimpleNamespace(d="/does-not-exist", ped=dict(video_ja_cortado=True), est=estado, etapas=etapas,
            iniciar_preparar=lambda: chamadas.append("preparar-iniciou"), esperar_preparar=lambda: chamadas.append("preparar-terminou"))
        env = dict(comum=SimpleNamespace(mascaras_ok=lambda _: True), roda=lambda c,n,fn:fn(c))
        for nome in ("transcricao", "video_integral", "encaixe", "revisao", "letreiros", "plano", "broll", "montagem", "conferencia", "render"):
            env["et_" + nome] = lambda c, n=nome: chamadas.append(n)
        env["et_cortes"] = Mock(side_effect=AssertionError("não cortar"))
        env = funcoes_auto({"executar_etapas", "etapas_pedido"}, dict(env, ETAPAS=etapas, ETAPAS_BP=etapas))
        env["executar_etapas"](ctx, "letreiros")
        self.assertEqual(chamadas, ["transcricao", "video_integral", "preparar-iniciou", "encaixe", "revisao", "letreiros", "preparar-terminou"])
        self.assertEqual(dict(env["etapas_pedido"](ctx.ped, True))["cortes"], "Vídeo já cortado")

    def test_no_new_searches_blocks_standard_broll_pipeline(self):
        no_network = Mock(side_effect=AssertionError("rede não permitida"))
        state = dict(plano=dict(momentos=[]))
        ctx = SimpleNamespace(d="unused", ped=dict(sem_novas_buscas=True, buscar_tiktok=True, buscar_youtube=True),
            P=lambda:dict(nome="Parte 01"), est=SimpleNamespace(log=lambda _:None),
            aux=lambda nome, dados=None:state.__setitem__(nome, dados) if dados is not None else state[nome],
            cl=SimpleNamespace(conversa=lambda *a,**k:None),
            pedir=lambda *a,**kw:dict(escolhas=[], buscas=[dict(nome="requested", termos=["foo"])], youtube=[dict(nome="requested")]),
            grava_P=lambda _:None)
        env = funcoes_auto({"buscas_permitidas", "et_broll"}, dict(
            custos=SimpleNamespace(token_apify=no_network), tiktok=SimpleNamespace(buscar=no_network),
            youtube=SimpleNamespace(disponivel=no_network), biblioteca=SimpleNamespace(ler=lambda:dict(itens={})),
            youtube_para_biblioteca=no_network, duracoes_momentos=lambda *a:{}, disponiveis=lambda _:[],
            rotulo_pasta=lambda _:"Oferta", prompt=lambda _:None, texto=lambda x:x, lista_momentos=lambda *a:"",
            candidatos=lambda *a,**kw:[], marcados_do_anuncio=lambda _:[], filtro=lambda _: {}, sch_escolha=lambda _:None,
            MAX_BUSCAS=6, MAX_YOUTUBE=3, reservar=lambda *a:None, rodar=lambda *a:None, PY="python", ESTUDIO="unused"))
        self.assertIn("0 clipe(s) novo(s)", env["et_broll"](ctx))
        no_network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
