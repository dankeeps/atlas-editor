"""Um workspace ativo nunca pode cair nos caminhos fixos de ESTUDIO_RAIZ/ESTUDIO_BROLLS/ESTUDIO_BIBLIOTECA/
ESTUDIO_ARQUIVOS — essas variáveis existem para o workspace padrão (e para testes que não usam workspace nenhum),
mas a VPS de produção as define permanentemente no processo inteiro (deploy/Dockerfile). Sem essa regra, todo
workspace acabaria lendo e escrevendo na mesma pasta fixa do workspace padrão — B-roll e projeto de um cliente
vazando para outro. Ver comum.workspace_ativo()."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def setUpModule():
    global comum, biblioteca
    import comum
    import biblioteca


class WorkspacePathsIgnoreFixedEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas-workspace-paths-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.fixo = self.base / "fixo-do-ambiente"          # o que ESTUDIO_* aponta (como no Dockerfile)
        self.ws_pasta = self.base / "workspace-cliente"       # a pasta do workspace ativo
        env = dict(ESTUDIO_RAIZ=str(self.fixo / "Edições"), ESTUDIO_BROLLS=str(self.fixo / "B-rolls"),
                   ESTUDIO_BIBLIOTECA=str(self.fixo / "B-rolls" / "biblioteca"), ESTUDIO_ARQUIVOS=str(self.fixo / "arquivos"))
        self.stack_env = patch.dict(os.environ, env)
        self.stack_env.start()
        self.addCleanup(self.stack_env.stop)
        self.workspace = dict(id="w1", pasta=str(self.ws_pasta), brolls=None)

    def test_sem_workspace_ativo_usa_o_caminho_fixo_do_ambiente(self):
        self.assertEqual(comum.raiz(), str(self.fixo / "Edições"))
        self.assertEqual(comum.brolls(), str(self.fixo / "B-rolls"))
        self.assertEqual(biblioteca.brolls(), str(self.fixo / "B-rolls"))
        self.assertEqual(biblioteca.raiz(), str(self.fixo / "B-rolls" / "biblioteca"))

    def test_workspace_ativo_ignora_os_caminhos_fixos_do_ambiente(self):
        tok = comum.definir_workspace(self.workspace)
        try:
            self.assertEqual(comum.raiz(), str(self.ws_pasta))
            self.assertEqual(comum.brolls(), str(self.ws_pasta / ".brolls"))
            self.assertEqual(biblioteca.brolls(), str(self.ws_pasta / ".brolls"))
            self.assertEqual(biblioteca.raiz(), str(self.ws_pasta / ".brolls" / "biblioteca"))
            self.assertNotIn(str(self.fixo), comum.raiz())
            self.assertNotIn(str(self.fixo), biblioteca.brolls())
        finally:
            comum.resetar_workspace(tok)
        # Devolvido ao padrão depois do reset: volta a enxergar o caminho fixo, sem vazar o workspace anterior.
        self.assertEqual(comum.raiz(), str(self.fixo / "Edições"))

    def test_workspace_com_pasta_de_brolls_propria_tambem_e_respeitado(self):
        workspace_com_brolls = dict(id="w2", pasta=str(self.ws_pasta), brolls=str(self.base / "brolls-avulso"))
        tok = comum.definir_workspace(workspace_com_brolls)
        try:
            self.assertEqual(comum.brolls(), str(self.base / "brolls-avulso"))
            self.assertEqual(biblioteca.brolls(), str(self.base / "brolls-avulso"))
            self.assertEqual(biblioteca.raiz(), str(self.base / "brolls-avulso" / "biblioteca"))
        finally:
            comum.resetar_workspace(tok)

    def test_servidor_arquivos_tambem_ignora_o_caminho_fixo_com_workspace_ativo(self):
        sys.path.insert(0, str(ROOT / "app"))
        os.environ.setdefault("EDITOR_IA_ADMIN_EMAIL", "admin@example.test")
        import servidor
        tok = comum.definir_workspace(self.workspace)
        try:
            self.assertEqual(servidor.arquivos(), os.path.join(str(self.ws_pasta), ".arquivos"))
        finally:
            comum.resetar_workspace(tok)
        self.assertEqual(servidor.arquivos(), str(self.fixo / "arquivos"))


if __name__ == "__main__":
    unittest.main()
