"""Contratos que a migração Linux precisa preservar. Sem API paga nem download de modelos."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import estudo
import estudio
import transcricao
import pose
import numpy as np
from PIL import Image, ImageFont


class TranscricaoTests(unittest.TestCase):
    def test_preserva_palavra_espaco_e_float(self):
        segmentos = [NS(start=0, end=1, text=" Olá mundo", words=[
            NS(word=" Olá", start=0.1, end=0.4), NS(word=" mundo", start=0.5, end=1)])]
        resultado = transcricao.converter_segmentos(iter(segmentos))
        self.assertEqual(resultado, {"segments": [{"start": 0.0, "end": 1.0, "text": " Olá mundo", "words": [
            {"word": " Olá", "start": 0.1, "end": 0.4}, {"word": " mundo", "start": 0.5, "end": 1.0}]}]})
        self.assertIsInstance(resultado["segments"][0]["words"][1]["end"], float)

    def test_alinhamento_obrigatorio(self):
        for palavras in [None, [], [NS(word=" fala", start=None, end=1)], [NS(word=" fala", start=2, end=1)]]:
            with self.subTest(palavras=palavras), self.assertRaises(ValueError):
                transcricao.converter_segmentos([NS(start=0, end=1, text=" fala", words=palavras)])

    def test_refino_e_audio_usam_mesmo_backend(self):
        class Modelo:
            def transcribe(self, audio, **kw):
                self.audio, self.opcoes = audio, kw
                return iter([NS(start=0, end=1, text=" fala", words=[NS(word=" fala", start=0.1, end=1)])]), None
        modelo = Modelo()
        audio = np.zeros(16000, dtype=np.float32)
        with patch.object(transcricao, "modelo", return_value=modelo):
            resultado = estudio.transcrever_arquivo(audio, "Nome Próprio")
        self.assertIs(modelo.audio, audio)
        self.assertTrue(modelo.opcoes["word_timestamps"])
        self.assertFalse(modelo.opcoes["condition_on_previous_text"])
        self.assertEqual(modelo.opcoes["language"], "pt")
        self.assertEqual(modelo.opcoes["initial_prompt"], "Nome Próprio")
        self.assertEqual(resultado["segments"][0]["words"][0]["word"], " fala")

    def test_cortes_nao_atravessam_palavras(self):
        palavras = [dict(w=str(i), t=float(a), e=float(b)) for i, (a, b) in enumerate([
            (0.7, 1.05), (1.1, 1.5), (1.53, 1.88), (3.1, 3.5), (3.52, 3.8),
            (5.0, 5.18), (5.2, 5.8), (5.9, 6.18), (8.7, 9.4), (9.5, 9.85)])]
        cortes = estudio.segmentos_fala(palavras, [[0, 10]])
        self.assertEqual(len(cortes), 4)
        for corte in cortes:
            for ponto in corte:
                for palavra in palavras:
                    self.assertFalse(palavra["t"] < ponto < palavra["e"] - 0.02, (ponto, palavra))
        for palavra in palavras:
            self.assertTrue(any(a <= palavra["t"] and b >= palavra["e"] for a, b in cortes))


class PoseTests(unittest.TestCase):
    @staticmethod
    def pessoa(ombro, quadril, x=0.5):
        j = np.zeros((17, 3), dtype=np.float32)
        j[5] = [x - 0.1, ombro, 0.9]; j[6] = [x + 0.1, ombro, 0.9]
        j[11] = [x - 0.06, quadril, 0.9]; j[12] = [x + 0.06, quadril, 0.9]
        return j

    def test_escolhe_maior_tronco_origem_em_cima(self):
        fundo = self.pessoa(0.1, 0.3)
        frente = self.pessoa(0.54, 0.8)
        cintura, pescoco, ombros = pose.maior_tronco([fundo, frente], 1080, 1920)
        self.assertAlmostEqual(cintura, 1536, places=3)
        self.assertAlmostEqual(pescoco, 1036.8, places=3)
        self.assertAlmostEqual(ombros, 216, places=3)

    def test_sem_modelo_usa_fallback(self):
        with patch.object(pose, "sessao", return_value=None):
            self.assertEqual(pose.juntas_pasta("/sem-imagens"), (None, None, None))

    def test_letterbox_desfaz_margem(self):
        tensor, transformacao = pose.preparar(Image.new("RGB", (540, 960)))
        self.assertEqual(tensor.shape, (1, 3, 640, 640))
        self.assertEqual(transformacao, (140, 0, 360, 640))
        pessoa = self.pessoa(0.5, 0.8)
        j = pessoa.copy(); j[:, 0] = j[:, 0] * 360 + 140; j[:, 1] *= 640
        linha = np.concatenate(([320, 320, 200, 500, 0.95], j.reshape(-1)))
        saida = linha[None, :, None]
        detectadas = pose.pessoas(saida, transformacao)
        self.assertEqual(len(detectadas), 1)
        np.testing.assert_allclose(detectadas[0][[5, 6, 11, 12]], pessoa[[5, 6, 11, 12]], atol=1e-6)


class PortabilidadeTests(unittest.TestCase):
    def test_todas_as_fontes_dos_estilos_existem(self):
        for p in (ROOT / "estilos").glob("*/estilo.json"):
            for fonte in json.loads(p.read_text())["fontes"].values():
                arquivo = ROOT / fonte["arquivo"]
                with self.subTest(estilo=p.parent.name, fonte=arquivo.name):
                    self.assertTrue(arquivo.is_file())
                    f = ImageFont.truetype(str(arquivo), 60, index=fonte.get("indice", 0))
                    self.assertGreater(f.getlength("Coração"), 0)

    def test_emoji_colorido_tamanho_final(self):
        from fontes import emoji_img
        for ch in ("✅", "❌"):
            im = emoji_img(ch, 120)
            self.assertEqual(im.height, 120)
            self.assertIsNotNone(im.getbbox())
            rgba = np.asarray(im)
            self.assertTrue(np.any((rgba[:, :, 3] > 100) & ((rgba[:, :, 0] != rgba[:, :, 1]) | (rgba[:, :, 1] != rgba[:, :, 2]))))

    def test_ambiente_define_pastas_modelos_e_porta(self):
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, PYTHONPATH=str(ROOT / "lib"), ESTUDIO_RAIZ=td + "/projetos", ESTUDIO_BROLLS=td + "/brolls",
                       ESTUDIO_BIBLIOTECA=td + "/brolls/biblioteca", ESTUDIO_MODELOS=td + "/modelos", ESTUDIO_PORTA="4123")
            cmd = "import comum,biblioteca,json; print(json.dumps([comum.RAIZ,comum.MODELOS,comum.PORTA,biblioteca.BROLLS,biblioteca.RAIZ]))"
            resultado = json.loads(subprocess.check_output([sys.executable, "-c", cmd], env=env, text=True))
            self.assertEqual(resultado, [td + "/projetos", td + "/modelos", 4123, td + "/brolls", td + "/brolls/biblioteca"])

    def test_subprocessos_usam_interpretador_atual(self):
        import auto, kanban, leva, tiktok, youtube
        for modulo in [estudio, auto, kanban, leva]: self.assertEqual(modulo.PY, sys.executable)
        for modulo in [tiktok, youtube, estudo]: self.assertEqual(modulo.PY_SISTEMA, sys.executable)
        self.assertEqual(kanban.VENV, sys.executable)


if __name__ == "__main__": unittest.main()
