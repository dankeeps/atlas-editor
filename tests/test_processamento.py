"""Abas Visão Geral e Processamento: contadores do workspace e o que está rodando agora, sem rede real."""
import contextlib
import json
import os
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
    global servidor, kanban, biblioteca
    import servidor
    import kanban
    import biblioteca


class VisaoGeralEProcessamento(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas-processamento-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for target, value in [("RAIZ", self.base / "quadro"), ("ARQ", self.base / "quadro/quadro.json")]:
            self.stack.enter_context(patch.object(kanban, target, str(value)))
        self.stack.enter_context(patch.object(kanban.comum, "RAIZ", str(self.base / "projetos")))
        self.stack.enter_context(patch.object(kanban.biblioteca, "BROLLS", str(self.base / "brolls")))
        self.stack.enter_context(patch.object(biblioteca, "BROLLS", str(self.base / "brolls")))
        self.stack.enter_context(patch.object(biblioteca, "RAIZ", str(self.base / "brolls" / "biblioteca")))
        self.stack.enter_context(patch.object(servidor, "JOBS", {}))
        self.video = self.base / "video.mp4"
        self.video.write_bytes(b"fake")

    def test_visao_geral_counts_by_column_and_recent_brolls(self):
        lid = kanban.criar_lote("Lote", "Expert", "Oferta", "ultradinamico", [("Pronto1", str(self.video)), ("Ativo1", str(self.video))])
        cards = list(kanban.ler()["cards"].values())
        pronto, ativo = cards[0], cards[1]
        with kanban.mexer() as q: q["cards"][pronto["id"]].update(coluna="pronto", estado="parado")
        broll_novo = self.base / "b1.mp4"; broll_novo.write_bytes(b"x")
        broll_velho = self.base / "b2.mp4"; broll_velho.write_bytes(b"y")
        i1 = biblioteca.adicionar(arquivo=str(broll_novo), fonte="teste")
        i2 = biblioteca.adicionar(arquivo=str(broll_velho), fonte="teste")
        with biblioteca.mexer() as B: B["itens"][i2]["baixado"] = "2000-01-01"
        d = servidor.visao_geral()
        self.assertEqual(d["em_producao"], 1)   # só o Ativo1 (Pronto1 já foi pra "pronto")
        self.assertEqual(d["editados"], 1)
        self.assertEqual(d["brolls_semana"], 1)  # só o de hoje conta, o de 2000 não

    def test_processamento_lists_running_kanban_and_editor_jobs_with_elapsed_time(self):
        lid = kanban.criar_lote("Lote", "Expert", "Oferta", "ultradinamico", [("Rodando1", str(self.video))])
        card = next(iter(kanban.ler()["cards"].values()))
        ha_10_min = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 600))
        with kanban.mexer() as q: q["cards"][card["id"]].update(estado="rodando", atualizado=ha_10_min)
        servidor.JOBS[("slug", "A")] = dict(rodando=True, etapa="renderizando", inicio=time.time() - 30)
        d = servidor.processamento()
        por_sistema = {it["sistema"]: it for it in d["itens"]}
        self.assertEqual(len(d["itens"]), 2)
        self.assertAlmostEqual(por_sistema["Kanban"]["segundos"], 600, delta=5)
        self.assertAlmostEqual(por_sistema["Editor"]["segundos"], 30, delta=5)
        self.assertEqual(por_sistema["Editor"]["nome"], "slug · A")
        # Kanban (600s) vem antes do Editor (30s): mais tempo rodando primeiro.
        self.assertEqual(d["itens"][0]["sistema"], "Kanban")

    def test_processamento_ignores_jobs_not_currently_running(self):
        kanban.criar_lote("Lote", "Expert", "Oferta", "ultradinamico", [("Parado1", str(self.video))])
        servidor.JOBS[("slug", "A")] = dict(rodando=False, etapa="pronto")
        d = servidor.processamento()
        self.assertEqual(d["itens"], [])

    def test_processamento_lists_running_edicao_automatica_como_editor(self):
        # a edição automática (auto.py) roda num processo à parte, fora de JOBS — regressão do caso real
        # "AM80": o job aparecia no painel da própria aba, mas sumia da tabela "o que está rodando agora".
        pasta = self.base / "projetos" / "AM80" / "auto"
        pasta.mkdir(parents=True)
        json.dump(dict(nome="AM80"), open(str(pasta / "pedido.json"), "w"))
        json.dump(dict(rodando=True, pid=os.getpid(), status="rodando", inicio=time.time() - 45,
                       etapas=[dict(id="transcricao", nome="Transcrição da fala", estado="ok"),
                               dict(id="cortes", nome="Cortes: silêncios e erros", estado="rodando")]),
                  open(str(pasta / "estado.json"), "w"))
        d = servidor.processamento()
        item = next(it for it in d["itens"] if it["sistema"] == "Editor" and it["nome"] == "AM80")
        self.assertEqual(item["tipo"], "Cortes: silêncios e erros")
        self.assertAlmostEqual(item["segundos"], 45, delta=5)

    def test_processamento_ignores_edicao_automatica_ja_terminada(self):
        pasta = self.base / "projetos" / "Pronto1" / "auto"
        pasta.mkdir(parents=True)
        json.dump(dict(nome="Pronto1"), open(str(pasta / "pedido.json"), "w"))
        json.dump(dict(rodando=False, status="pronto", etapas=[]), open(str(pasta / "estado.json"), "w"))
        d = servidor.processamento()
        self.assertEqual(d["itens"], [])

    def test_stats_sistema_returns_plausible_numbers(self):
        d = servidor.stats_sistema()
        self.assertGreaterEqual(d["nucleos"], 1)
        if d["cpu_pct"] is not None: self.assertTrue(0 <= d["cpu_pct"] <= 100)
        if d["mem_total"] is not None: self.assertLessEqual(d["mem_usado"], d["mem_total"])


if __name__ == "__main__":
    unittest.main()
