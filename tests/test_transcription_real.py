"""Inferência real CPU, opcional: ESTUDIO_TESTE_TRANSCRICAO_REAL=1 python tests/test_transcription_real.py

Exige faster-whisper e o large-v3-turbo já instalado; não chama serviços pagos.
"""
import os
from pathlib import Path
import re
import sys
import time
import unicodedata
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


@unittest.skipUnless(os.environ.get("ESTUDIO_TESTE_TRANSCRICAO_REAL") == "1", "inferência real habilitada explicitamente")
class TranscricaoRealTests(unittest.TestCase):
    def test_fala_portugues_alinhamento_e_cortes(self):
        from estudio import transcrever_arquivo, palavras_de, segmentos_fala
        arquivo = ROOT / "tests" / "fixtures" / "fala-pt-br.wav"
        with wave.open(str(arquivo)) as f:
            duracao = f.getnframes() / f.getframerate()
            self.assertEqual(f.getframerate(), 16000)
        inicio = time.monotonic()
        resultado = transcrever_arquivo(str(arquivo))
        palavras = palavras_de(resultado)
        texto = " ".join(p["w"] for p in palavras)
        normalizado = "".join(c for c in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(c))
        tokens = set(re.findall(r"\w+", normalizado))
        esperadas = {"hoje", "editar", "video", "palavra", "momento", "certo", "salvar", "arquivo", "editor", "palavras", "completas", "silencios"}
        self.assertGreaterEqual(len(tokens & esperadas), 10, texto)
        self.assertGreaterEqual(len(palavras), 25, texto)
        self.assertLessEqual(len(palavras), 40, texto)
        anterior = 0.0
        for palavra in palavras:
            self.assertIsInstance(palavra["t"], float)
            self.assertIsInstance(palavra["e"], float)
            self.assertGreaterEqual(palavra["t"], anterior - 0.02)
            self.assertGreaterEqual(palavra["e"], palavra["t"])
            self.assertLessEqual(palavra["e"], duracao + 0.1)
            anterior = palavra["t"]
        self.assertTrue(any(w["word"].startswith(" ") for s in resultado["segments"] for w in s["words"]))
        cortes = segmentos_fala(palavras, [[0, duracao]])
        self.assertGreaterEqual(len(cortes), 2, "A pausa sintética central deve separar duas faixas de fala.")
        for a, b in cortes:
            for palavra in palavras:
                for ponto in (a, b):
                    self.assertFalse(palavra["t"] < ponto < palavra["e"] - 0.02, (ponto, palavra))
        for palavra in palavras:
            self.assertTrue(any(a <= palavra["t"] + 0.001 and b >= palavra["e"] - 0.001 for a, b in cortes), palavra)
        print(f"Whisper real: {len(palavras)} palavras; {len(cortes)} trechos; {duracao:.2f}s de áudio em {time.monotonic() - inicio:.2f}s.")
        print(texto)


if __name__ == "__main__": unittest.main(verbosity=2)
