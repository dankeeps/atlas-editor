"""Cortes reais com FFmpeg, sem mídia do usuário, modelos ou chamadas de IA."""
from array import array
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import estudio


class CompatibilidadeFFmpegTests(unittest.TestCase):
    def tearDown(self):
        estudio.opcao_grafo_arquivo.cache_clear()

    def test_ffmpeg_debian_usa_opcao_de_arquivo_e_cacheia_deteccao(self):
        estudio.opcao_grafo_arquivo.cache_clear()
        ajuda = "-filter_complex graph  create a complex filtergraph\n-filter_complex_script filename  read complex filtergraph from file\n"
        with patch.object(estudio.subprocess, "run", return_value=SimpleNamespace(stdout=ajuda)) as run:
            self.assertEqual(estudio.opcao_grafo_arquivo(), "-filter_complex_script")
            self.assertEqual(estudio.opcao_grafo_arquivo(), "-filter_complex_script")
            self.assertEqual(run.call_count, 1)

    def test_ffmpeg_recente_usa_sintaxe_de_arquivo_atual(self):
        estudio.opcao_grafo_arquivo.cache_clear()
        with patch.object(estudio.subprocess, "run", return_value=SimpleNamespace(stdout="-filter_complex <graph_description>  create a complex filtergraph\n")):
            self.assertEqual(estudio.opcao_grafo_arquivo(), "-/filter_complex")

    def test_erro_conserva_diagnostico_real_no_final_para_o_kanban(self):
        causa = "Unrecognized option '/filter_complex'.\nError splitting the argument list: Option not found\n"
        erro = subprocess.CalledProcessError(1, ["ffmpeg", "-x"], stderr=causa)
        with patch.object(estudio.subprocess, "run", side_effect=erro):
            with self.assertRaises(RuntimeError) as result:
                estudio.ff("-x")
        self.assertTrue(str(result.exception).endswith(causa.strip()))
        self.assertIn("FFmpeg falhou (código 1)", str(result.exception))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe não disponíveis")
class JumpCutRealTests(unittest.TestCase):
    def run_media(self, *args):
        result = subprocess.run([str(arg) for arg in args], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return result.stdout

    def test_cortes_reordenam_video_audio_e_preservam_mapa_no_frame(self):
        with tempfile.TemporaryDirectory(prefix="atlas-cortes-") as temp:
            root = Path(temp)
            video, audio, version = root / "origem.mov", root / "voz48k.wav", root / "A"
            version.mkdir()
            self.run_media("ffmpeg", "-hide_banner", "-v", "error", "-y",
                "-f", "lavfi", "-i", "color=c=red:s=160x90:r=30:d=1",
                "-f", "lavfi", "-i", "color=c=green:s=160x90:r=30:d=1",
                "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=30:d=1",
                "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
                "-map", "[v]", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", video)
            samples = array("h")
            for i in range(3 * 48000):
                t = i / 48000
                value = round(10000 * math.sin(2 * math.pi * (300 + 300 * int(t)) * t))
                samples.extend((value, value))
            if sys.byteorder != "little":
                samples.byteswap()
            with wave.open(str(audio), "wb") as wav:
                wav.setnchannels(2)
                wav.setsampwidth(2)
                wav.setframerate(48000)
                wav.writeframes(samples.tobytes())

            # O lead do último segundo vem antes do corpo do primeiro segundo.
            # Tempos não alinhados testam o snap de 30 fps usado em produção.
            estudio.jump_cut(str(video), str(audio), [[2.004, 2.597], [.207, .603]], str(version))
            info = json.loads(self.run_media("ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", version / "jc.mov"))
            stream = next(s for s in info["streams"] if s["codec_type"] == "video")
            self.assertEqual((stream["width"], stream["height"]), (160, 90))
            self.assertEqual(stream["r_frame_rate"], "30/1")
            self.assertEqual(int(stream["nb_frames"]), 30)
            self.assertAlmostEqual(float(info["format"]["duration"]), 1, delta=1 / 30)
            for position, dominant in ((.1, 2), (.8, 0)):
                with self.subTest(position=position):
                    pixel = self.run_media("ffmpeg", "-v", "error", "-ss", position,
                        "-i", version / "jc.mov", "-frames:v", "1", "-vf", "scale=1:1",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
                    self.assertEqual(len(pixel), 3)
                    self.assertGreater(pixel[dominant], 220)
                    self.assertTrue(all(pixel[i] < 30 for i in range(3) if i != dominant))

            for filename, rate, channels in (("voz.wav", 48000, 2), ("voz16k.wav", 16000, 1)):
                with self.subTest(filename=filename), wave.open(str(version / filename), "rb") as wav:
                    self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (rate, channels, 2))
                    self.assertAlmostEqual(wav.getnframes() / rate, 1, delta=1 / 30)
                    values = array("h", wav.readframes(wav.getnframes()))
                    if sys.byteorder != "little":
                        values.byteswap()
                    mono = values[::channels]
                    # Conte cruzamentos positivos longe dos fades: primeiro 900 Hz,
                    # depois 300 Hz, comprovando que o áudio acompanha o corte visual.
                    for start, end, frequency in ((.1, .3, 900), (.7, .9, 300)):
                        segment = mono[round(start * rate):round(end * rate)]
                        crossings = sum(a <= 0 < b for a, b in zip(segment, segment[1:]))
                        self.assertAlmostEqual(crossings / (end - start), frequency, delta=10)
            with (version / "mapa.json").open() as file:
                self.assertEqual(json.load(file), {"mapa": [[2.0, 2.6, 0], [.2, .6, .6]], "dur": 1.0})
            self.assertFalse((version / "_jc.txt").exists())


if __name__ == "__main__":
    unittest.main()
