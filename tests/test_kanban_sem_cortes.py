"""Fluxo Kanban com vídeo já cortado e reenvio único, sem rede ou mídias reais."""
import contextlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

def setUpModule():
    global kanban
    import kanban

class KanbanSemCortes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas-kanban-integral-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for target, value in [("RAIZ", self.base / "quadro"), ("ARQ", self.base / "quadro/quadro.json")]:
            self.stack.enter_context(patch.object(kanban, target, str(value)))
        self.stack.enter_context(patch.object(kanban.comum, "RAIZ", str(self.base / "projetos")))
        self.stack.enter_context(patch.object(kanban.biblioteca, "BROLLS", str(self.base / "brolls")))
        self.leva = self.base / "brolls/Expert/Oferta/.levas/busca-original"
        self.leva.mkdir(parents=True)
        self.video_old = self.base / "old.mp4"
        self.video_old.write_bytes(b"original raw video")
        self.video_new = self.base / "new.mp4"
        self.video_new.write_bytes(b"different, already edited video")

    def criar(self, nome="Parte 01", video=None, expert="Expert", oferta="Oferta"):
        lid = kanban.criar_lote("Mini VSL", expert, oferta, "ultradinamico", [(nome, str(video or self.video_new))])
        q = kanban.ler()
        return next(c for c in q["cards"].values() if c["lote"] == lid)

    def reservar(self):
        old = self.criar(video=self.video_old)
        with kanban.mexer() as q:
            q["cards"][old["id"]].update(coluna="cortes", estado="ok", projeto="Parte 01", leva=str(self.leva))
            q["reenvios"] = {old["id"]: dict(id=old["id"], card_origem=old["id"], nome="Parte 01", expert="Expert", oferta="Oferta",
                                               leva=str(self.leva), video_origem=str(self.video_old), estado="pendente")}
        return old

    def test_regular_upload_uses_broll_then_edition_without_cuts_column(self):
        c = self.criar(nome="Outro")
        self.assertEqual(c["coluna"], "broll")
        self.assertEqual(c["estado"], "espera")
        self.assertTrue(c["video_ja_cortado"])
        self.assertNotIn("cortes", dict(kanban.COLUNAS))
        self.assertNotIn("novas", dict(kanban.COLUNAS))
        with self.assertRaises(ValueError): kanban.mover([c["id"]], "cortes")
        with self.assertRaises(ValueError): kanban.mover([c["id"]], "novas")
        self.assertEqual(kanban.proximo(kanban.ler()), [("lote", c["lote"])])
        kanban.mover([c["id"]], "edicao")
        self.assertEqual(kanban.proximo(kanban.ler()), [("card", c["id"])])

    def test_broll_success_advances_straight_to_edicao_without_dragging(self):
        c = self.criar(nome="Outro")
        with patch.object(kanban, "fase_broll") as search:
            kanban.passo(log=lambda _: None)
        search.assert_called_once()
        x = kanban.ler()["cards"][c["id"]]
        self.assertEqual((x["coluna"], x["estado"]), ("edicao", "espera"))
        self.assertFalse(x["revisado"])
        self.assertEqual(kanban.proximo(kanban.ler()), [("card", c["id"])])

    def test_broll_failure_stays_in_broll_for_retry(self):
        c = self.criar(nome="Outro")
        with patch.object(kanban, "fase_broll", side_effect=RuntimeError("a busca falhou")):
            kanban.passo(log=lambda _: None)
        x = kanban.ler()["cards"][c["id"]]
        self.assertEqual((x["coluna"], x["estado"]), ("broll", "erro"))
        self.assertIn("a busca falhou", x["msg"])
        kanban.mover([c["id"]], "broll")
        self.assertEqual(kanban.ler()["cards"][c["id"]]["estado"], "espera")

    def test_two_ready_edicao_cards_run_truly_concurrently(self):
        a = self.criar(nome="A"); b = self.criar(nome="B")
        kanban.mover([a["id"], b["id"]], "edicao")
        marcas = []
        portao = threading.Barrier(2, timeout=15)  # só destrava quando os DOIS já chegaram aqui ao mesmo tempo

        def trabalho(col, lote, card, log):
            marcas.append(("inicio", card["nome"]))
            portao.wait()
            marcas.append(("fim", card["nome"]))

        with patch.object(kanban, "fase_projeto", side_effect=trabalho):
            n = kanban.lancar_prontos(log=lambda _: None)
            self.assertEqual(n, 2)
            limite = time.monotonic() + 15
            # len(marcas)==4 só prova que as DUAS trabalho() retornaram — não que a gravação final (mexer(), depois
            # do portão) já aconteceu nas duas threads. Espera o estado real do card, não o marcador do mock.
            while any(kanban.ler()["cards"][i]["estado"] == "rodando" for i in (a["id"], b["id"])) and time.monotonic() < limite:
                time.sleep(0.01)
        self.assertEqual(len(marcas), 4)
        self.assertEqual({m[1] for m in marcas if m[0] == "fim"}, {"A", "B"})
        q = kanban.ler()
        self.assertEqual(q["cards"][a["id"]]["estado"], "ok")
        self.assertEqual(q["cards"][b["id"]]["estado"], "ok")

    def test_lancar_prontos_never_exceeds_concurrency_limit(self):
        cards = [self.criar(nome=n) for n in ("A", "B", "C")]
        ids = [c["id"] for c in cards]
        kanban.mover(ids, "edicao")
        bloqueio = threading.Event()
        trabalho = lambda col, lote, card, log: bloqueio.wait(15)
        with patch.object(kanban, "LIMITE_CONCORRENCIA", 2), patch.object(kanban, "fase_projeto", side_effect=trabalho):
            self.assertEqual(kanban.lancar_prontos(log=lambda _: None), 2)
            time.sleep(0.05)
            self.assertEqual(sorted(kanban.ler()["cards"][i]["estado"] for i in ids), ["espera", "rodando", "rodando"])
            self.assertEqual(kanban.lancar_prontos(log=lambda _: None), 0)  # ainda sem vaga: não estoura o limite
            bloqueio.set()
            limite = time.monotonic() + 15
            while any(kanban.ler()["cards"][i]["estado"] == "rodando" for i in ids) and time.monotonic() < limite:
                time.sleep(0.01)
        self.assertEqual([kanban.ler()["cards"][i]["estado"] for i in ids[:2]], ["ok", "ok"])

    def test_render_never_runs_more_than_one_at_a_time(self):
        cards = [self.criar(nome=n) for n in ("A", "B")]
        ids = [c["id"] for c in cards]
        kanban.mover(ids, "render")
        bloqueio = threading.Event()
        trabalho = lambda col, lote, card, log: bloqueio.wait(15)
        with patch.object(kanban, "fase_projeto", side_effect=trabalho):
            self.assertEqual(kanban.lancar_prontos(log=lambda _: None), 1)  # LIMITE_CONCORRENCIA padrão é 2, mas render é sempre 1
            time.sleep(0.05)
            self.assertEqual(sorted(kanban.ler()["cards"][i]["estado"] for i in ids), ["espera", "rodando"])
            bloqueio.set()
            limite = time.monotonic() + 15
            while kanban.ler()["cards"][ids[0]]["estado"] == "rodando" and time.monotonic() < limite: time.sleep(0.01)
        self.assertEqual(kanban.ler()["cards"][ids[0]]["estado"], "ok")

    def test_proximo_never_picks_two_cards_that_share_the_same_project_folder(self):
        # Dois anúncios com o mesmo nome no mesmo lote (erro de digitação, por ex.) caem na mesma pasta de
        # projeto — nunca podem rodar fase_projeto() ao mesmo tempo, ou um pisa no outro.
        lid = kanban.criar_lote("Duplicado", "Expert", "Oferta", "ultradinamico",
                                 [("Mesmo Nome", str(self.video_new)), ("Mesmo Nome", str(self.video_new))])
        ids = [c["id"] for c in kanban.ler()["cards"].values() if c["lote"] == lid]
        kanban.mover(ids, "edicao")
        escolhidos = kanban.proximo(kanban.ler(), 5)
        self.assertEqual(len(escolhidos), 1)

    def test_recuperar_travados_frees_stuck_rodando_cards_after_restart(self):
        c = self.criar(nome="Preso")
        with kanban.mexer() as q: q["cards"][c["id"]].update(estado="rodando")
        import workspaces
        with patch.object(workspaces, "listar", return_value=[dict(id=workspaces.PADRAO_ID, pasta="")]):
            recuperados = kanban.recuperar_travados()
        self.assertEqual(recuperados, [c["id"]])
        x = kanban.ler()["cards"][c["id"]]
        self.assertEqual(x["estado"], "erro")
        self.assertIn("reinício", x["msg"])

    def test_replacement_survives_deleting_old_card_and_lot_and_is_single_use(self):
        old = self.reservar()
        kanban.apagar([old["id"]])
        first = self.criar()
        self.assertEqual((first["coluna"], first["estado"]), ("edicao", "espera"))
        self.assertTrue(first["sem_novas_buscas"])
        self.assertIsNone(first["transcricao"])
        self.assertIsNone(first["video_leva"])
        self.assertEqual(first["video"], str(self.video_new))
        self.assertEqual(first["leva"], str(self.leva))
        self.assertFalse(first["revisado"])
        self.assertEqual(kanban.ler()["reenvios"][old["id"]]["card_destino"], first["id"])
        self.assertEqual(self.criar()["coluna"], "broll")
        self.assertEqual(kanban.quadro()["reenvios_pendentes"], [])
        self.assertTrue(self.video_old.exists())

    def test_same_name_on_another_offer_or_expert_does_not_consume(self):
        old = self.reservar()
        for options in [dict(oferta="Outra"), dict(expert="Outro"), dict(nome="Parte 02")]:
            self.assertEqual(self.criar(**options)["coluna"], "broll")
        self.assertEqual(kanban.ler()["reenvios"][old["id"]]["estado"], "pendente")
        self.assertEqual(len(kanban.quadro()["reenvios_pendentes"]), 1)

    def test_migration_keeps_old_video_stopped_without_consuming_exception(self):
        old = self.reservar()
        self.assertEqual(kanban.migrar_fluxo_sem_cortes(), [old["id"]])
        q = kanban.ler(); c = q["cards"][old["id"]]
        self.assertEqual((c["coluna"], c["estado"]), ("edicao", "parado"))
        self.assertEqual(q["reenvios"][old["id"]]["estado"], "pendente")
        self.assertEqual(kanban.proximo(q), [])
        self.assertFalse(kanban.quadro()["cards"][0]["pode_abrir_editor"])
        self.assertEqual(kanban.migrar_fluxo_sem_cortes(), [])

    def test_migration_moves_cards_stuck_in_retired_novas_column_to_broll(self):
        c = self.criar(nome="Presa antes da atualização")
        with kanban.mexer() as q: q["cards"][c["id"]].update(coluna="novas", estado="parado")
        self.assertEqual(kanban.migrar_fluxo_sem_cortes(), [c["id"]])
        x = kanban.ler()["cards"][c["id"]]
        self.assertEqual((x["coluna"], x["estado"]), ("broll", "espera"))
        self.assertEqual(kanban.proximo(kanban.ler()), [("lote", c["lote"])])
        self.assertEqual(kanban.migrar_fluxo_sem_cortes(), [])

    def test_mixed_lot_never_sends_replacement_to_broll_search(self):
        old = self.reservar(); kanban.apagar([old["id"]])
        lid = kanban.criar_lote("Mixed", "Expert", "Oferta", "ultradinamico", [("Parte 01", str(self.video_new)), ("Outro", str(self.video_new))])
        cards = [c for c in kanban.ler()["cards"].values() if c["lote"] == lid]
        replacement = next(c for c in cards if c["nome"] == "Parte 01")
        other = next(c for c in cards if c["nome"] == "Outro")
        kanban.mover([other["id"]], "broll")
        with patch.object(kanban, "fase_broll") as search:
            kanban.passo(log=lambda _: None)
        self.assertEqual([c["id"] for c in search.call_args.args[1]], [other["id"]])
        self.assertEqual(kanban.ler()["cards"][replacement["id"]]["estado"], "espera")
        kanban.mover([replacement["id"]], "broll")
        self.assertEqual(kanban.ler()["cards"][replacement["id"]]["coluna"], "edicao")
        with self.assertRaises(ValueError): kanban.fase_broll({}, [replacement], lambda _: None)

    def test_new_request_uses_new_media_new_transcription_and_existing_broll_plan(self):
        old = self.reservar(); kanban.apagar([old["id"]])
        c = self.criar(); q = kanban.ler(); lote = q["lotes"][c["lote"]]
        with patch.object(kanban, "roda_cmd") as run, patch.object(kanban.biblioteca, "nome_oferta", return_value="Oferta"):
            kanban.fase_projeto("edicao", lote, c, lambda _: None)
        p = self.base / "projetos/Parte 01/auto/pedido.json"
        ped = json.loads(p.read_text())
        self.assertEqual(ped["video"], str(self.video_new))
        self.assertIsNone(ped["whisper"])
        self.assertTrue(ped["video_ja_cortado"])
        self.assertTrue(ped["sem_novas_buscas"])
        self.assertFalse(ped["buscar_tiktok"] or ped["buscar_youtube"])
        self.assertEqual(ped["anuncio_origem"], dict(nome="Parte 01", leva="busca-original"))
        self.assertEqual(ped["leva_termos"], str(self.leva / "termos.json"))
        self.assertEqual(ped["anuncio"], "Parte 01")
        self.assertEqual(run.call_args.args[0][-2:], ["--ate", "letreiros"])
        # A retry is the same request; it cannot silently attach another card or video.
        c["video"] = str(self.video_old)
        before = p.read_bytes()
        with patch.object(kanban, "roda_cmd") as run:
            with self.assertRaisesRegex(ValueError, "projeto anterior"):
                kanban.fase_projeto("edicao", lote, c, lambda _: None)
            run.assert_not_called()
        self.assertEqual(p.read_bytes(), before)

    def test_invalid_origin_does_not_consume_or_create_card(self):
        old = self.reservar()
        self.leva.rmdir()
        before = Path(kanban.ARQ).read_bytes()
        with self.assertRaises(ValueError): self.criar()
        self.assertEqual(Path(kanban.ARQ).read_bytes(), before)

    def test_same_source_cannot_be_reuploaded_by_reference_as_replacement(self):
        self.reservar()
        before = Path(kanban.ARQ).read_bytes()
        with self.assertRaisesRegex(ValueError, "novo vídeo"):
            self.criar(video=self.video_old)
        self.assertEqual(Path(kanban.ARQ).read_bytes(), before)

if __name__ == "__main__": unittest.main()
