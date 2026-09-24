"""Resultados reais do B-roll: biblioteca utilizável, falhas externas e estado do Kanban.

Sem rede ou serviços pagos; os arquivos e o quadro ficam em diretório temporário.
"""
import contextlib
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
def setUpModule():
    # A suíte HTTP configura o runtime temporário durante a descoberta dos testes.
    # Adie imports para não fixar os caminhos pessoais antes dessa configuração.
    global biblioteca, kanban, leva
    import biblioteca
    import kanban
    import leva


class BrollTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.d = Path(self.temp.name)
        self.B = {"itens": {}, "proximo": 1}
        self.nomes = ["Parte 01"]
        self.T = self.plano(self.nomes)

    def plano(self, nomes):
        return {"por_anuncio": [dict(anuncio=n, broll_estimados=95, imagens=[dict(clipe="", pasta_ja_tem=False)]) for n in nomes],
                "categorias": [], "buscas": [dict(nome="treino", termos=["gym workout"], anuncios=nomes, categoria="Treino", o_que="treino")]}

    def clipe(self, i="B0001", **extras):
        arquivo = self.d / (i + ".mp4")
        arquivo.write_bytes(b"video existente")
        self.B["itens"][i] = dict(id=i, arquivo=str(arquivo), estado="disponivel", estudo={"descricao": "treino"},
                                  categoria="Treino", anuncios=self.nomes, **extras)
        return self.B["itens"][i]

    def resumo(self, achados=None):
        return leva.resumo_resultado(self.T, achados or {}, self.B["itens"], self.nomes)

    def test_catalogo_suficiente_nao_exige_novo_download(self):
        self.clipe()
        self.T["por_anuncio"][0]["imagens"][0]["clipe"] = "B0001"
        self.T["buscas"] = []
        resumo = self.resumo()
        leva.validar_resultado(resumo)
        self.assertEqual(resumo["por_anuncio"], {"Parte 01": 1})
        self.assertEqual((resumo["bons"], resumo["novos"], resumo["reaproveitados"]), (1, 0, 1))

    def test_resultado_parcial_util_pode_seguir(self):
        self.clipe()
        resumo = self.resumo({"treino": ["B0001", "B0001"]})
        leva.validar_resultado(resumo, [dict(fonte="YouTube", motivo="bloqueado")])
        self.assertEqual((resumo["bons"], resumo["novos"], resumo["total"]), (1, 1, 1))

    def test_outro_anuncio_nao_compensa_anuncio_zerado(self):
        self.clipe()
        self.nomes = ["Parte 01", "Parte 02"]
        self.T = self.plano(self.nomes)
        resumo = self.resumo({"treino": ["B0001"]})
        self.assertEqual(resumo["sem_broll"], ["Parte 02"])
        with self.assertRaisesRegex(RuntimeError, "Parte 02"):
            leva.validar_resultado(resumo)

    def test_arquivo_ausente_descartado_ou_sem_estudo_nao_contam(self):
        for estado in ("ausente", "descartado", "sem_estudo"):
            with self.subTest(estado=estado):
                item = self.clipe()
                if estado == "ausente": Path(item["arquivo"]).unlink()
                elif estado == "descartado": item["estado"] = "descartado"
                else: item["estudo"] = None
                resumo = self.resumo({"treino": ["B0001"]})
                self.assertEqual(resumo["bons"], 0)
                with self.assertRaisesRegex(RuntimeError, "Nenhum B-roll utilizável"):
                    leva.validar_resultado(resumo)

    def test_plano_sem_broll_e_permitido_mas_plano_ausente_nao(self):
        self.T["por_anuncio"][0].update(broll_estimados=0, imagens=[])
        leva.validar_resultado(self.resumo())
        self.T["por_anuncio"] = []
        with self.assertRaises(RuntimeError): leva.validar_resultado(self.resumo())

    def executar_leva(self, tt, yt):
        (self.d / "transcricoes").mkdir()
        (self.d / "transcricoes" / "01.json").write_text(json.dumps({"texto": "treino"}))
        (self.d / "pedido.json").write_text(json.dumps(dict(expert="Expert", oferta="Oferta", anuncios=[dict(nome="Parte 01", arquivo="unused")], fontes=["tiktok", "youtube"])))
        (self.d / "termos.json").write_text(json.dumps(self.T))
        (self.d / "estado.json").write_text(json.dumps({"etapas": [dict(id=i, nome=n, estado="ok" if i in ("termos", "transcricao") else "pendente") for i, n in leva.ETAPAS]}))
        @contextlib.contextmanager
        def mexer(): yield self.B
        with contextlib.ExitStack() as stack:
            for alvo, valor in [("leva.sys.argv", ["leva.py", str(self.d)]), ("leva.signal.signal", lambda *a: None),
                                ("leva.custos.token_apify", lambda: "test"), ("leva.custos.resumo", lambda d: dict(claude=0, apify=0, gemini=0)),
                                ("leva.youtube.disponivel", lambda: True), ("leva.youtube.buscar", yt), ("leva.tiktok.buscar", tt),
                                ("leva.biblioteca.ler", lambda: self.B), ("leva.biblioteca.mexer", mexer),
                                ("leva.biblioteca.marcar_anuncios", lambda *a: None), ("leva.biblioteca.mover", lambda *a: None)]:
                stack.enter_context(patch(alvo, valor))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            try: leva.main()
            except SystemExit as e: self.assertEqual(e.code, 1)
        return json.loads((self.d / "estado.json").read_text())

    def test_falha_total_nao_fica_pronta_e_preserva_diagnostico_das_fontes(self):
        def yt(*args): raise RuntimeError("Sign in to confirm you're not a bot")
        estado = self.executar_leva(lambda *a, **kw: dict(ids=[], novos=[]), yt)
        self.assertEqual(estado["status"], "erro")
        self.assertEqual(estado["resumo"]["total"], 0)
        self.assertEqual(estado["resumo"]["sem_broll"], ["Parte 01"])
        self.assertEqual({f["fonte"] for f in estado["falhas_busca"]}, {"TikTok", "YouTube"})
        self.assertIn("verificação de robô", estado["mensagem"])
        self.assertIn("nenhum clipe foi baixado", estado["mensagem"])
        self.assertEqual(next(e for e in estado["etapas"] if e["id"] == "organizacao")["estado"], "erro")

    def test_uma_fonte_com_clipe_util_e_outra_falhou_conclui_com_aviso(self):
        self.clipe()
        def yt(*args): raise RuntimeError("Sign in to confirm you're not a bot")
        estado = self.executar_leva(lambda *a, **kw: dict(ids=["B0001"], novos=["B0001"]), yt)
        self.assertEqual(estado["status"], "pronto")
        self.assertEqual(estado["resumo"]["por_anuncio"], {"Parte 01": 1})
        self.assertEqual(len(estado["falhas_busca"]), 1)

    def test_bloqueio_youtube_interrompe_novas_tentativas_da_fonte(self):
        self.clipe()
        self.T["buscas"] = [dict(self.T["buscas"][0], nome=f"treino {k}") for k in range(18)]
        chamadas = []
        class Bloqueio(RuntimeError): pass
        def yt(*args):
            chamadas.append(args)
            raise Bloqueio("YouTube bloqueou o download na VPS (verificação de robô)")
        with patch.object(leva.youtube, "BloqueioYouTube", Bloqueio, create=True):
            estado = self.executar_leva(lambda *a, **kw: dict(ids=["B0001"], novos=["B0001"]), yt)
        self.assertEqual(estado["status"], "pronto")
        self.assertGreaterEqual(len(chamadas), 1)
        self.assertLessEqual(len(chamadas), 2)  # no máximo as duas buscas já em voo
        self.assertEqual(estado["resumo"]["bons"], 1)


class BuscaAgendadaTests(unittest.TestCase):
    """main_youtube_agendada: sem anúncio, repete rodadas (Claude planeja -> busca no YouTube) até a duração
    acabar ou o YouTube bloquear. Claude e YouTube são falsos aqui — o que se testa é o laço em si."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.d = Path(self.temp.name)
        self.B = {"itens": {"B0001": dict(id="B0001", estado="disponivel", estudo={"descricao": "treino"},
                                          categoria="Treino", levas=[], expert="Expert", oferta="Oferta")}, "proximo": 2}

    def rodar(self, termos_fake, yt_fake, duracao_horas):
        ped = dict(expert="Expert", oferta="Oferta", modo="youtube_agendada",
                   agendado_em=time.time(), duracao_horas=duracao_horas)
        (self.d / "pedido.json").write_text(json.dumps(ped))
        @contextlib.contextmanager
        def mexer(): yield self.B
        with contextlib.ExitStack() as stack:
            for alvo, valor in [("leva.signal.signal", lambda *a: None),
                                ("leva.youtube.disponivel", lambda: True),
                                ("leva.termos_agendada", termos_fake), ("leva.yt_uma", yt_fake),
                                ("leva.custos.resumo", lambda d: dict(claude=0, apify=0, gemini=0)),
                                ("leva.custos.registrar", lambda *a, **kw: None),
                                ("leva.biblioteca.ler", lambda: self.B), ("leva.biblioteca.mexer", mexer),
                                ("leva.biblioteca.item", lambda i: self.B["itens"].get(i)),
                                ("leva.biblioteca.mover", lambda *a: None),
                                ("leva.biblioteca.motor_estudo", lambda: "gemini")]:
                stack.enter_context(patch(alvo, valor))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            leva.main_youtube_agendada(str(self.d), ped)
        return json.loads((self.d / "estado.json").read_text())

    def test_repete_rodadas_variando_termos_ate_a_duracao_acabar(self):
        chamadas = []
        def termos_fake(expert, oferta, ja_buscados, log=None, ao_cobrar=None):
            chamadas.append(list(ja_buscados)); time.sleep(0.05)
            return [dict(nome=f"busca {len(chamadas)}", categoria="Treino", termos=[f"termo {len(chamadas)}"], o_que="treino")]
        def yt_fake(ctx, b): ctx.guardar(b, ["B0001"], [])
        estado = self.rodar(termos_fake, yt_fake, duracao_horas=0.15 / 3600)
        self.assertGreaterEqual(len(chamadas), 2)  # mais de uma rodada rodou de verdade
        self.assertIn("termo 1", chamadas[1])  # a 2ª rodada já sabia o termo da 1ª (não repete)
        self.assertEqual(estado["status"], "pronto")
        self.assertIn("completou a duração agendada", estado["mensagem"])

    def test_para_na_hora_se_o_youtube_bloqueia(self):
        chamadas = []
        def termos_fake(expert, oferta, ja_buscados, log=None, ao_cobrar=None):
            chamadas.append(1); return [dict(nome="busca 1", categoria="Treino", termos=["termo"], o_que="treino")]
        def yt_fake(ctx, b): ctx.youtube_bloqueado.set()
        estado = self.rodar(termos_fake, yt_fake, duracao_horas=1)  # duração longa: só o bloqueio deve parar
        self.assertEqual(len(chamadas), 1)  # não tentou uma segunda rodada depois de bloqueado
        self.assertEqual(estado["status"], "pronto")
        self.assertIn("bloqueou o acesso", estado["mensagem"])

    def test_para_quando_nao_ha_mais_nada_pra_buscar(self):
        def termos_fake(expert, oferta, ja_buscados, log=None, ao_cobrar=None): return []
        estado = self.rodar(termos_fake, lambda ctx, b: None, duracao_horas=1)
        self.assertEqual(estado["status"], "pronto")
        self.assertIn("já estavam cobertas", estado["mensagem"])
        self.assertEqual(estado["resumo"]["total"], 0)

    def test_erro_no_plano_encerra_com_diagnostico_sem_travar(self):
        def termos_fake(expert, oferta, ja_buscados, log=None, ao_cobrar=None): raise RuntimeError("chave inválida")
        estado = self.rodar(termos_fake, lambda ctx, b: None, duracao_horas=1)
        self.assertEqual(estado["status"], "pronto")  # erro de UMA rodada não é erro fatal — só para de tentar
        self.assertEqual(estado["resumo"]["total"], 0)


class ListarAgendadasPendentesTests(unittest.TestCase):
    """listar_agendadas_pendentes: o que laco_agendadas() usa pra saber o que lançar agora — tem que achar só
    o que já chegou a hora e nunca foi lançado, nunca lançar duas vezes o mesmo pedido."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.brolls = Path(self.temp.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(leva.biblioteca, "BROLLS", str(self.brolls)))

    def cria(self, nome, modo="youtube_agendada", agendado_em=None, com_estado=False):
        d = self.brolls / "Expert" / "Oferta" / ".levas" / nome
        d.mkdir(parents=True)
        ped = dict(expert="Expert", oferta="Oferta", duracao_horas=1)
        if modo is not None: ped["modo"] = modo
        if agendado_em is not None: ped["agendado_em"] = agendado_em
        (d / "pedido.json").write_text(json.dumps(ped))
        if com_estado: (d / "estado.json").write_text("{}")
        return str(d)

    def test_acha_pendente_cuja_hora_ja_chegou(self):
        d = self.cria("ja-chegou", agendado_em=time.time() - 10)
        self.assertEqual(leva.listar_agendadas_pendentes(), [d])

    def test_ignora_cuja_hora_ainda_nao_chegou(self):
        self.cria("no-futuro", agendado_em=time.time() + 3600)
        self.assertEqual(leva.listar_agendadas_pendentes(), [])

    def test_ignora_o_que_ja_foi_lancado(self):
        self.cria("ja-lancado", agendado_em=time.time() - 10, com_estado=True)
        self.assertEqual(leva.listar_agendadas_pendentes(), [])

    def test_ignora_leva_normal_a_partir_de_anuncio(self):
        self.cria("leva-normal", modo=None, agendado_em=time.time() - 10)
        self.assertEqual(leva.listar_agendadas_pendentes(), [])


class KanbanBrollTests(unittest.TestCase):
    def test_falha_preserva_leva_e_quadro_expoe_etapa_resumo_log(self):
        with tempfile.TemporaryDirectory() as td, contextlib.ExitStack() as stack:
            d = Path(td)
            for alvo, valor in [("kanban.RAIZ", str(d / "kanban")), ("kanban.ARQ", str(d / "kanban" / "quadro.json")),
                                ("kanban.comum.RAIZ", str(d / "projetos")), ("kanban.biblioteca.BROLLS", str(d / "brolls"))]:
                stack.enter_context(patch(alvo, valor))
            video = d / "orig.mp4"; video.write_bytes(b"video")
            lid = kanban.criar_lote("Demanda", "Expert", "Oferta", "ultradinamico", [("Anuncio", str(video))])
            cid = next(iter(kanban.ler()["cards"]))
            kanban.mover([cid], "broll")
            def subprocesso(*args, **kwargs):
                card = kanban.ler()["cards"][cid]
                dl = Path(card["leva"])
                self.assertEqual(card["lote"], lid)
                self.assertTrue(Path(card["video_leva"]).is_file())
                self.assertEqual(Path(card["transcricao"]).parent, dl / "transcricoes")
                (dl / "estado.json").write_text(json.dumps(dict(status="erro", mensagem="Nenhum clipe foi salvo", resumo={"bons": 0, "sem_broll": ["Anuncio"]},
                                                                etapas=[dict(nome="Busca", estado="rodando")], log=["download bloqueado"],
                                                                falhas_busca=[dict(fonte="YouTube", busca="treino", motivo="bloqueado")])) )
                raise RuntimeError("Nenhum clipe foi salvo")
            stack.enter_context(patch.object(kanban, "roda_cmd", subprocesso))
            self.assertEqual(kanban.passo(log=lambda *a: None)["estado"], "erro")
            card = kanban.quadro()["cards"][0]
            self.assertEqual(card["estado"], "erro")
            self.assertEqual(card["broll"]["resumo"]["bons"], 0)
            self.assertEqual(card["broll"]["status"], "erro")
            self.assertEqual(card["etapa"], "Busca")
            self.assertEqual(card["log"], ["download bloqueado"])


if __name__ == "__main__": unittest.main()
