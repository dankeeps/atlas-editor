"""Synthetic removal-policy tests; no providers, models or user media."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import limpeza_cortes as limpeza


DUP = "Quando combinados fazem você evoluir dez meses em apenas um".split()


def words(texts, padding=100, gap=.35):
    result, time = [], 1.0
    for token in list(texts) + [f"conteudo{k}" for k in range(padding)]:
        result.append(dict(w=token, t=round(time, 6), e=round(time + .2, 6)))
        time += .2 + gap
    return result


def proposal(a, b, c=None, d=None, tipo="retomada", confianca=.999):
    return dict(de=a, ate=b, tipo=tipo, substituto_de=c, substituto_ate=d,
                motivo="Correspondência verificável entre tomadas.", confianca=confianca)


def shift_from(W, index, seconds):
    for w in W[index:]:
        w["t"] += seconds
        w["e"] += seconds


class LimpezaTests(unittest.TestCase):
    def test_default_mantem_todas_as_palavras_em_uma_versao(self):
        W = words("Toda fala válida fica mesmo fora do roteiro".split())
        r = limpeza.validar_remocoes(W, [])
        doc = limpeza.criar_documento(W, r, W[-1]["e"] + 3)
        self.assertEqual(r["palavras_removidas"], 0)
        self.assertEqual(doc["versoes"], [dict(id="A", nome="Versão única", secoes=[],
                                                faixas=[[0, W[-1]["e"] + 3]])])
        self.assertEqual(doc["cortes_cfg"]["modo"], "fala_vad")
        self.assertEqual(doc["cortes_cfg"]["respiro"], 1 / 30)
        self.assertEqual(doc["ia"]["trechos"], [dict(de=0, ate=len(W) - 1, versoes=["A"])])

    def test_palavra_com_duracao_zero_nao_quebra_a_validacao(self):
        # Caso real (projeto "AM80"): interjeições muito curtas ("é", "mas") às vezes saem do alinhamento
        # forçado com t==e depois do arredondamento — isso é dado legítimo, não transcrição corrompida.
        W = words("Isso é mesmo uma boa pergunta".split())
        W[1]["e"] = W[1]["t"]
        r = limpeza.validar_remocoes(W, [])
        doc = limpeza.criar_documento(W, r, W[-1]["e"] + 1)
        self.assertEqual(r["palavras_removidas"], 0)
        self.assertEqual(doc["ia"]["trechos"], [dict(de=0, ate=len(W) - 1, versoes=["A"])])

    def test_duracao_negativa_ainda_e_rejeitada(self):
        W = words("Isso é mesmo uma boa pergunta".split())
        W[1]["e"] = W[1]["t"] - .01
        with self.assertRaises(ValueError):
            limpeza.validar_remocoes(W, [])

    def test_ordem_quebrada_ainda_e_rejeitada(self):
        W = words("Isso é mesmo uma boa pergunta".split())
        W[2]["t"] = W[0]["t"] - .5
        with self.assertRaises(ValueError):
            limpeza.validar_remocoes(W, [])

    def test_caso_833_duplicata_longa_adjacente_pode_manter_primeira(self):
        W = words(DUP + DUP)
        r = limpeza.validar_remocoes(W, [proposal(10, 19, 0, 9, "duplicata")])
        self.assertEqual(r["palavras_removidas"], 10)
        self.assertEqual(r["remocoes"][0]["evidencia"]["correspondencia"], "duplicata_exata")
        doc = limpeza.criar_documento(W, r, W[-1]["e"] + 1)
        self.assertEqual(doc["versoes"][0]["faixas"][0], [0, W[10]["t"]])
        self.assertEqual(doc["versoes"][0]["faixas"][1][0], W[19]["e"])

    def test_duplicata_pode_manter_ultima_tambem(self):
        W = words(DUP + DUP)
        r = limpeza.validar_remocoes(W, [proposal(0, 9, 10, 19, "duplicata")])
        self.assertEqual(r["palavras_removidas"], 10)

    def test_prefixo_com_continuacao_numerica_ou_negacao_e_valido(self):
        for final in ("três coisas importantes", "nunca falha depois"):
            with self.subTest(final=final):
                fragment = "isso não vai mudar".split()
                whole = fragment + final.split()
                W = words(fragment + whole)
                r = limpeza.validar_remocoes(W, [proposal(0, 3, 4, 4 + len(whole) - 1)])
                self.assertEqual(r["palavras_removidas"], 4)

    def test_negacao_inserida_no_conteudo_substituido_nao_pode_sumir(self):
        W = words("isso vai funcionar isso não vai funcionar depois".split())
        r = limpeza.validar_remocoes(W, [proposal(0, 2, 3, 7)])
        self.assertEqual(r["palavras_removidas"], 0)
        self.assertTrue(r["avisos"])

    def test_numero_diferente_nao_e_equivalente(self):
        W = words("voce melhora em dois dias voce melhora em tres dias depois".split())
        r = limpeza.validar_remocoes(W, [proposal(0, 4, 5, 10)])
        self.assertEqual(r["palavras_removidas"], 0)

    def test_retoma_adjacente_apos_dez_segundos_mas_nao_trinta_e_um(self):
        W = words("voce vai conseguir voce vai conseguir melhorar tudo".split())
        shift_from(W, 3, 10)
        r = limpeza.validar_remocoes(W, [proposal(0, 2, 3, 7)])
        self.assertEqual(r["palavras_removidas"], 3)
        shift_from(W, 3, 21)
        r = limpeza.validar_remocoes(W, [proposal(0, 2, 3, 7)])
        self.assertEqual(r["palavras_removidas"], 0)

    def test_repeticao_curta_e_enfase_sao_preservadas(self):
        for text in ("muito muito", "não não", "eu quero eu quero"):
            W = words(text.split())
            mid = len(text.split()) // 2
            r = limpeza.validar_remocoes(W, [proposal(0, mid - 1, mid, mid * 2 - 1, "duplicata")])
            self.assertEqual(r["palavras_removidas"], 0)
        W = words(["Repito"] + DUP + DUP)
        r = limpeza.validar_remocoes(W, [proposal(1, 10, 11, 20, "duplicata")])
        self.assertEqual(r["palavras_removidas"], 0)

    def test_fala_unica_nao_some_com_substituto_inventado(self):
        W = words("Uma frase única diz algo diferente Outra frase tem informação importante".split())
        r = limpeza.validar_remocoes(W, [proposal(0, 5, 6, 10)])
        self.assertEqual(r["palavras_removidas"], 0)
        self.assertEqual(r["palavras_mantidas"], len(W))

    def test_subsequencia_curta_exige_cobertura_integral(self):
        W = words("voce vai melhorar voce realmente vai melhorar bastante".split())
        r = limpeza.validar_remocoes(W, [proposal(0, 2, 3, 7)])
        self.assertEqual(r["palavras_removidas"], 3)
        W = words("voce vai melhorar talvez voce realmente vai melhorar bastante".split())
        r = limpeza.validar_remocoes(W, [proposal(0, 3, 4, 8)])
        self.assertEqual(r["palavras_removidas"], 0)

    def test_fala_intermediaria_nao_e_pulada(self):
        W = words("voce vai melhorar novidade exclusiva voce vai melhorar bastante".split())
        r = limpeza.validar_remocoes(W, [proposal(0, 2, 5, 8)])
        self.assertEqual(r["palavras_removidas"], 0)

    def test_operacional_fechado_e_rotulo_lead_so_quando_isolados(self):
        for command in ("vou repetir", "Lead 02"):
            W = words(command.split())
            r = limpeza.validar_remocoes(W, [proposal(0, 1, tipo="operacional")])
            self.assertEqual(r["palavras_removidas"], 2)
        for command in ("Mecanismo", "corta gordura", "falei mal da academia"):
            W = words(command.split())
            r = limpeza.validar_remocoes(W, [proposal(0, len(command.split()) - 1, tipo="operacional")])
            self.assertEqual(r["palavras_removidas"], 0)
        W = words("corta".split(), gap=.05)
        r = limpeza.validar_remocoes(W, [proposal(0, 0, tipo="operacional")])
        self.assertEqual(r["palavras_removidas"], 0)

    def test_baixa_confianca_indices_invertidos_booleanos_e_nan_preservam(self):
        W = words(DUP + DUP)
        samples = [proposal(0, 9, 10, 19, "duplicata", .94),
                   proposal(9, 0, 10, 19, "duplicata"),
                   proposal(True, 9, 10, 19, "duplicata"),
                   proposal(0, 9, 10, 19, "duplicata", float("nan")),
                   proposal(0, 999, 10, 19, "duplicata")]
        for p in samples:
            r = limpeza.validar_remocoes(W, [p])
            self.assertEqual(r["palavras_removidas"], 0)
            json.dumps(r, allow_nan=False)

    def test_ciclos_e_substitutos_removidos_rejeitam_ambos(self):
        W = words(DUP + DUP)
        r = limpeza.validar_remocoes(W, [proposal(0, 9, 10, 19, "duplicata"), proposal(10, 19, 0, 9, "duplicata")])
        self.assertEqual(r["palavras_removidas"], 0)
        self.assertEqual(len(r["rejeitadas"]), 2)

    def test_cap_vinte_por_cento_nao_trunca_nem_resume(self):
        W = words(DUP + DUP, padding=20)
        r = limpeza.validar_remocoes(W, [proposal(0, 9, 10, 19, "duplicata")])
        self.assertEqual(r["palavras_removidas"], 0)
        self.assertIn("20%", r["avisos"][0])
        self.assertEqual(r["trechos"], [dict(de=0, ate=39, versoes=["A"])])

    def test_invasao_da_palavra_vizinha_nao_aprova(self):
        W = words(["valido"] + DUP + DUP)
        W[0]["e"] = W[1]["t"] + .1
        r = limpeza.validar_remocoes(W, [proposal(1, 10, 11, 20, "duplicata")])
        self.assertEqual(r["palavras_removidas"], 0)

    def test_todas_as_palavras_fora_remocao_aprovada_estao_cobertas(self):
        W = words(DUP + DUP)
        r = limpeza.validar_remocoes(W, [proposal(10, 19, 0, 9, "duplicata")])
        doc = limpeza.criar_documento(W, r, W[-1]["e"] + 2)
        faixas = doc["versoes"][0]["faixas"]
        for i, w in enumerate(W):
            if 10 <= i <= 19:
                continue
            self.assertTrue(any(a <= w["t"] and b >= w["e"] for a, b in faixas))
        self.assertEqual(r["palavras_total"], r["palavras_mantidas"] + r["palavras_removidas"])

    def test_resultado_externo_adulterado_nao_bypassa_evidencia(self):
        W = words("Toda fala valida permanece".split())
        r = limpeza.validar_remocoes(W, [])
        r["remocoes"] = [proposal(0, 2, 3, 5)]
        with self.assertRaises(ValueError):
            limpeza.criar_documento(W, r, W[-1]["e"] + 1)


class FluxoCortesTests(unittest.TestCase):
    def test_et_cortes_nao_usa_selecao_versoes_ou_conferencia_que_omite(self):
        import auto
        W = words("Toda fala valida deve permanecer aqui".split(), padding=20)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "fonte").mkdir()
            with wave.open(str(root / "fonte/voz16k.wav"), "wb") as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
                wav.writeframes(b"\x00\x00" * (16000 * 30))
            logs, calls = [], []
            convo = object()
            ctx = SimpleNamespace(d=td, W=lambda: W, est=SimpleNamespace(log=logs.append),
                                  cl=SimpleNamespace(conversa=lambda *a, **kw: convo),
                                  vd=lambda v: str(root / "versoes" / v))
            def pedir(conv, partes, schema, rotulo):
                calls.append(schema)
                self.assertEqual(set(schema["properties"]), {"remocoes", "observacoes"})
                return dict(remocoes=[], observacoes=["Dúvida preservada para revisão."])
            ctx.pedir = pedir
            with patch.object(auto, "rodar") as rodar, patch.object(auto.ops, "ler_mapa", return_value=([], 25)):
                auto.et_cortes(ctx)
            self.assertEqual(len(calls), 1)
            rodar.assert_called_once()
            self.assertEqual(rodar.call_args[0][0][-2:], ["--versoes", "A"])
            doc = json.loads((root / "fonte/cortes.json").read_text())
            self.assertEqual([v["id"] for v in doc["versoes"]], ["A"])
            self.assertEqual(doc["ia"]["limpeza"]["palavras_mantidas"], len(W))
            self.assertIn("Dúvida preservada", doc["observacoes"][0])


if __name__ == "__main__":
    unittest.main()
