"""scripts/criar_template.py: a única porta de entrada da skill criar-template pra estilos/ e fontes/. Testa
os comandos por dentro (importando o módulo, sem subprocess) — cada um só chama uma função de lib/ ou
app/servidor.py que já tem teste próprio; aqui é só conferir que o CLI liga os fios certos."""
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
sys.path.insert(0, str(ROOT / "scripts"))


def setUpModule():
    global criar_template, template_escrita, servidor
    import criar_template
    import template_escrita
    import servidor


ESTILO_VALIDO = dict(
    nome="X", descricao="d", usa=dict(letreiro=True, formatos=["cheia"], escuro=False, pb=False, flash=False),
    processo=[], modo="broll_primeiro",
    letreiro=dict(estilos=dict(sans=dict(familia="sans", px=96, cor=[255, 255, 255], tracking=-0.04, efeito="sombra")),
                  efeitos=dict(sombra=[[0, 0, 0, 0.45, 26, 0]])),
    legenda=dict(familia="demi", tam=60, pad=30, x=0.5, y=0.7, max_palavras=3, max_caracteres=17, sombras=[[0, 0, 0, 0.5, 10, 0]]),
)


class Args:
    """Um Namespace de mentira — só os atributos que cada cmd_* lê."""
    def __init__(self, **kw):
        self.estilo = self.leia = self.busca = self.exemplo = self.exemplo_fonte = self.amostra = self.fonte = None
        self.__dict__.update(kw)


class CriarTemplateCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas-cli-criar-template-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / "skill" / "estilos").mkdir(parents=True)
        (self.base / "skill" / "fontes").mkdir(parents=True)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(template_escrita.comum, "SKILL", str(self.base / "skill")))
        self.stack.enter_context(patch.object(servidor, "SKILL", str(self.base / "skill")))
        self.stack.enter_context(patch.object(servidor.comum, "RAIZ", str(self.base / "projetos")))

        self.estilo_json = self.base / "estilo.json"
        self.estilo_json.write_text(json.dumps(ESTILO_VALIDO), encoding="utf-8")
        self.leia_md = self.base / "LEIA.md"; self.leia_md.write_text("o que este template é")
        self.busca_md = self.base / "BUSCA.md"; self.busca_md.write_text("que broll buscar")

    def test_criar_grava_rascunho_valido(self):
        args = Args(nome="meu-template", estilo=str(self.estilo_json), leia=str(self.leia_md), busca=str(self.busca_md))
        criar_template.cmd_criar(args)
        gravado = json.load(open(self.base / "skill" / "estilos" / "meu-template" / "estilo.json"))
        self.assertFalse(gravado["publicado"])
        self.assertEqual(gravado["origem"], "claude")
        self.assertTrue((self.base / "skill" / "estilos" / "meu-template" / "LEIA.md").exists())

    def test_criar_propaga_erro_de_validacao_sem_gravar_nada(self):
        ruim = dict(ESTILO_VALIDO); ruim["modo"] = "modo-que-nao-existe"
        (self.base / "estilo-ruim.json").write_text(json.dumps(ruim))
        args = Args(nome="vai-falhar", estilo=str(self.base / "estilo-ruim.json"))
        with self.assertRaises(template_escrita.TemplateInvalido):
            criar_template.cmd_criar(args)
        self.assertFalse((self.base / "skill" / "estilos" / "vai-falhar").exists())

    def test_criar_com_fonte_nova_copia_para_fontes_e_valida(self):
        fonte_origem = self.base / "MinhaFonte.ttf"; fonte_origem.write_bytes(b"fake-font")
        com_fonte = dict(ESTILO_VALIDO, fontes={"sans": {"arquivo": "fontes/MinhaFonte.ttf"}})
        estilo_com_fonte = self.base / "estilo-com-fonte.json"
        estilo_com_fonte.write_text(json.dumps(com_fonte))
        args = Args(nome="com-fonte-nova", estilo=str(estilo_com_fonte), fonte=[str(fonte_origem)])
        criar_template.cmd_criar(args)
        self.assertTrue((self.base / "skill" / "fontes" / "MinhaFonte.ttf").exists())
        gravado = json.load(open(self.base / "skill" / "estilos" / "com-fonte-nova" / "estilo.json"))
        self.assertEqual(gravado["fontes"]["sans"]["arquivo"], "fontes/MinhaFonte.ttf")

    def test_criar_nao_sobrescreve_fonte_existente_com_mesmo_nome(self):
        alvo = self.base / "skill" / "fontes" / "AvenirNext-Bold.ttf"
        alvo.write_bytes(b"fonte-de-verdade-ja-no-servidor")
        fonte_origem = self.base / "AvenirNext-Bold.ttf"; fonte_origem.write_bytes(b"outra-coisa-qualquer")
        args = Args(nome="nao-sobrescreve", estilo=str(self.estilo_json), fonte=[str(fonte_origem)])
        criar_template.cmd_criar(args)
        self.assertEqual(alvo.read_bytes(), b"fonte-de-verdade-ja-no-servidor")  # não mexeu

    def test_publicar_libera_o_rascunho(self):
        args = Args(nome="meu-template", estilo=str(self.estilo_json))
        criar_template.cmd_criar(args)
        criar_template.cmd_publicar(Args(nome="meu-template"))
        gravado = json.load(open(self.base / "skill" / "estilos" / "meu-template" / "estilo.json"))
        self.assertTrue(gravado["publicado"])

    def test_publicar_falha_se_ja_publicado(self):
        args = Args(nome="meu-template", estilo=str(self.estilo_json))
        criar_template.cmd_criar(args)
        criar_template.cmd_publicar(Args(nome="meu-template"))
        with self.assertRaises(ValueError):
            criar_template.cmd_publicar(Args(nome="meu-template"))

    def test_proximo_nome_primeira_vez_e_versoes_seguintes(self):
        import io
        def roda(base):
            saida = io.StringIO()
            with contextlib.redirect_stdout(saida):
                criar_template.cmd_proximo_nome(Args(base=base))
            return saida.getvalue().strip()
        self.assertEqual(roda("base"), "base")
        (self.base / "skill" / "estilos" / "base").mkdir()
        self.assertEqual(roda("base"), "base-v2")


if __name__ == "__main__":
    unittest.main()
