"""Guardas da revisão de cortes: sem mídias do usuário ou provedores externos."""
import ast
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import contextvars
from types import SimpleNamespace
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
NAMES = {"hash_cortes", "segmentos_revisados", "cortes_ocupados", "salvar_cortes", "revisar_card", "nova_thread"}


class CortesRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="atlas-revision-")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        (self.project / "fonte").mkdir(parents=True)
        (self.project / "versoes/A").mkdir(parents=True)
        self.path = self.project / "fonte/cortes.json"
        self.path.write_text(json.dumps({"versoes": [{"id": "A", "nome": "A", "faixas": [[0, 10]], "segs": [[0, 10]]}]}))
        with wave.open(str(self.project / "fonte/voz16k.wav"), "wb") as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000); audio.writeframes(bytes(320000))
        self.Q = {"cards": {"C004": dict(id="C004", projeto="project", coluna="cortes", estado="ok", revisado=True)}}
        self.targets = []
        test = self

        class PendingThread:
            def __init__(self, target, daemon):
                self.target = target
            def start(self):
                test.targets.append(self.target)

        @contextmanager
        def mexer():
            yield self.Q

        def pasta_proj(slug):
            if slug != "project": raise ValueError("projeto inválido")
            return str(self.project)

        self.commands = []
        def run(command, **options):
            self.commands.append(command)
            for name in ("jc.mov", "voz.wav", "voz16k.wav", "mapa.json", "whisper.json"):
                (self.project / "versoes/A" / name).write_bytes(b"output")
            return SimpleNamespace(returncode=0)

        self.scope = dict(os=os, json=json, math=math, wave=wave, hashlib=hashlib, re=re, secrets=secrets, contextvars=contextvars,
            threading=SimpleNamespace(Thread=PendingThread), subprocess=SimpleNamespace(run=run, CalledProcessError=subprocess.CalledProcessError),
            CORTES_LOCK=threading.RLock(), JOBS={}, kanban=SimpleNamespace(mexer=mexer, agora=lambda: "2026-09-21"),
            pasta_proj=pasta_proj, estado_auto=lambda p: dict(rodando=False), PY="python", LIB="/app/lib")
        tree = ast.parse((ROOT / "app/servidor.py").read_text())
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in NAMES]
        self.assertEqual(len(functions), len(NAMES))
        exec(compile(ast.Module(body=functions, type_ignores=[]), "<server-functions>", "exec"), self.scope)

    def request(self, **changes):
        value = dict(p="project", v="A", card="C004", segs=[[0, 2], [4, 10]],
                     revisao=self.scope["hash_cortes"](str(self.path)), revisado=False)
        value.update(changes)
        return value

    def save(self, **changes):
        return self.scope["salvar_cortes"](self.request(**changes))

    def confirm(self, revision=None):
        return self.scope["revisar_card"](dict(id="C004", valor=True, p="project", v="A",
            revisao=revision or self.scope["hash_cortes"](str(self.path))))

    def test_busy_project_cannot_change_file_or_review_state(self):
        before = self.path.read_bytes()
        self.scope["JOBS"][("cortes", "project", "A")] = dict(rodando=True)
        with self.assertRaisesRegex(ValueError, "processado"):
            self.save()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(self.Q["cards"]["C004"]["revisado"])
        self.assertEqual(self.targets, [])

    def test_invalid_ranges_version_card_and_stale_revision_do_not_write(self):
        bad = [dict(segs=x) for x in ([], [[float("nan"), 1]], [[0, float("inf")]], [[False, 1]],
               [[-1, 2]], [[0, 11]], [[2, 3], [1, 2]], [[0, 3], [2, 4]], [["0", 2]], [[0, .01]])]
        bad += [dict(v="B"), dict(card="missing"), dict(card=None), dict(revisao="stale"), dict(revisado=True)]
        for change in bad:
            with self.subTest(change=change):
                before = self.path.read_bytes()
                with self.assertRaises(ValueError): self.save(**change)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertTrue(self.Q["cards"]["C004"]["revisado"])
                self.assertEqual(self.targets, [])

    def test_first_save_reserves_job_and_second_save_cannot_overwrite(self):
        result = self.save()
        before = self.path.read_bytes()
        self.assertEqual(self.Q["cards"]["C004"]["estado"], "rodando")
        self.assertFalse(self.Q["cards"]["C004"]["revisado"])
        with self.assertRaises(ValueError): self.save(segs=[[0, 1]])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.targets), 1)
        self.assertEqual(self.scope["JOBS"][("cortes", "project", "A")]["revisao"], result["revisao"])

    def test_review_requires_completed_current_outputs(self):
        result = self.save()
        with self.assertRaisesRegex(ValueError, "espere"): self.confirm(result["revisao"])
        self.targets.pop()()
        self.assertEqual(self.Q["cards"]["C004"]["estado"], "ok")
        self.assertFalse(self.Q["cards"]["C004"]["revisado"])
        with self.assertRaisesRegex(ValueError, "mudaram"): self.confirm("stale")
        self.assertTrue(self.confirm(result["revisao"])["ok"])
        self.assertTrue(self.Q["cards"]["C004"]["revisado"])
        self.assertEqual(self.commands[0], ["nice", "-n", "10", "python", "/app/lib/estudio.py", "cortes", "project",
                                          "--aplicar", "--versoes", "A", "--manter-copia"])

    def test_failed_render_never_sets_reviewed_and_keeps_real_error(self):
        self.save()
        def failed(*args, **kwargs):
            raise subprocess.CalledProcessError(1, args[0], output="render failed", stderr="FFmpeg: invalid graph")
        self.scope["subprocess"].run = failed
        self.targets.pop()()
        card = self.Q["cards"]["C004"]
        self.assertEqual(card["estado"], "erro")
        self.assertFalse(card["revisado"])
        self.assertIn("invalid graph", card["msg"])
        with self.assertRaises(ValueError): self.confirm()

    def test_missing_output_cannot_receive_review_seal(self):
        self.save()
        self.targets.pop()()
        (self.project / "versoes/A/whisper.json").unlink()
        with self.assertRaisesRegex(ValueError, "arquivos"): self.confirm()

    def test_card_from_other_project_and_queue_are_rejected(self):
        for changed in (dict(projeto="other"), dict(estado="espera"), dict(estado="rodando"), dict(coluna="edicao")):
            card = self.Q["cards"]["C004"].copy()
            self.Q["cards"]["C004"].update(changed)
            before = self.path.read_bytes()
            with self.assertRaises(ValueError): self.save()
            self.assertEqual(self.path.read_bytes(), before)
            self.Q["cards"]["C004"] = card

    @unittest.skipUnless(shutil.which("node"), "Node indisponível")
    def test_drag_replaces_snapshot_and_review_waits_for_success(self):
        js = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert");
const html=fs.readFileSync(process.argv[1],"utf8"),script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script);
const handlers={},elements={};
const element=id=>elements[id]||(elements[id]={disabled:false,textContent:"",on:false,
  clientWidth:10,getBoundingClientRect:()=>({left:0}),classList:{add(){elements[id].on=true},remove(){elements[id].on=false}}});
const ctx=vm.createContext({console,JSON,Math,Set,addEventListener:(name,fn)=>handlers[name]=fn,
  setTimeout:()=>{},fetch:()=>{},$ :element});
vm.runInContext('let D={dur:10,revisao:"new"},SEGS=[[0,2],[6,10]],arrasto=null,sel=-1,salvando=false;'+
  'const ESPIANDO=new Set(["2.00:6.00"]),chave=([a,b])=>a.toFixed(2)+":"+b.toFixed(2);'+
  'const P="project",V="A",CARD="C004";function desenha(){}function avisa(){}function registra(){}function tempoDe(x){return x}',ctx);
function block(a,b){return script.slice(script.indexOf(a),script.indexOf(b,script.indexOf(a)))}
vm.runInContext(block("function removidos()","function desenha()")+
  block('addEventListener("pointermove"',"function devolve(")+
  block("function refaz(","// ---------- prévia"),ctx);
vm.runInContext('arrasto={orig:[2,6],modo:"mover",t0:2,base:[[2,6]],segs:[[0,2],[6,10]],esp:["2.00:6.00"]}',ctx);
handlers.pointermove({clientX:3});
handlers.pointermove({clientX:4});
handlers.pointermove({clientX:10});
assert.deepStrictEqual(JSON.parse(vm.runInContext("JSON.stringify(SEGS)",ctx)),[[0,6]]);
handlers.pointermove({clientX:2.5});
assert.deepStrictEqual(JSON.parse(vm.runInContext("JSON.stringify(SEGS)",ctx)),[[0,2.5],[6.5,10]]);
assert.deepStrictEqual(JSON.parse(vm.runInContext("JSON.stringify([...ESPIANDO])",ctx)),["2.50:6.50"]);
handlers.pointercancel();
assert.deepStrictEqual(JSON.parse(vm.runInContext("JSON.stringify(SEGS)",ctx)),[[0,2],[6,10]]);
vm.runInContext(block("// ---------- salvar","document.onkeydown="),ctx);
(async()=>{
  ctx.fetch=async()=>({ok:true,json:async()=>({revisao:"new",job:{rodando:true,revisao:"new"}})});
  await vm.runInContext("vigia()",ctx);
  assert(!element("#mrev").on);
  ctx.fetch=async()=>({ok:true,json:async()=>({revisao:"new",segs:[[0,2],[6,10]],job:{rodando:false,revisao:"new",erro:"failed"}})});
  await vm.runInContext("vigia()",ctx);
  assert(!element("#mrev").on);
  ctx.fetch=async()=>({ok:true,json:async()=>({revisao:"new",segs:[[0,2],[6,10]],job:{rodando:false,revisao:"new",erro:null}})});
  await vm.runInContext("vigia()",ctx);
  assert(element("#mrev").on);
  console.log("drag and asynchronous review OK");
})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(["node", "-e", js, str(ROOT / "app/cortes.html")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
