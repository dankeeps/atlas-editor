"""Bloqueios externos devem preservar a causa e evitar downloads repetidos."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import youtube


class YouTubeFalhasTests(unittest.TestCase):
    def test_bloqueio_do_provedor_tem_diagnostico_sem_url_ou_cookie(self):
        resposta = SimpleNamespace(returncode=1, stdout="", stderr="ERROR: Sign in to confirm you’re not a bot. Use --cookies secrets.txt https://example.test/?token=privado")
        with patch.object(youtube, "ytdlp", return_value="yt-dlp"), patch.object(youtube.subprocess, "run", return_value=resposta):
            with self.assertRaises(youtube.BloqueioYouTube) as raised:
                youtube._roda(["-J", "https://www.youtube.com/watch?v=fixture"])
        self.assertIn("verificação de acesso", str(raised.exception))
        self.assertNotIn("privado", str(raised.exception))
        self.assertNotIn("secrets.txt", str(raised.exception))

    def test_bloqueio_no_storyboard_nao_tenta_baixar_video_de_novo(self):
        with patch.object(youtube, "_quadros_sb", side_effect=youtube.BloqueioYouTube("bloqueado")), patch.object(youtube, "_quadros_video") as fallback:
            with self.assertRaises(youtube.BloqueioYouTube):
                youtube.quadros("fixture", "/tmp/nao-usado")
            fallback.assert_not_called()

    def test_storyboard_ausente_ainda_permite_fallback_normal(self):
        esperado = ("folha.jpg", [1, 2], 3)
        with patch.object(youtube, "_quadros_sb", return_value=None), patch.object(youtube, "_quadros_video", return_value=esperado) as fallback:
            self.assertEqual(youtube.quadros("fixture", "/tmp/nao-usado"), esperado)
            fallback.assert_called_once()

    def test_erro_de_video_individual_nao_bloqueia_a_fonte(self):
        resposta = SimpleNamespace(returncode=1, stdout="", stderr="ERROR: Video unavailable")
        with patch.object(youtube, "ytdlp", return_value="yt-dlp"), patch.object(youtube.subprocess, "run", return_value=resposta):
            with self.assertRaises(youtube.ErroYouTube) as raised:
                youtube._roda(["-J", "fixture"])
        self.assertNotIsInstance(raised.exception, youtube.BloqueioYouTube)

    def test_baixar_para_grava_de_verdade_mesmo_com_nome_terminando_em_part(self):
        """Regressão: o ffmpeg de conversão escreve num arquivo cujo nome termina em ".part" (nunca ".mp4"
        — de propósito, pra não parecer um resultado pronto no meio do caminho). Sem "-f mp4" explícito no
        comando, o ffmpeg não consegue adivinhar o muxer pela extensão e falha 100% das vezes — não importa
        o vídeo nem a rede. Aqui só o download do yt-dlp é falso (gera um vídeo sintético local); a conversão
        roda com o ffmpeg de verdade, exatamente como em produção."""
        def yt_dlp_falso(args, timeout=600):
            pasta = os.path.dirname(args[args.index("-o") + 1])
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=10",
                               os.path.join(pasta, "t.mp4")], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)  # falha aqui seria o ffmpeg do ambiente, não o bug sob teste
            return ""
        with tempfile.TemporaryDirectory() as tmp:
            destino = os.path.join(tmp, "clipe.mp4")
            with patch.object(youtube, "_roda", side_effect=yt_dlp_falso):
                resultado = youtube.baixar_para("fixture", 1, 3, destino)
            self.assertEqual(resultado, destino)
            self.assertGreater(os.path.getsize(destino), 0)


if __name__ == "__main__":
    unittest.main()
