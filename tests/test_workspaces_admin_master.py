"""ADMIN_MASTER vem de ATLAS_ADMIN_MASTER (env), não fica cravado no código — instalação local sem essa
variável não tem admin master nenhum, só a VPS de produção a define. Ver lib/workspaces.py."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def setUpModule():
    global workspaces
    import workspaces


class AdminMasterVemDoAmbiente(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas-admin-master-")
        self.addCleanup(self.tmp.cleanup)
        self.stack_env = patch.dict(os.environ, dict(ESTUDIO_WORKSPACES=str(Path(self.tmp.name) / "workspaces.json")))
        self.stack_env.start()
        self.addCleanup(self.stack_env.stop)
        self.addCleanup(self._recarregar_sem_env)

    def _recarregar_sem_env(self):
        os.environ.pop("ATLAS_ADMIN_MASTER", None)
        importlib.reload(workspaces)

    def test_sem_variavel_nao_existe_admin_master(self):
        os.environ.pop("ATLAS_ADMIN_MASTER", None)
        importlib.reload(workspaces)
        self.assertEqual(workspaces.ADMIN_MASTER, "")
        self.assertFalse(workspaces.pode_acessar("qualquer@example.test", "algum-id"))

    def test_com_variavel_esse_email_sempre_acessa_qualquer_workspace(self):
        with patch.dict(os.environ, dict(ATLAS_ADMIN_MASTER="Dono@Example.com")):
            importlib.reload(workspaces)
            self.assertEqual(workspaces.ADMIN_MASTER, "dono@example.com")
            self.assertTrue(workspaces.pode_acessar("dono@example.com", "workspace-restrito-qualquer"))
            self.assertTrue(workspaces.pode_acessar("DONO@EXAMPLE.COM", "workspace-restrito-qualquer"))


if __name__ == "__main__":
    unittest.main()
