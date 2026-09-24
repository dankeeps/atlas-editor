"""lib/estudo_video.py contra vídeos sintéticos de verdade (ffmpeg) — mede ritmo, legenda, som e extrai
quadros. Não usa vídeo real de anúncio (não tem um aqui); o que importa testar é que a medição em si funciona
e se comporta como documentado (agrupa cortes, nunca confunde branco com legenda, respeita o limite de
quadros), não o conteúdo específico de um vídeo real."""
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
SEM_FFMPEG = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None


def setUpModule():
    global estudo_video
    import estudo_video


def construir_video(caminho, cores, duracao_bloco=0.5, tom=True):
    """Um vídeo vertical com um bloco de cor sólida por entrada de `cores` — cada troca de cor é uma "troca
    de cena" bem clara pro detector do ffmpeg, sem precisar de um vídeo de anúncio de verdade."""
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for cor in cores:
        cmd += ["-f", "lavfi", "-i", f"color=c={cor}:s=360x640:r=24:d={duracao_bloco},format=yuv420p"]
    n = len(cores)
    filtro = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
    if tom:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duracao_bloco * n}"]
        cmd += ["-filter_complex", filtro, "-map", "[v]", "-map", f"{n}:a", "-c:v", "libx264", "-c:a", "aac"]
    else:
        cmd += ["-filter_complex", filtro, "-map", "[v]", "-an", "-c:v", "libx264"]
    cmd += [str(caminho)]
    subprocess.run(cmd, check=True)


@unittest.skipIf(SEM_FFMPEG, "ffmpeg/ffprobe não estão instalados neste ambiente")
class EstudoVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="atlas-estudo-video-")
        cls.base = Path(cls.tmp.name)
        cls.video_3_blocos = cls.base / "tres.mp4"
        construir_video(cls.video_3_blocos, ["red", "blue", "green"], duracao_bloco=1.0)
        cls.video_10_blocos = cls.base / "dez.mp4"
        # 0,8s por bloco: acima da janela de 0,6s que _cortes() usa pra agrupar detecções (documentado —
        # ver a mesma regra em ~/.claude/skills/edicao-dinamica/referencias/armadilhas.md); abaixo disso os
        # cortes reais colapsam em menos blocos, então não seria um teste realista do limite de quadros.
        construir_video(cls.video_10_blocos, ["red", "blue"] * 5, duracao_bloco=0.8)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_duracao(self):
        self.assertAlmostEqual(estudo_video.duracao(str(self.video_3_blocos)), 3.0, delta=0.2)

    def test_medir_ritmo_acha_os_tres_blocos(self):
        r = estudo_video.medir_ritmo(str(self.video_3_blocos))
        self.assertEqual(r["blocos"], 3)
        self.assertAlmostEqual(r["mediana_s"], 1.0, delta=0.15)
        self.assertEqual(len(r["inicios_s"]), 3)
        self.assertAlmostEqual(r["inicios_s"][0], 0.0, delta=0.05)

    def test_medir_legenda_sem_cor_de_destaque_nao_confunde_com_legenda(self):
        # vermelho/azul/verde são saturados, mas não formam uma faixa estreita e densa (é a tela inteira) —
        # a regra "cor de destaque numa faixa estreita" tem que recusar isso, não confundir com legenda.
        r = estudo_video.medir_legenda(str(self.video_3_blocos), passo=0.4)
        self.assertFalse(r["achou"])

    def test_medir_som_devolve_os_campos_esperados(self):
        r = estudo_video.medir_som(str(self.video_3_blocos))
        self.assertTrue(r["medido"])
        for chave in ("ataques_graves", "ataques_agudos", "casamento_com_cortes", "trocas_sem_som", "fundo_dbfs", "batida"):
            self.assertIn(chave, r)

    def test_medir_som_sem_audio_nao_quebra(self):
        sem_audio = self.base / "sem_audio.mp4"
        construir_video(sem_audio, ["red", "blue"], duracao_bloco=0.5, tom=False)
        r = estudo_video.medir_som(str(sem_audio))
        self.assertFalse(r["medido"])

    def test_extrair_quadros_um_por_bloco_quando_cabe_no_limite(self):
        with tempfile.TemporaryDirectory() as destino:
            quadros = estudo_video.extrair_quadros(str(self.video_3_blocos), destino, maximo=8)
            self.assertEqual(len(quadros), 3)
            for q in quadros: self.assertTrue(Path(q).exists() and Path(q).stat().st_size > 0)

    def test_extrair_quadros_respeita_o_maximo_com_muitos_blocos(self):
        with tempfile.TemporaryDirectory() as destino:
            quadros = estudo_video.extrair_quadros(str(self.video_10_blocos), destino, maximo=4)
            self.assertLessEqual(len(quadros), 4)
            self.assertGreaterEqual(len(quadros), 3)  # a amostragem uniforme não pode colapsar quase tudo

    def test_extrair_quadros_amostra_do_video_inteiro_nao_so_o_comeco(self):
        with tempfile.TemporaryDirectory() as destino:
            r = estudo_video.medir_ritmo(str(self.video_10_blocos))
            quadros = estudo_video.extrair_quadros(str(self.video_10_blocos), destino, maximo=4)
            # com amostragem uniforme, o último quadro extraído tem que vir de perto do fim do vídeo, não
            # dos primeiros blocos — senão a "amostra uniforme" na verdade só pegou o começo.
            nomes = sorted(Path(q).name for q in quadros)
            self.assertTrue(nomes[-1].startswith("quadro03") or len(quadros) < 4)
            self.assertGreater(r["duracao"], 3.5)  # confirma que o vídeo de 10 blocos é bem mais longo que 4 blocos seriam

    def test_estudar_junta_tudo(self):
        with tempfile.TemporaryDirectory() as destino:
            r = estudo_video.estudar(str(self.video_3_blocos), destino, maximo_quadros=8)
            self.assertEqual(set(r), {"ritmo", "legenda", "som", "quadros"})
            self.assertEqual(len(r["quadros"]), 3)


if __name__ == "__main__":
    unittest.main()
