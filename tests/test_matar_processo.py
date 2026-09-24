"""app/servidor.py:matar_processo — no POSIX é os.killpg (mata o grupo inteiro criado por
start_new_session=True); no Windows esse start_new_session não cria grupo nenhum (o subprocess
da stdlib ignora o parâmetro ali), então a única forma de matar a árvore é taskkill /T. Sem
Windows de verdade para testar, o que dá para garantir aqui é que cada plataforma chama a API
certa — não o comportamento do sistema operacional em si."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "app"))


def setUpModule():
    global servidor
    import servidor


class MatarProcessoTests(unittest.TestCase):
    def test_posix_usa_killpg_no_grupo_do_processo(self):
        with patch.object(servidor.os, "name", "posix"), \
             patch.object(servidor.os, "getpgid", return_value=999) as m_getpgid, \
             patch.object(servidor.os, "killpg") as m_killpg:
            servidor.matar_processo(123)
        m_getpgid.assert_called_once_with(123)
        m_killpg.assert_called_once_with(999, servidor.signal.SIGTERM)

    def test_windows_usa_taskkill_na_arvore_do_processo(self):
        with patch.object(servidor.os, "name", "nt"), \
             patch.object(servidor.subprocess, "run") as m_run:
            servidor.matar_processo(123)
        m_run.assert_called_once_with(["taskkill", "/F", "/T", "/PID", "123"], capture_output=True)


if __name__ == "__main__":
    unittest.main()
