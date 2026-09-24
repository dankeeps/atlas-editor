"""B-roll do YouTube para a edição automática e para a aba Busca de B-roll (o mesmo caminho que a skill get-brolls faz na
conversa): busca pelo yt-dlp (sem chave, sem custo), folha de contato das capas, quadros do vídeo escolhido com o segundo
de cada um, e o download só do trecho escolhido (até 1080p, sem áudio, H.264).

Os quadros saem das miniaturas que o YouTube já publica (storyboard: ~5 s, sem baixar o vídeo e cobrindo a duração
inteira). Só quando o vídeo não tem storyboard é que baixa uma cópia de 240p — o que leva minutos em vídeo longo.

A origem de cada corte (link, canal, título, segundos) fica em <projeto>/assets/youtube.json e o acervo mostra na
categoria "YouTube". Os direitos do material são de quem publicou: o registro serve para saber de onde veio cada cena.

  python3 youtube.py buscar "<termos>" [--n 12]                                  lista os resultados (teste)
  python3 youtube.py quadros <id> <pasta>                                        folha de quadros com o segundo
  python3 youtube.py baixar <pasta do projeto> <id> <início> <duração> <nome> "<descrição>"
Só usa a biblioteca padrão (roda no Python do sistema e no da .venv); precisa do yt-dlp e do ffmpeg."""
import os, sys, json, time, secrets, shutil, subprocess, threading, urllib.request, tempfile
from concurrent.futures import ThreadPoolExecutor
LIB = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, LIB)

PATH = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")
PY_SISTEMA = sys.executable                # folhas.py usa o PIL, que está no Python do sistema
DUR_MIN, DUR_MAX = 20, 1800                   # nem Shorts nem live de horas
ANALISE_MAX = 1200                            # para escolher o trecho, olha no máximo os primeiros 20 min
_trava = threading.Lock()

class ErroYouTube(RuntimeError): pass

class BloqueioYouTube(ErroYouTube):
    """O provedor exige verificação de acesso; repetir vídeos não resolve."""
    pass

def ytdlp():
    for c in (shutil.which("yt-dlp", path=PATH), os.path.expanduser("~/.claude/skills/get-brolls/.venv/bin/yt-dlp")):
        if c and os.path.exists(c): return c
    return None

def disponivel(): return bool(ytdlp()) and bool(shutil.which("ffmpeg", path=PATH))

def _roda(args, timeout=600):
    r = subprocess.run([ytdlp(), "--no-warnings", "--no-playlist", *args], capture_output=True, text=True, timeout=timeout,
                       env=dict(os.environ, PATH=PATH))
    if r.returncode != 0:
        erro = r.stderr or ""
        if "sign in to confirm" in erro.lower() and "bot" in erro.lower():
            raise BloqueioYouTube("O YouTube exigiu verificação de acesso e bloqueou os downloads. "
                                  "É necessário revisar a conexão/autenticação do YouTube antes de tentar novamente.")
        raise ErroYouTube((erro.strip().splitlines() or ["yt-dlp falhou"])[-1][:300])
    return r.stdout

def buscar(termos, n=12):
    """Resultados da busca (sem baixar nada). Tira Shorts, lives e vídeos curtos ou longos demais."""
    d = json.loads(_roda(["--flat-playlist", "-J", f"ytsearch{int(n * 1.6)}:{termos}"], timeout=90))
    out = []
    for e in d.get("entries") or []:
        dur = e.get("duration") or 0; url = e.get("url") or ""
        if "/shorts/" in url or e.get("live_status") in ("is_live", "is_upcoming") or not (DUR_MIN <= dur <= DUR_MAX): continue
        out.append(dict(n=len(out) + 1, id=e["id"], titulo=(e.get("title") or "").strip(), canal=(e.get("channel") or e.get("uploader") or "").strip(),
                        dur=int(dur), views=e.get("view_count") or 0, url=f"https://www.youtube.com/watch?v={e['id']}"))
        if len(out) >= n: break
    return out

def _baixa_url(url, destino):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as r:
            open(destino, "wb").write(r.read()); return destino
    except Exception: return None

def folha_capas(cands, destino):
    """Capas numeradas (16:9) numa folha de contato; a legenda traz número, duração e canal."""
    pasta = os.path.dirname(destino); os.makedirs(pasta, exist_ok=True)
    def capa(c): return c, _baixa_url(f"https://i.ytimg.com/vi/{c['id']}/mqdefault.jpg", os.path.join(pasta, f"capa_{c['id']}.jpg"))
    with ThreadPoolExecutor(8) as ex: capas = list(ex.map(capa, cands))
    args = [f"{f or 'x'}::{c['n']} · {c['dur'] // 60}:{c['dur'] % 60:02d} · {c['canal'][:18]}" for c, f in capas]
    subprocess.run([PY_SISTEMA, os.path.join(LIB, "folhas.py"), destino, "4", "320", "180", *args], check=True)
    return destino

def _storyboard(vid):
    """O storyboard de melhor resolução (as miniaturas que o YouTube já publica para a barra do player)."""
    d = json.loads(_roda(["-J", f"https://www.youtube.com/watch?v={vid}"], timeout=90))
    sbs = [f for f in (d.get("formats") or []) if str(f.get("format_id", "")).startswith("sb") and f.get("fragments") and f.get("fps")]
    if not sbs: return None
    f = max(sbs, key=lambda x: (x.get("width") or 0))
    return dict(frags=[fr["url"] for fr in f["fragments"] if fr.get("url")], cols=int(f.get("columns") or 1), rows=int(f.get("rows") or 1),
                w=int(f["width"]), h=int(f["height"]), passo=1.0 / float(f["fps"]), dur=float(d.get("duration") or 0))

def _quadros_sb(vid, pasta, n=24):
    """Folha de quadros pelas miniaturas do YouTube: não baixa o vídeo, custa nada e leva segundos (em vez de minutos).
    A miniatura é pequena (320×180): serve para achar a cena, não para julgar texto miúdo — quem julga é a ficha depois."""
    sb = _storyboard(vid)
    if not sb or not sb["frags"] or sb["dur"] <= 0 or sb["passo"] <= 0: return None
    por = max(1, sb["cols"] * sb["rows"]); total = min(len(sb["frags"]) * por, max(1, int(sb["dur"] / sb["passo"])))
    if total < 8: return None
    os.makedirs(pasta, exist_ok=True)
    escolhidos = sorted({min(int(total * (k + 0.5) / n), total - 1) for k in range(n)})
    with ThreadPoolExecutor(6) as ex:
        ms = dict(ex.map(lambda fr: (fr, _baixa_url(sb["frags"][fr], os.path.join(pasta, f"sb_{vid}_{fr}.jpg"))),
                         sorted({i // por for i in escolhidos})))
    imgs, ts = [], []
    for i in escolhidos:
        m = ms.get(i // por)
        if not m: continue
        p = i % por; x, y = (p % sb["cols"]) * sb["w"], (p // sb["cols"]) * sb["h"]
        o = os.path.join(pasta, f"q_{vid}_{i:04d}.jpg")
        subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-i", m, "-vf", f"crop={sb['w']}:{sb['h']}:{x}:{y}", "-frames:v", "1", o],
                       env=dict(os.environ, PATH=PATH))
        if os.path.exists(o): imgs.append(o); ts.append(round((i + 0.5) * sb["passo"], 1))
    if len(imgs) < 8: return None
    folha = os.path.join(pasta, f"quadros_{vid}.jpg")
    subprocess.run([PY_SISTEMA, os.path.join(LIB, "folhas.py"), folha, "6", str(sb["w"]), str(sb["h"]),
                    *[f"{i}::{int(t) // 60}:{int(t) % 60:02d} ({t:.0f}s)" for i, t in zip(imgs, ts)]], check=True)
    return folha, ts, sb["dur"]

def quadros(vid, pasta, n=24, sem_baixar=False):
    """Folha de quadros do vídeo com o segundo de cada um. Tenta as miniaturas publicadas (rápido); se o vídeo não
    tiver storyboard, baixa uma cópia de 240p. sem_baixar=True desiste em vez de baixar. Devolve (folha, [segundos], duração)."""
    try:
        r = _quadros_sb(vid, pasta, n)
        if r: return r
    except BloqueioYouTube:
        raise
    except Exception: pass
    if sem_baixar: raise ErroYouTube("esse vídeo não tem miniaturas publicadas")
    return _quadros_video(vid, pasta, n)

def _quadros_video(vid, pasta, n=24):
    """Baixa uma cópia pequena (≤240p, só vídeo), tira n quadros espalhados e monta a folha com o segundo de cada um.
    Devolve (folha, [segundos], duração analisada)."""
    os.makedirs(pasta, exist_ok=True); base = os.path.join(pasta, f"baixa_{vid}")
    if not any(f.startswith(f"baixa_{vid}.") for f in os.listdir(pasta)):
        _roda(["-q", "-f", "bv*[height<=240][ext=mp4]/bv*[height<=360]/wv*", "--download-sections", f"*0-{ANALISE_MAX}",
               "-o", base + ".%(ext)s", f"https://www.youtube.com/watch?v={vid}"])
    arq = next(os.path.join(pasta, f) for f in os.listdir(pasta) if f.startswith(f"baixa_{vid}."))
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", arq],
                               capture_output=True, text=True, env=dict(os.environ, PATH=PATH)).stdout.strip() or 0)
    if dur <= 0: raise ErroYouTube("a cópia de análise veio vazia")
    ts = [round(dur * (k + 0.5) / n, 1) for k in range(n)]; imgs = []
    def um(t):
        o = os.path.join(pasta, f"q_{vid}_{t:07.1f}.jpg")
        subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-ss", str(t), "-i", arq, "-frames:v", "1", "-vf", "scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2", o],
                       env=dict(os.environ, PATH=PATH))
        return o if os.path.exists(o) else "x"
    with ThreadPoolExecutor(6) as ex: imgs = list(ex.map(um, ts))
    folha = os.path.join(pasta, f"quadros_{vid}.jpg")
    subprocess.run([PY_SISTEMA, os.path.join(LIB, "folhas.py"), folha, "6", "320", "180",
                    *[f"{i}::{int(t) // 60}:{int(t) % 60:02d} ({t:.0f}s)" for i, t in zip(imgs, ts)]], check=True)
    return folha, ts, dur

def fontes(pasta_assets):
    try: return json.load(open(os.path.join(pasta_assets, "youtube.json")))
    except (OSError, ValueError): return {}

def baixar_para(vid, ini, dur, destino):
    """Baixa só o trecho [ini, ini+dur] (até 1080p, sem áudio) em H.264 no caminho dado. Devolve o caminho.
    Converte para um arquivo parcial ao lado de `destino` e só troca pelo nome final no fim (os.replace):
    duas buscas rodando ao mesmo tempo podem mirar no mesmo vídeo+segundo — sem isso, os dois ffmpeg escrevendo
    direto no mesmo destino corromperiam o arquivo um do outro."""
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    parcial = destino + "." + secrets.token_hex(8) + ".part"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            a, b = max(0.0, float(ini)), max(0.0, float(ini)) + max(2.0, float(dur))
            _roda(["-q", "-f", "bv*[height<=1080][vcodec^=avc1]/bv*[height<=1080]", "--download-sections", f"*{a:.2f}-{b:.2f}",
                   "-o", os.path.join(tmp, "t.%(ext)s"), f"https://www.youtube.com/watch?v={vid}"])
            bruto = os.path.join(tmp, next(f for f in os.listdir(tmp) if f.startswith("t.")))
            # -f mp4 é obrigatório aqui: `parcial` termina em ".part", não ".mp4" (de propósito, pra não
            # parecer um render pronto no meio do caminho) — sem dizer o formato explícito, o ffmpeg não
            # consegue adivinhar o muxer pela extensão e recusa TODA vez ("Unable to choose an output format").
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", bruto, "-t", f"{b - a:.2f}", "-an", "-vf", "scale='min(1920,iw)':-2",
                                "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                                "-f", "mp4", parcial],
                               capture_output=True, text=True, env=dict(os.environ, PATH=PATH))
            if r.returncode != 0 or not os.path.getsize(parcial): raise ErroYouTube("não consegui converter o trecho: " + r.stderr[-200:])
        os.replace(parcial, destino)
    finally:
        if os.path.exists(parcial): os.remove(parcial)
    return destino

def baixar(d, vid, ini, dur, nome, descricao, info=None):
    """Baixa só o trecho [ini, ini+dur] (até 1080p, sem áudio), converte para H.264 e registra a origem.
    Devolve o caminho em <projeto>/assets/."""
    assets = os.path.join(d, "assets"); os.makedirs(assets, exist_ok=True)
    with _trava:
        destino = os.path.join(assets, f"yt-{nome}.mp4"); k = 2
        while os.path.exists(destino): destino = os.path.join(assets, f"yt-{nome}-{k}.mp4"); k += 1
        open(destino, "wb").close()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            a, b = max(0.0, float(ini)), max(0.0, float(ini)) + max(2.0, float(dur))
            _roda(["-q", "-f", "bv*[height<=1080][vcodec^=avc1]/bv*[height<=1080]", "--download-sections", f"*{a:.2f}-{b:.2f}",
                   "-o", os.path.join(tmp, "t.%(ext)s"), f"https://www.youtube.com/watch?v={vid}"])
            bruto = os.path.join(tmp, next(f for f in os.listdir(tmp) if f.startswith("t.")))
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", bruto, "-t", f"{b - a:.2f}", "-an", "-vf", "scale='min(1920,iw)':-2",
                                "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", destino],
                               capture_output=True, text=True, env=dict(os.environ, PATH=PATH))
            if r.returncode != 0 or not os.path.getsize(destino): raise ErroYouTube("não consegui converter o trecho: " + r.stderr[-200:])
    except BaseException:
        if os.path.exists(destino) and not os.path.getsize(destino): os.remove(destino)
        raise
    info = info or {}
    with _trava:
        F = fontes(assets)
        F[os.path.basename(destino)] = dict(url=f"https://www.youtube.com/watch?v={vid}", titulo=info.get("titulo", ""), canal=info.get("canal", ""),
                                            ini=round(a, 2), fim=round(b, 2), descricao=descricao.strip(), quando=time.strftime("%Y-%m-%dT%H:%M:%S"))
        tmp = os.path.join(assets, "youtube.json.tmp"); json.dump(F, open(tmp, "w"), ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(assets, "youtube.json"))
    return destino

if __name__ == "__main__":
    a = sys.argv[1:]
    if not disponivel(): print("yt-dlp ou ffmpeg não encontrado"); sys.exit(1)
    if a[:1] == ["buscar"]:
        n = int(a[a.index("--n") + 1]) if "--n" in a else 12
        for c in buscar(a[1], n): print(f"{c['n']:2d} {c['id']} {c['dur']:5d}s {c['views']:>10} {c['canal'][:22]:22s} {c['titulo'][:70]}")
    elif a[:1] == ["quadros"]:
        f, ts, dur = quadros(a[1], a[2]); print(f, f"({dur:.0f}s analisados)")
    elif a[:1] == ["baixar"]:
        print(baixar(os.path.abspath(a[1]), a[2], float(a[3]), float(a[4]), a[5], a[6]))
    else:
        print(__doc__)
