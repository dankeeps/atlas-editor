"""Mixagem: voz do jump cut + efeitos sonoros (regras do estilo + edições do usuário). Sem música.
uso: python3 mix.py <pasta da versão>  -> mix.wav (-14 LUFS) e sfx.txt"""
import sys, os, json, wave, subprocess, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from comum import regras_sfx, caminho_sfx

SR = 48000
def ler(p):
    w = wave.open(p); x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    x = x.reshape(-1, w.getnchannels()); return np.repeat(x, 2, 1) if x.shape[1] == 1 else x
_cache = {}
def som(arquivo, projeto):
    if arquivo not in _cache:
        p = caminho_sfx(arquivo, projeto)
        if not p.endswith(".wav"):
            tmp = f"/tmp/estudio_{os.path.basename(p)}.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", p, "-ac", "2", "-ar", str(SR), tmp], check=True); p = tmp
        _cache[arquivo] = ler(p)
    return _cache[arquivo]

if __name__ == "__main__":
    pasta = os.path.abspath(sys.argv[1]); os.chdir(pasta); projeto = os.path.dirname(os.path.dirname(pasta))
    P = json.load(open("plano.json")); R = regras_sfx(P.get("estilo", "ultradinamico"))
    voz = ler("voz.wav"); voz *= 10 ** (-3 / 20) / np.max(np.abs(voz)); mix = voz.copy(); log = []
    for e in R.eventos(P):
        if e.get("off"): continue
        s = som(e["f"], projeto)[int(e["ini"] * SR):]
        if e["dur"]: s = s[:int(e["dur"] * SR)]
        s = s / (np.max(np.abs(s)) + 1e-9) * 10 ** (e["g"] / 20)
        fo = min(len(s), int(e["fade"] * SR)); s = s.copy(); s[-fo:] *= np.linspace(1, 0, fo)[:, None]
        a = int(e["a"] * SR); b = min(len(mix), a + len(s)); mix[a:b] += s[:b - a]
        log.append(f"{e['t']:7.2f}  {e['f']:28s} {e['g']:+.0f}  {e['chave']}")
    pk = np.max(np.abs(mix)); mix *= min(1, 0.98 / pk)
    w = wave.open("mix_bruto.wav", "w"); w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((np.clip(mix, -1, 1) * 32767).astype(np.int16).tobytes()); w.close()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", "mix_bruto.wav", "-af",
                    "alimiter=limit=0.8:attack=3:release=60:level=disabled,loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "48000", "mix.wav"], check=True)
    os.remove("mix_bruto.wav"); open("sfx.txt", "w").write("\n".join(log)); print(len(log), "efeitos", flush=True)
