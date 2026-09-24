"""Rascunho/publicado dos templates: um template criado pela skill criar-template fica de fora do uso real
até alguém publicar pela tela — aqui eu testo só a parte do servidor (estilos()/publicar_template()), sem
precisar da escrita em si (isso é tests/test_template_escrita.py)."""
import contextlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "app"))


def setUpModule():
    global servidor, template_escrita
    import servidor
    import template_escrita


ESTILO_BASE = dict(
    nome="X", descricao="d", usa=dict(letreiro=True, formatos=["cheia"], escuro=False, pb=False, flash=False),
    processo=[], modo="broll_primeiro",
    letreiro=dict(estilos=dict(sans=dict(familia="sans", px=96, cor=[255, 255, 255], tracking=-0.04, efeito="sombra")),
                  efeitos=dict(sombra=[[0, 0, 0, 0.45, 26, 0]])),
    legenda=dict(familia="demi", tam=60, pad=30, x=0.5, y=0.7, max_palavras=3, max_caracteres=17, sombras=[[0, 0, 0, 0.5, 10, 0]]),
)


class TemplatesPublicacaoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas-templates-pub-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        (self.base / "skill" / "estilos").mkdir(parents=True)
        self.stack.enter_context(patch.object(servidor, "SKILL", str(self.base / "skill")))
        self.stack.enter_context(patch.object(servidor.comum, "RAIZ", str(self.base / "projetos")))
        # servidor.SKILL e comum.SKILL são constantes SEPARADAS (cada módulo calcula a sua) — sem isto,
        # template_escrita.proximo_nome_versao() olharia pro estilos/ de verdade do repositório, não pro de
        # mentira que os testes daqui criam.
        self.stack.enter_context(patch.object(template_escrita.comum, "SKILL", str(self.base / "skill")))

    def cria_estilo(self, nome, publicado, origem="manual"):
        pasta = self.base / "skill" / "estilos" / nome
        pasta.mkdir(parents=True)
        E = dict(ESTILO_BASE, publicado=publicado, origem=origem)
        json.dump(E, open(pasta / "estilo.json", "w"))
        return pasta

    def test_estilos_esconde_rascunho_por_padrao(self):
        self.cria_estilo("normal", publicado=True)
        self.cria_estilo("rascunho", publicado=False)
        ids = [e["id"] for e in servidor.estilos()]
        self.assertIn("normal", ids)
        self.assertNotIn("rascunho", ids)

    def test_incluir_rascunhos_mostra_tudo(self):
        self.cria_estilo("normal", publicado=True)
        self.cria_estilo("rascunho", publicado=False)
        ids = [e["id"] for e in servidor.estilos(incluir_rascunhos=True)]
        self.assertCountEqual(ids, ["normal", "rascunho"])

    def test_estilo_sem_campo_publicado_conta_como_publicado(self):
        # os 3 templates de hoje (ultradinamico etc.) não têm essa chave — comportamento de sempre preservado.
        pasta = self.base / "skill" / "estilos" / "legado"
        pasta.mkdir(parents=True)
        json.dump(ESTILO_BASE, open(pasta / "estilo.json", "w"))
        self.assertIn("legado", [e["id"] for e in servidor.estilos()])

    def test_publicar_libera_o_rascunho(self):
        self.cria_estilo("rascunho", publicado=False)
        servidor.publicar_template("rascunho")
        E = json.load(open(self.base / "skill" / "estilos" / "rascunho" / "estilo.json"))
        self.assertTrue(E["publicado"])
        self.assertIn("rascunho", [e["id"] for e in servidor.estilos()])

    def test_publicar_falha_se_ja_publicado(self):
        self.cria_estilo("normal", publicado=True)
        with self.assertRaises(ValueError):
            servidor.publicar_template("normal")

    def test_publicar_falha_se_template_nao_existe(self):
        with self.assertRaises(ValueError):
            servidor.publicar_template("nao-existe")

if __name__ == "__main__":
    unittest.main()
