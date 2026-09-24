"""app/servidor.py: agendar_busca_youtube/cancelar_agendada e a parte de levas() que mostra o agendamento
antes de lançar. Sem rede — as checagens de chave/yt-dlp são falsificadas."""
import contextlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "app"))


def setUpModule():
    global servidor
    import servidor


class AgendarBuscaYoutubeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas-busca-agendada-")
        self.addCleanup(self.tmp.cleanup)
        self.brolls = Path(self.tmp.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(servidor.biblioteca, "BROLLS", str(self.brolls)))
        self.stack.enter_context(patch.object(servidor.chaves, "ler", lambda: dict(anthropic="chave-de-teste")))
        self.stack.enter_context(patch.object(servidor.gemini, "chave", lambda: "chave-de-teste"))
        self.stack.enter_context(patch.object(servidor.youtube, "disponivel", lambda: True))

    def pedido(self, **sobrepor):
        base = dict(expert="Expert", oferta="Oferta", agendado_em=time.time() + 3600, duracao_horas=8)
        base.update(sobrepor)
        return base

    def test_agenda_sem_lancar_nada(self):
        rel = servidor.agendar_busca_youtube(self.pedido())
        dl = self.brolls / rel
        self.assertTrue((dl / "pedido.json").exists())
        self.assertFalse((dl / "estado.json").exists())
        ped = json.loads((dl / "pedido.json").read_text())
        self.assertEqual(ped["modo"], "youtube_agendada")
        self.assertEqual(ped["fontes"], ["youtube"])

    def test_duas_chamadas_no_mesmo_segundo_nao_colidem(self):
        # Regressão: o nome da pasta só com data-hora (segundo a segundo) colidia num duplo-clique real —
        # sem upload de anúncio no meio pra segurar o ritmo, é bem mais fácil de acontecer aqui do que na
        # leva normal. As duas chamadas têm que resultar em pastas DIFERENTES, nunca um FileExistsError.
        rel1 = servidor.agendar_busca_youtube(self.pedido())
        rel2 = servidor.agendar_busca_youtube(self.pedido())
        self.assertNotEqual(rel1, rel2)
        self.assertTrue((self.brolls / rel1 / "pedido.json").exists())
        self.assertTrue((self.brolls / rel2 / "pedido.json").exists())

    def test_recusa_horario_no_passado(self):
        with self.assertRaises(ValueError):
            servidor.agendar_busca_youtube(self.pedido(agendado_em=time.time() - 3600))

    def test_recusa_duracao_fora_do_intervalo(self):
        for duracao in (0.1, 30):
            with self.subTest(duracao=duracao), self.assertRaises(ValueError):
                servidor.agendar_busca_youtube(self.pedido(duracao_horas=duracao))

    def test_recusa_sem_expert_ou_oferta(self):
        with self.assertRaises(ValueError):
            servidor.agendar_busca_youtube(self.pedido(expert=""))

    def test_levas_mostra_agendada_antes_de_lancar(self):
        rel = servidor.agendar_busca_youtube(self.pedido())
        L = servidor.levas()
        self.assertEqual(len(L), 1)
        self.assertEqual(L[0]["id"], rel)
        self.assertTrue(L[0]["agendada"])
        self.assertEqual(L[0]["estado"]["status"], "agendada")
        self.assertEqual(L[0]["anuncios"], [])

    def test_cancelar_agendada_apaga_a_pasta(self):
        rel = servidor.agendar_busca_youtube(self.pedido())
        servidor.cancelar_agendada(rel)
        self.assertFalse((self.brolls / rel).exists())

    def test_cancelar_agendada_ja_lancada_recusa(self):
        rel = servidor.agendar_busca_youtube(self.pedido())
        (self.brolls / rel / "estado.json").write_text("{}")
        with self.assertRaises(ValueError):
            servidor.cancelar_agendada(rel)


if __name__ == "__main__":
    unittest.main()
