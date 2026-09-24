"""Caption timing/render parity without paid APIs, video models, or user projects."""
import ast
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def functions(path, names, scope):
    """Load pure renderer functions, excluding its command-line startup."""
    source = ast.parse(path.read_text())
    chosen = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(chosen) == len(names), (names, [node.name for node in chosen])
    exec(compile(ast.Module(body=chosen, type_ignores=[]), str(path), "exec"), scope)
    return scope


class KaraokeTests(unittest.TestCase):
    def setUp(self):
        self.style = json.loads((ROOT / "estilos/ultradinamico/estilo.json").read_text())
        self.style["legenda"].update(karaoke=True, caixa_alta=True, minusculas=False, sem_pontuacao=True,
                                     familia="sans", tam=52, max_palavras=4, max_caracteres=26,
                                     destaque=[33, 210, 219], tracking=-.02, pad=18)
        self.plan = {"legenda_pos": {"tam": 52}}
        self.scope = functions(ROOT / "lib/motor.py",
            {"fonte", "texto_mascara", "norm", "montar_legenda", "frases", "texto_legenda",
             "quebra_linhas", "pals_legenda", "palavra_ativa", "camada_legenda_karaoke",
             "camada_legenda", "camada_da_legenda"},
            dict(EST=self.style, P=self.plan, W=1080, H=1920, SKILL=str(ROOT), _fc={},
                 np=np, Image=Image, ImageChops=ImageChops, ImageDraw=ImageDraw, ImageFont=ImageFont,
                 ImageFilter=ImageFilter, re=re, json=json, math=math, os=os, unicodedata=unicodedata))
        self.caption = {"ini": 0, "fim": 1.5, "txt": "UM DOIS TRÊS",
                        "pals": [["UM", 0, .4], ["DOIS", .5, .9], ["TRÊS", 1, 1.4]]}

    def cyan(self, image):
        rgba = np.asarray(image)
        return np.all(rgba[:, :, :3] == [33, 210, 219], axis=2) & (rgba[:, :, 3] > 200)

    def test_uppercase_and_punctuation_preserve_existing_style_behavior(self):
        self.assertEqual(self.scope["texto_legenda"]("Olá, você consegue?"), "OLÁ VOCÊ CONSEGUE")
        self.style["legenda"].pop("caixa_alta")
        self.style["legenda"]["minusculas"] = True
        self.assertEqual(self.scope["texto_legenda"]("Olá, VOCÊ consegue?"), "olá você consegue")
        self.style["legenda"].pop("minusculas")
        self.style["legenda"].pop("sem_pontuacao")
        self.assertEqual(self.scope["texto_legenda"]("Olá, VOCÊ consegue?"), "Olá, VOCÊ consegue?")

    def test_one_spoken_word_is_cyan_with_identical_layout_over_time(self):
        centers, sizes = [], set()
        for t in (.2, .7, 1.2):
            image, baseline = self.scope["camada_da_legenda"](self.caption, t, {})
            mask = self.cyan(image)
            self.assertGreater(mask.sum(), 20)
            centers.append(np.nonzero(mask)[1].mean())
            sizes.add((image.size, baseline))
            rgba = np.asarray(image)
            self.assertGreater((np.all(rgba[:, :, :3] == 255, axis=2) & (rgba[:, :, 3] > 200)).sum(), 20)
        self.assertEqual(centers, sorted(centers))
        self.assertGreater(centers[1] - centers[0], 20)
        self.assertGreater(centers[2] - centers[1], 20)
        self.assertEqual(len(sizes), 1)
        image, _ = self.scope["camada_da_legenda"](self.caption, .46, {})
        self.assertEqual(self.cyan(image).sum(), 0)

    def test_word_boundary_prefers_new_word_over_previous_tolerance(self):
        pals = [["UM", 0, .5], ["DOIS", .5, 1]]
        active = self.scope["palavra_ativa"]
        self.assertEqual(active(pals, .499), 0)
        self.assertEqual(active(pals, .5), 1)
        self.assertEqual(active(pals, 1.02), 1)
        self.assertEqual(active(pals, 1.041), -1)

    def test_manual_text_or_invalid_timing_falls_back_to_current_text(self):
        bad = []
        changed = deepcopy(self.caption); changed["txt"] = "TEXTO NOVO"; bad.append(changed)
        moved = deepcopy(self.caption); moved.update(ini=2, fim=3.5); bad.append(moved)
        trimmed = deepcopy(self.caption); trimmed["fim"] = .8; bad.append(trimmed)
        for value in (float("nan"), float("inf"), True, "0.2"):
            item = deepcopy(self.caption); item["pals"][0][1] = value; bad.append(item)
        malformed = deepcopy(self.caption); malformed["pals"] = [["UM", 0]]; bad.append(malformed)
        for caption in bad:
            with self.subTest(caption=caption):
                self.assertIsNone(self.scope["pals_legenda"](caption))
                actual, baseline = self.scope["camada_da_legenda"](caption, .2, {})
                expected, expected_baseline = self.scope["camada_legenda"](caption["txt"])
                self.assertEqual(actual.tobytes(), expected.tobytes())
                self.assertEqual(baseline, expected_baseline)
                self.assertEqual(self.cyan(actual).sum(), 0)

    def test_moved_timings_remain_valid_and_old_styles_ignore_pals(self):
        moved = deepcopy(self.caption)
        moved.update(ini=2, fim=3.5)
        moved["pals"] = [[w, a + 2, b + 2] for w, a, b in moved["pals"]]
        self.assertEqual(self.scope["pals_legenda"](moved), moved["pals"])
        self.assertEqual(self.scope["palavra_ativa"](moved["pals"], 2.7), 1)
        self.style["legenda"].pop("karaoke")
        actual, _ = self.scope["camada_da_legenda"](self.caption, .2, {})
        expected, _ = self.scope["camada_legenda"](self.caption["txt"])
        self.assertEqual(actual.tobytes(), expected.tobytes())
        self.assertEqual(self.cyan(actual).sum(), 0)

    def test_cache_reuses_active_word_and_does_not_confuse_word_groups(self):
        cache = {}
        first = self.scope["camada_da_legenda"](self.caption, .1, cache)
        self.assertIs(first, self.scope["camada_da_legenda"](self.caption, .2, cache))
        self.assertIsNot(first, self.scope["camada_da_legenda"](self.caption, .7, cache))
        grouped = {**self.caption, "pals": [["UM DOIS", 0, .9], ["TRÊS", 1, 1.4]]}
        different = self.scope["camada_da_legenda"](grouped, .1, cache)
        self.assertIsNot(first, different)
        self.assertGreater(self.cyan(different[0]).sum(), self.cyan(first[0]).sum())

    def test_narrow_caption_wraps_without_moving_between_highlights(self):
        self.style["legenda"]["largura_max"] = .16
        images = [self.scope["camada_da_legenda"](self.caption, t, {})[0] for t in (.2, .7, 1.2)]
        self.assertEqual(len({im.size for im in images}), 1)
        first_y = np.nonzero(self.cyan(images[0]))[0].mean()
        last_y = np.nonzero(self.cyan(images[-1]))[0].mean()
        self.assertGreater(last_y - first_y, 30)

    @unittest.skipUnless(shutil.which("ffprobe"), "FFprobe is required by the actual renderer CLI")
    def test_actual_caption_command_persists_word_timing_only_for_karaoke(self):
        # Nenhum estilo do produto usa karaoke hoje; o mecanismo continua coberto com um
        # estilo temporário só para este teste, sem depender de um estilo específico existir.
        karaoke_style = json.loads((ROOT / "estilos/ultradinamico/estilo.json").read_text())
        karaoke_style["legenda"].update(karaoke=True, caixa_alta=True, sem_pontuacao=True)
        temp_style_dir = ROOT / "estilos" / f"_teste_karaoke_{os.getpid()}"
        temp_style_dir.mkdir()
        (temp_style_dir / "estilo.json").write_text(json.dumps(karaoke_style))
        try:
            with tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                whisper = {"segments": [{"words": [{"word": word, "start": start, "end": end}
                           for word, start, end in [["Olá,", 0, .3], ["você", .3, .6], ["consegue?", .6, 1.0]]]}]}
                (folder / "whisper.json").write_text(json.dumps(whisper))
                for style in (temp_style_dir.name, "ultradinamico"):
                    plan = {"estilo": style, "src": str(folder / "not-needed-for-captions.mp4"),
                            "masks": str(folder / "masks"), "whisper": str(folder / "whisper.json"),
                            "cortes": [], "blocos": [], "dur": 1.1}
                    (folder / "plano.json").write_text(json.dumps(plan))
                    result = subprocess.run([sys.executable, str(ROOT / "lib/motor.py"), str(folder), "legendas"],
                                            capture_output=True, text=True, timeout=20, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
                    self.assertEqual(result.returncode, 0, result.stderr)
                    saved = json.loads((folder / "plano.json").read_text())["legendas"]
                    self.assertTrue(saved)
                    if style == temp_style_dir.name:
                        self.assertEqual(" ".join(lg["txt"] for lg in saved), "OLÁ VOCÊ CONSEGUE")
                        self.assertEqual([p for lg in saved for p in lg["pals"]], [["OLÁ", 0, .3], ["VOCÊ", .3, .6], ["CONSEGUE", .6, 1]])
                    else:
                        self.assertEqual(" ".join(lg["txt"] for lg in saved), "Olá, você consegue?")
                        self.assertTrue(all("pals" not in lg for lg in saved))
        finally:
            shutil.rmtree(temp_style_dir)


class ZoomLimitTests(unittest.TestCase):
    def setUp(self):
        self.zoom = functions(ROOT / "lib/estudio.py", {"zoom_auto"}, {})["zoom_auto"]
        self.plan = {"cortes": [0, 6, 12, 18], "dur": 24, "blocos": []}
        self.style = {"zoom": {"alterna": [1, 1.15, 1, 1.27], "min_movimento": 2.2,
                               "estilos_punch": ["azul"], "punch": .15}}

    def test_no_limits_preserve_existing_targets_and_smoothness(self):
        self.assertEqual(self.zoom(self.plan, self.style),
                         [[5.97, 1.15, "suave"], [11.97, 1, "suave"], [17.97, 1.27, "suave"], [23.97, 1, "suave"]])

    def test_limits_apply_to_base_and_punch_without_changing_timings(self):
        self.plan["blocos"] = [{"linhas": [["azul", "FORÇA", 2.0, None]], "fim": 3}]
        original = self.zoom(self.plan, self.style)
        self.style["zoom"].update(minimo=1, maximo=1.12)
        limited = self.zoom(self.plan, self.style)
        self.assertEqual([row[0] for row in limited], [row[0] for row in original])
        self.assertTrue(all(1 <= row[1] <= 1.12 and row[2] == "suave" for row in limited))
        self.assertTrue(any(row[1] > 1.12 for row in original))


if __name__ == "__main__":
    unittest.main()
