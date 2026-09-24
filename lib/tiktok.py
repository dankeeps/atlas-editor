"""Busca de B-roll no TikTok (Apify, ator clockworks, EUA, vertical) que alimenta a biblioteca: TODOS os candidatos que
passam no filtro são baixados para ~/B-rolls/biblioteca/ e estudados pelo Gemini (descrição, tags, trechos limpos). O que
não for usado agora fica disponível para os próximos vídeos. Também gera a folha de contato numerada (para olhar no chat).

  python3 tiktok.py buscar "<projeto>" "<nome da busca>" "termo em inglês" ["termo" ...] [--n 15] [--dur 3-45] [--regiao US] [--sem-estudo]
  python3 tiktok.py quadros "<projeto>" <busca> 3,7,11     -> busca/<busca>/quadros.jpg (5 quadros de cada, com o segundo)
Depois, para usar no projeto: estudio.py acervo "<projeto>" --biblioteca B0012 B0015
Token da Apify: ⚙ Configurações, APIFY_API_TOKEN do ambiente ou do ~/.zshrc. Cobrança por resultado/download conforme o ator.
Roda no Python do sistema (/usr/bin/python3, que tem o PIL)."""
import os, re, sys, json, time, subprocess, tempfile, unicodedata, urllib.request, urllib.error, urllib.parse, datetime
from concurrent.futures import ThreadPoolExecutor
LIB = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, LIB)
import comum, custos, biblioteca

ATOR = "clockworks~tiktok-scraper"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
PATH = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")
PY_SISTEMA = sys.executable

class ErroBusca(RuntimeError): pass
class ErroDownload(ErroBusca): pass

def slug(s, n=40):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:n] or "broll"

def humano(n):
    n = n or 0
    for c, s in ((1_000_000, "M"), (1_000, "k")):
        if n >= c: return f"{n / c:.1f}".rstrip("0").rstrip(".") + s
    return str(int(n))

def api(caminho, dados=None, timeout=60):
    tk = custos.token_apify()
    if not tk: raise ErroBusca("sem token da Apify (⚙ Configurações)")
    req = urllib.request.Request("https://api.apify.com/v2/" + caminho, headers={"Authorization": "Bearer " + tk, "Content-Type": "application/json"},
                                 data=json.dumps(dados).encode() if dados is not None else None)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

def rodar_ator(entrada, espera=900):
    run = api(f"acts/{ATOR}/runs", entrada)["data"]; t0 = time.time()
    while True:
        d = api(f"actor-runs/{run['id']}")["data"]
        if d["status"] not in ("READY", "RUNNING"): break
        if time.time() - t0 > espera: raise ErroBusca(f"a busca passou de {espera}s na Apify (run {run['id']})")
        time.sleep(5)
    if d["status"] != "SUCCEEDED": raise ErroBusca(f"a Apify terminou em {d['status']} (https://console.apify.com/actors/runs/{d['id']})")
    return api(f"datasets/{d['defaultDatasetId']}/items?clean=true", timeout=180), d

def _erro_download(ex):
    """Nunca devolver URLs assinadas, cabeçalhos ou stderr de provedores ao log."""
    if isinstance(ex, ErroDownload): return str(ex)
    if isinstance(ex, urllib.error.HTTPError): return f"servidor respondeu HTTP {ex.code}"
    if isinstance(ex, (TimeoutError, subprocess.TimeoutExpired)): return "o download excedeu o tempo permitido"
    if isinstance(ex, urllib.error.URLError): return "não foi possível conectar ao servidor de mídia"
    if isinstance(ex, OSError): return f"falha de arquivo/rede (código {ex.errno or 'indisponível'})"
    return "não foi possível concluir o download"

def urls_video(it):
    """mediaUrls contém os vídeos salvos pelo ator; downloadAddr é o link direto, quando disponível."""
    urls = list(it.get("mediaUrls") or [])
    urls.append((it.get("videoMeta") or {}).get("downloadAddr"))
    saida = []
    for url in urls:
        if not isinstance(url, str): continue
        partes = urllib.parse.urlsplit(url)
        if partes.scheme not in ("https", "http") or not partes.hostname: continue
        if partes.hostname == "api.apify.com":
            # O token configurado segue só no cabeçalho, nunca no busca.json.
            query = [(k, v) for k, v in urllib.parse.parse_qsl(partes.query, keep_blank_values=True) if k.lower() != "token"]
            url = urllib.parse.urlunsplit(partes._replace(query=urllib.parse.urlencode(query)))
        if url not in saida: saida.append(url)
    return saida

def video_valido(arquivo):
    if not os.path.isfile(arquivo): return False
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height:format=duration", "-of", "json", arquivo],
                           capture_output=True, text=True, timeout=30, env=dict(os.environ, PATH=PATH))
        if r.returncode: return False
        dados = json.loads(r.stdout); video = (dados.get("streams") or [{}])[0]
        return float((dados.get("format") or {}).get("duration") or 0) > 0 and video.get("width", 0) > 0 and video.get("height", 0) > 0
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired): return False

def pegar(url, destino, tentativas=2, validar=None, estrito=False):
    erro = "nenhum arquivo recebido"
    for _ in range(tentativas):
        temporario = None
        try:
            cab = {"User-Agent": UA, "Referer": "https://www.tiktok.com/"}
            req = urllib.request.Request(url, headers=cab)
            partes = urllib.parse.urlsplit(url)
            if partes.scheme == "https" and partes.hostname == "api.apify.com" and partes.port in (None, 443):
                # urllib não encaminha unredirected_headers ao CDN durante um redirect.
                req.add_unredirected_header("Authorization", "Bearer " + custos.token_apify())
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            with urllib.request.urlopen(req, timeout=90) as resposta, tempfile.NamedTemporaryFile(
                    dir=os.path.dirname(destino), prefix=".download-", suffix=os.path.splitext(destino)[1], delete=False) as arq:
                temporario = arq.name; total = 0
                while True:
                    trecho = resposta.read(1024 * 1024)
                    if not trecho: break
                    arq.write(trecho); total += len(trecho)
                esperado = resposta.headers.get("Content-Length")
                if esperado and total != int(esperado): raise ErroDownload("o servidor enviou um arquivo incompleto")
            if not total or (validar and not validar(temporario)): raise ErroDownload("o arquivo recebido não contém vídeo válido")
            os.replace(temporario, destino); temporario = None
            return True
        except Exception as ex: erro = _erro_download(ex)
        finally:
            if temporario and os.path.exists(temporario): os.remove(temporario)
    if estrito: raise ErroDownload(erro)
    return False

def baixar_video(c):
    """O vídeo inteiro em ~/B-rolls/biblioteca/videos/<tiktok_id>.mp4 (link da Apify quando houver, senão yt-dlp)."""
    if not re.fullmatch(r"[0-9]{1,32}", str(c.get("tiktok_id") or "")): raise ErroDownload("identificador de vídeo TikTok inválido")
    alvo = os.path.join(biblioteca.pasta("videos"), f"{c['tiktok_id']}.mp4")
    if video_valido(alvo): return alvo
    erros = []
    for u in c.get("downloads", []):
        try:
            if pegar(u, alvo, 1, validar=video_valido, estrito=True): return alvo
        except ErroDownload as ex: erros.append(str(ex))
    if not c.get("downloads"): erros.append("a Apify não forneceu arquivo de vídeo")
    # O fallback tem prazo e pasta própria: falhas nunca viram um .mp4 parcial na biblioteca.
    try:
        with tempfile.TemporaryDirectory(dir=os.path.dirname(alvo), prefix=".tiktok-") as tmp:
            saida = os.path.join(tmp, "video.mp4")
            r = subprocess.run(["yt-dlp", "-q", "--no-warnings", "--no-playlist", "--socket-timeout", "30", "--retries", "1",
                                "-S", "res,ext:mp4:m4a", "--merge-output-format", "mp4", "-o", saida, c["url"]],
                               capture_output=True, text=True, timeout=180, env=dict(os.environ, PATH=PATH))
            if r.returncode == 0 and video_valido(saida):
                os.replace(saida, alvo); return alvo
            bloqueado = re.search(r"captcha|sign in|log.?in|not a bot|verify", r.stderr or "", re.I)
            erros.append("TikTok exige verificação/login para baixar o vídeo" if bloqueado else "yt-dlp não conseguiu baixar um vídeo válido")
    except Exception as ex: erros.append(_erro_download(ex))
    raise ErroDownload("; ".join(dict.fromkeys(erros)))

# ---------------------------------------------------------------- buscar
def buscar(d, nome, termos, n=15, dur=(3, 45), regiao="US", estudar=True, log=print, ao_cobrar_estudo=None, nicho=None, campos=None):
    """Busca, guarda tudo na biblioteca e estuda cada clipe novo uma vez (Claude ou Gemini, ⚙). Devolve dict(busca, run, ids, novos, folhas).
    d = pasta do projeto (lê o nicho do projeto.json) ou de uma leva da aba Busca de B-roll (nicho e campos vêm por parâmetro)."""
    if nicho is None:
        pj = os.path.join(d, "projeto.json"); P = json.load(open(pj)) if os.path.exists(pj) else {}
        nicho = P.get("nicho") or P.get("oferta") or ""
    # Sem este download o ator retorna apenas metadados; yt-dlp na VPS pode ser bloqueado pelo TikTok.
    entrada = dict(searchQueries=list(termos), searchSection="/video", resultsPerPage=n, videoSearchSorting="MOST_RELEVANT",
                   videoSearchDateFilter="ALL_TIME", proxyCountryCode=regiao, shouldDownloadVideos=True, shouldDownloadCovers=True,
                   downloadSubtitlesOptions="NEVER_DOWNLOAD_SUBTITLES", shouldDownloadSlideshowImages=False, shouldDownloadAvatars=False,
                   shouldDownloadMusicCovers=False, scrapeRelatedVideos=False)
    log(f"TikTok: buscando '{nome}' ({', '.join(termos)})…")
    itens, run = rodar_ator(entrada)
    try: custos.registrar_apify(d, run["id"], f"busca no TikTok: {nome}")
    except Exception as e: log(f"(não consegui ler o custo da busca na Apify: {str(e)[:100]})")
    B = biblioteca.ler()["itens"]; na_bib = {x.get("tiktok_id"): i for i, x in B.items() if x.get("tiktok_id")}
    vistos, cands = set(), []; fora = dict(curto=0, longo=0, horizontal=0, anuncio=0, slideshow=0)
    for it in itens:
        v = it.get("videoMeta") or {}; a = it.get("authorMeta") or {}; tid = str(it.get("id") or "")
        if not tid or tid in vistos or not it.get("webVideoUrl"): continue
        vistos.add(tid); du = v.get("duration") or 0; w, h = v.get("width") or 0, v.get("height") or 0
        if it.get("isSlideshow"): fora["slideshow"] += 1; continue
        if it.get("isAd") or it.get("isSponsored"): fora["anuncio"] += 1; continue
        if du and du < dur[0]: fora["curto"] += 1; continue
        if du and du > dur[1]: fora["longo"] += 1; continue
        if w and h and h <= w: fora["horizontal"] += 1; continue
        cands.append(dict(tiktok_id=tid, url=it["webVideoUrl"], titulo=(it.get("text") or "").strip(), autor="@" + str(a.get("name") or "?"),
                          views=it.get("playCount") or 0, duracao=du, termo=it.get("searchQuery") or termos[0],
                          capa=v.get("coverUrl") or v.get("originalCoverUrl") or "",
                          downloads=urls_video(it)))
    if not cands: raise ErroBusca(f"nenhum candidato sobrou de {len(itens)} resultados (fora: {fora})")
    cands.sort(key=lambda c: -(c["views"] or 0))
    busca = f"{slug(nome, 30)}-{datetime.datetime.now():%H%M%S}"; p = os.path.join(d, "busca", busca); os.makedirs(os.path.join(p, "capas"), exist_ok=True)
    for i, c in enumerate(cands, 1): c["n"] = i
    novos = [c for c in cands if c["tiktok_id"] not in na_bib]
    log(f"TikTok · {nome}: {len(itens)} vieram, {len(cands)} servem ({len(cands) - len(novos)} já estavam na biblioteca); baixando {len(novos)}…")
    def guarda(c):
        try: arq = baixar_video(c)
        except ErroDownload as ex:
            c["erro_download"] = str(ex)
            return None
        return biblioteca.adicionar(arquivo=arq, fonte="tiktok", tiktok_id=c["tiktok_id"], url=c["url"], autor=c["autor"], views=c["views"],
                                    termo=c["termo"], busca=busca, nicho=nicho, titulo=re.sub(r"#\S+", "", c["titulo"]).strip()[:200], **(campos or {}))
    with ThreadPoolExecutor(6) as ex: ids_novos = [i for i in ex.map(guarda, novos) if i]
    todos = {x.get("tiktok_id"): i for i, x in biblioteca.ler()["itens"].items() if x.get("tiktok_id") and os.path.isfile(x.get("arquivo") or "")}
    for c in cands: c["id"] = todos.get(c["tiktok_id"])
    falhas = sum(bool(c.get("erro_download")) for c in cands)
    with open(os.path.join(p, "busca.json"), "w") as arq:
        json.dump(dict(id=busca, nome=nome, termos=list(termos), run=run["id"], quando=datetime.datetime.now().isoformat(timespec="seconds"),
                       candidatos=cands, falhas_download=falhas), arq, ensure_ascii=False, indent=1)
    if not any(c.get("id") for c in cands):
        motivo = next((c["erro_download"] for c in cands if c.get("erro_download")), "nenhum arquivo foi salvo")
        raise ErroBusca(f"TikTok encontrou {len(cands)} candidato(s), mas nenhum vídeo foi salvo na biblioteca: {motivo}")
    if falhas: log(f"TikTok · {nome}: {len(ids_novos)} vídeo(s) salvo(s); {falhas} download(s) falharam")
    def capa(c):                                   # folha de contato (para olhar no chat)
        alvo = os.path.join(p, "capas", f"{c['n']:02d}.jpg")
        return alvo if c["capa"] and pegar(c["capa"], alvo) else None
    with ThreadPoolExecutor(8) as ex: capas = list(ex.map(capa, cands))
    folhas = []
    for k in range(0, len(cands), 12):
        dest = os.path.join(p, f"contato-{k // 12 + 1}.jpg"); folhas.append(dest)
        subprocess.run([PY_SISTEMA, os.path.join(LIB, "folhas.py"), dest, "4", "300", "533",
                        *[f"{f or 'x'}::{c['n']:02d} {c.get('id') or '—'} · {int(c['duracao'])}s · {humano(c['views'])}" for c, f in zip(cands[k:k + 12], capas[k:k + 12])]])
    pend = [c["id"] for c in cands if c.get("id") and not (biblioteca.item(c["id"]) or {}).get("estudo")]
    if estudar and pend:
        log(f"estudando {len(pend)} clipe(s) de '{nome}' ({biblioteca.motor_estudo()}), uma vez só…")
        try: biblioteca.estudar_pendentes(pend, contexto=f"busca '{nome}': {', '.join(termos)}", ao_cobrar=ao_cobrar_estudo, log=log)
        except ImportError: log("(este Python não tem o SDK do Claude: os clipes ficam para estudar na aba B-rolls ou na edição automática)")
    return dict(busca=busca, run=run["id"], ids=[c["id"] for c in cands if c.get("id")], novos=ids_novos, folhas=folhas, falhas=falhas)

# ---------------------------------------------------------------- quadros (para olhar no chat)
def duracao(arq):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", arq], capture_output=True, text=True)
    try: return float(r.stdout.strip())
    except ValueError: return 0.0

def quadros(d, busca, itens, quantos=5):
    p = os.path.join(d, "busca", busca); dados = json.load(open(os.path.join(p, "busca.json")))
    por_n = {c["n"]: c for c in dados["candidatos"]}; lista = [por_n[int(x)] for x in re.findall(r"\d+", itens) if int(x) in por_n]
    B = biblioteca.ler()["itens"]; celulas = []
    for c in lista:
        arq = B.get(c.get("id"), {}).get("arquivo")
        if not arq or not os.path.exists(arq):
            celulas += ["x::" + f"{c['n']:02d} sem vídeo"] + ["x::"] * (quantos - 1); continue
        tot = duracao(arq)
        for k in range(quantos):
            t = round(tot * (k + 0.5) / quantos, 1); o = os.path.join(p, "quadros", f"{c['n']:02d}_{k}.jpg"); os.makedirs(os.path.dirname(o), exist_ok=True)
            subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-ss", str(t), "-i", arq, "-frames:v", "1", "-vf", "scale=200:356", o])
            celulas.append(f"{o if os.path.exists(o) else 'x'}::{c['n']:02d} {c.get('id', '')} · {t}s" + (f" de {tot:.0f}s" if k == 0 else ""))
    dest = os.path.join(p, "quadros.jpg")
    subprocess.run([PY_SISTEMA, os.path.join(LIB, "folhas.py"), dest, str(quantos), "200", "356", *celulas], check=True)
    return dest

def main():
    a = sys.argv[1:]
    if len(a) < 3: print(__doc__); return
    def opt(nome, padrao=None):
        if nome not in a: return padrao
        i = a.index(nome); v = a[i + 1]; del a[i:i + 2]; return v
    n = int(opt("--n", 15)); dur = tuple(int(x) for x in opt("--dur", "3-45").split("-")); regiao = opt("--regiao", "US")
    sem = "--sem-estudo" in a
    if sem: a.remove("--sem-estudo")
    cmd, proj = a[0], a[1]; d = proj if os.path.isdir(proj) else os.path.join(comum.RAIZ, proj)
    if not os.path.isfile(os.path.join(d, "projeto.json")): print("erro: projeto não encontrado:", d); sys.exit(1)
    if cmd == "buscar":
        r = buscar(d, a[2], a[3:], n, dur, regiao, estudar=not sem)
        print(f"busca     {r['busca']}\nbiblioteca {len(r['ids'])} B-roll(s): {' '.join(r['ids'])}  ({len(r['novos'])} novo(s))")
        for f in r["folhas"]: print(f"contato   {f}")
        for i in r["ids"]:
            x = biblioteca.item(i)
            if x: print("  " + (biblioteca.resumo_estudo(x) if x.get("estudo") else f"{i} · {x.get('dur', 0):.1f}s · (sem estudo) {x.get('titulo', '')[:70]}"))
    elif cmd == "quadros": print(quadros(d, a[2], a[3]))
    else: print(__doc__)

if __name__ == "__main__":
    main()
