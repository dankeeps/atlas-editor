"""Relatório de cortes: nenhum render, transcrição ou provedor externo."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import estudio


class CutVerificationReportTests(unittest.TestCase):
    def transcript(self, second_end):
        words = [
            {"word": " o", "start": 239.8, "end": 240.1},
            {"word": " próprio", "start": 240.4, "end": 240.96},
            {"word": " próprio", "start": 240.96, "end": second_end},
            {"word": " corpo", "start": 241.3, "end": 241.6},
            {"word": " inteiro", "start": 241.6, "end": 242.0},
        ]
        return {"segments": [{"words": words}]}

    def report(self, transcript, script=None):
        with tempfile.TemporaryDirectory(prefix="atlas-report-") as temp, redirect_stdout(io.StringIO()):
            estudio.verificar(temp, transcript, script)
            return (Path(temp) / "verificacao.md").read_text()

    def test_zero_or_negative_duration_cannot_report_false_adjacent_repeat(self):
        for second_end in (240.96, 240.90):
            with self.subTest(second_end=second_end):
                transcript = self.transcript(second_end)
                before = deepcopy(transcript)
                result = self.report(transcript, [])
                self.assertIn("## Repetições que sobraram (0)", result)
                self.assertNotIn('"próprio próprio"', result)
                self.assertEqual(transcript, before)
                self.assertEqual(len(estudio.palavras_de(transcript)), 5)

    def test_positive_duration_repeat_is_still_reported(self):
        result = self.report(self.transcript(241.2), [])
        self.assertIn("## Repetições que sobraram (1)", result)
        self.assertIn('"próprio próprio"', result)

    def test_missing_script_is_explicit_without_fake_comparison(self):
        for script in (None, [], [{"texto": ""}]):
            with self.subTest(script=script):
                result = self.report(self.transcript(240.96), script)
                self.assertTrue(result.startswith("# Verificação do jump cut"))
                self.assertIn("Roteiro não fornecido; comparação de cobertura com roteiro não aplicada.", result)
                self.assertNotIn("semelhança roteiro", result)
                self.assertNotIn("Trechos do roteiro que não aparecem", result)

    def test_supplied_script_still_receives_coverage_comparison(self):
        result = self.report(self.transcript(240.96), [{"texto": "o próprio próprio corpo inteiro"}])
        self.assertIn("## Trechos do roteiro que não aparecem (0)", result)
        self.assertIn("semelhança roteiro × fala: 1.00", result)
        self.assertNotIn("Roteiro não fornecido", result)


if __name__ == "__main__":
    unittest.main()
