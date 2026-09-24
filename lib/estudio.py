#!/usr/bin/env python3
"""Estúdio de Edição — linha de comando. Cada etapa grava o resultado na pasta do projeto (~/Edições/<projeto>).

  estudio.py novo --nome "X" --video V.mp4 --roteiro R.docx [--parte "VSL PARTE 01"] [--estilo ultradinamico] [--sigla TSN] [--expert E] [--oferta O]
  estudio.py cortes <projeto>             proposta de cortes contra o roteiro -> fonte/cortes.json + fonte/cortes.md
  estudio.py cortes <projeto> --aplicar   gera o jump cut de cada versão + verificação (versoes/<V>/verificacao.md)
  estudio.py preparar <projeto>           recorte da pessoa (RVM), proxy da prévia, ganhos de áudio
  estudio.py acervo <projeto> [--busca ID ...] [--pasta DIR]   catálogo do editor: fichas das buscas DESTE vídeo + assets/ + uploads
                                          (--pasta só quando o usuário indicar a pasta; copia para assets/)
  estudio.py rascunho <projeto>           cria edicao.py com a fala de cada versão em comentário, para escrever o roteiro de edição
  estudio.py plano <projeto> [--versao A] [--forcar]   roda edicao.py, grava versoes/<V>/plano.json e confere as regras do modelo
  estudio.py conferir <projeto> [--versao A]           confere as regras do modelo no plano atual (depois do editor)
  estudio.py render <projeto> [--versao A]             render paralelo (sem --versao: todas)
  estudio.py broll <projeto> <V> <N> --id ID [--ini S] [--tipo cheia|canto|dividida|card]   troca o B-roll N
  estudio.py abrir                         sobe o servidor e abre a tela inicial
  estudio.py status <projeto>"""
import os, sys, re, json, glob, time, shutil, subprocess, argparse, hashlib, unicodedata, zipfile, html
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
LIB = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, LIB)
import comum
from comum import SKILL, RAIZ
PY = sys.executable; FPS = 30

def sai(msg): print(f"erro: {msg}", file=sys.stderr); sys.exit(1)
def slug(nome): return re.sub(r"[/:]", "-", nome).strip()
def proj_dir(p):
    d = p if os.path.isabs(p) else os.path.join(RAIZ, p)
    if not os.path.exists(os.path.join(d, "projeto.json")): sai(f"projeto não encontrado: {d}")
    return d
def ler_proj(d): return json.load(open(os.path.join(d, "projeto.json")))
def grava_proj(d, P): json.dump(P, open(os.path.join(d, "projeto.json"), "w"), ensure_ascii=False, indent=1)
def ff(*a):
    try:
        subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *a], check=True,
                       stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as exc:
        # O Kanban conserva o final do erro. Inclua ali a causa do FFmpeg,
        # em vez de apenas o comando e o código de saída do subprocesso.
        detalhe = (exc.stderr or "sem diagnóstico do FFmpeg").strip()[-2000:]
        raise RuntimeError(f"FFmpeg falhou (código {exc.returncode}):\n{detalhe}") from None


@lru_cache(maxsize=1)
def opcao_grafo_arquivo():
    """FFmpeg 5/6 usa *_script; versões recentes aceitam -/ e removem *_script."""
    ajuda = subprocess.run(["ffmpeg", "-hide_banner", "-h", "full"], check=True,
                           capture_output=True, text=True).stdout
    return "-filter_complex_script" if re.search(r"(?m)^-filter_complex_script(?:\s|$)", ajuda) else "-/filter_complex"


# ---------------------------------------------------------------- transcrição
def transcrever_arquivo(wav16k, prompt=""):
    from transcricao import transcrever_arquivo as transcrever
    return transcrever(wav16k, prompt)

def palavras_de(r): return [dict(w=w["word"].strip(), t=w["start"], e=w["end"]) for s in r["segments"] for w in s["words"] if w["word"].strip()]

def ler_roteiro(caminho):
    if caminho.lower().endswith(".docx"):
        x = zipfile.ZipFile(caminho).read("word/document.xml").decode("utf-8")
        paras = re.findall(r"<w:p[ >].*?</w:p>", x, flags=re.S)
        return "\n".join(html.unescape("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.S))) for p in paras)
    return open(caminho, encoding="utf-8").read()

def nomes_proprios(txt):
    c = re.findall(r"\b[A-ZÁÉÍÓÚÂÊÔÃÕ][a-záéíóúâêôãõç]+(?:\s+[A-ZÁÉÍÓÚÂÊÔÃÕ][a-záéíóúâêôãõç]+)+", txt)
    return ", ".join(dict.fromkeys(c))[:400]

# ---------------------------------------------------------------- novo
def cmd_novo(a):
    from video_integral import identidade_arquivo
    identidade_fonte = identidade_arquivo(a.video)
    d = os.path.join(RAIZ, slug(a.nome))
    if os.path.exists(os.path.join(d, "projeto.json")): sai(f"já existe: {d}")
    os.makedirs(os.path.join(d, "fonte"), exist_ok=True); os.makedirs(os.path.join(d, "assets"), exist_ok=True)
    rot = ler_roteiro(a.roteiro) if a.roteiro else ""
    if rot: open(os.path.join(d, "fonte", "roteiro.txt"), "w").write(rot)
    print("extraindo áudio…", flush=True)
    ff("-i", a.video, "-vn", "-ac", "1", "-ar", "16000", os.path.join(d, "fonte", "voz16k.wav"))
    ff("-i", a.video, "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", os.path.join(d, "fonte", "voz48k.wav"))
    if getattr(a, "whisper", None) and os.path.exists(a.whisper):
        print("transcrição reaproveitada (a busca de B-roll já tinha feito)", flush=True)
        r = json.load(open(a.whisper)); r = r.get("whisper", r)
    else:
        print("transcrevendo (palavra por palavra)…", flush=True)
        r = transcrever_arquivo(os.path.join(d, "fonte", "voz16k.wav"), nomes_proprios(rot))
    json.dump(r, open(os.path.join(d, "fonte", "whisper.json"), "w"), ensure_ascii=False)
    parte = a.parte
    if rot and (not parte or parte == "auto"):
        import cortes_auto; parte = cortes_auto.detectar_parte(rot, palavras_de(r))
        if parte: print(f"parte do roteiro gravada neste vídeo: {parte}", flush=True)
    P = dict(nome=a.nome, estilo=a.estilo, expert=a.expert or "", oferta=a.oferta or "", sigla=a.sigla or "", fonte_video=os.path.abspath(a.video),
             fonte_identidade=identidade_fonte,
             roteiro="fonte/roteiro.txt" if rot else None, parte=parte, criado=time.strftime("%Y-%m-%d"), versoes=[],
             buscas=[])   # B-roll sempre novo: o acervo do projeto só tem o que foi baixado nestas buscas (estudio.py acervo --busca)
    if identidade_arquivo(a.video) != identidade_fonte:
        raise ValueError("O vídeo mudou durante a transcrição. Envie novamente o arquivo.")
    grava_proj(d, P); print(f"projeto criado: {d}\npróximo: estudio.py cortes \"{a.nome}\"")

# ---------------------------------------------------------------- cortes
def cmd_cortes(a):
    import cortes_auto as C, numpy as np, wave
    d = proj_dir(a.projeto); P = ler_proj(d); f = os.path.join(d, "fonte")
    if not a.aplicar:
        if not P.get("roteiro"): sai("este projeto não tem roteiro: a proposta automática compara a fala com o roteiro")
        pal = palavras_de(json.load(open(os.path.join(f, "whisper.json"))))
        prop = C.proposta(pal, open(os.path.join(d, P["roteiro"])).read(), parte=P.get("parte"))
        if not a.sem_refino:
            w = wave.open(os.path.join(f, "voz16k.wav")); X = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
            def tr(i0, i1):
                rr = transcrever_arquivo(X[int(i0 * 16000):int(i1 * 16000)])
                return [dict(w=x["word"].strip(), t=i0 + x["start"], e=i0 + x["end"]) for s in rr["segments"] for x in s["words"] if x["word"].strip()]
            prop = C.refinar_pausas(prop, pal, None, tr)
        json.dump(prop, open(os.path.join(f, "cortes.json"), "w"), ensure_ascii=False, indent=1)
        open(os.path.join(f, "cortes.md"), "w").write(C.relatorio(prop))
        print(open(os.path.join(f, "cortes.md")).read()[:5000]); print(f"\nrevise fonte/cortes.md; ajuste fonte/cortes.json se precisar; depois: estudio.py cortes \"{P['nome']}\" --aplicar")
        return
    prop = json.load(open(os.path.join(f, "cortes.json")))
    cfg = prop.get("cortes_cfg") or {}
    pals = palavras_de(json.load(open(os.path.join(f, "whisper.json"))))
    env = envelope(os.path.join(f, "voz16k.wav"))
    copia = os.path.join(f, "src1440.mov")
    if not os.path.exists(copia):
        print("gerando cópia de trabalho 1440x2560…", flush=True)
        ff("-i", P["fonte_video"], "-an", "-vf", "scale=1440:2560:flags=lanczos,fps=30", "-c:v", "libx264", "-crf", "13", "-preset", "fast", "-pix_fmt", "yuv420p", copia)
    so = set(a.versoes.split(",")) if a.versoes else None
    rot = open(os.path.join(d, P["roteiro"])).read() if P.get("roteiro") else ""
    for v in prop["versoes"]:
        if so and v["id"] not in so: continue
        vd = os.path.join(d, "versoes", v["id"]); os.makedirs(vd, exist_ok=True)
        # Exclusões aprovadas nunca podem voltar ao juntar faixas no modo VAD.
        fx = juntar_faixas(v["faixas"], 0 if cfg.get("modo") == "fala_vad" else cfg.get("buraco_min", 0.25))
        if v.get("segs"): segs = [[float(x), float(y)] for x, y in v["segs"]]            # revisado por você na tela de cortes
        elif cfg.get("modo") == "fala_vad":
            from segmentacao_fala import segmentar_arquivo
            segs = segmentar_arquivo(os.path.join(f, "voz16k.wav"), fx, pals, dict(cfg, fps=FPS))
        elif cfg.get("modo") == "volume": segs = segmentos(env, fx)                      # jeito antigo, se alguém pedir
        else: segs = segmentos_fala(pals, fx, cfg.get("pausa_min", 0.55), cfg.get("respiro", 0.18), cfg.get("cauda", 0.30))
        if not segs: sai("nenhuma fala detectada nas faixas aprovadas; fonte preservada")
        tot = sum(b - a for a, b in segs)
        print(f"versão {v['id']}: {len(segs)} trechos, {tot:.1f}s — renderizando jump cut…", flush=True)
        jump_cut(copia, os.path.join(f, "voz48k.wav"), segs, vd)
        r = transcrever_arquivo(os.path.join(vd, "voz16k.wav"), nomes_proprios(rot))
        json.dump(r, open(os.path.join(vd, "whisper.json"), "w"), ensure_ascii=False)
        verificar(vd, r, [fr for fr in prop.get("frases", []) if fr.get("secao") in v.get("secoes", [])])
    P["versoes"] = [dict(id=v["id"], nome=v["nome"]) for v in prop["versoes"] if os.path.exists(os.path.join(d, "versoes", v["id"], "mapa.json"))]
    grava_proj(d, P)
    if not a.manter_copia: os.remove(copia)
    print("pronto. confira versoes/<V>/verificacao.md; próximo: estudio.py preparar")

def envelope(wav16k):
    import numpy as np, wave
    w = wave.open(wav16k); x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    hop = 160; return 20 * np.log10(np.sqrt((x[:len(x) // hop * hop].reshape(-1, hop) ** 2).mean(1)) + 1e-9)

def juntar_faixas(faixas, buraco_min=0.25):
    """Faixas coladas (buraco menor que ~0,25 s) viram uma só: tirar 0,1 s não economiza nada e ainda pode
    engolir o fim de uma palavra. Acontece quando a IA divide a fala em dois trechos seguidos."""
    fs = sorted([list(map(float, f)) for f in faixas], key=lambda f: f[0]); out = [fs[0]]
    for a, b in fs[1:]:
        if a - out[-1][1] < buraco_min: out[-1][1] = max(out[-1][1], b)
        else: out.append([a, b])
    return out

def segmentos_fala(W, faixas, pausa_min=0.55, respiro=0.18, cauda=0.30):
    """Trechos que ficam, decididos pelas PALAVRAS da transcrição (não pelo volume do áudio).

    Corta só a pausa ENTRE duas palavras e só quando ela passa de `pausa_min`, deixando `respiro` de cada lado —
    nunca dentro de uma palavra. O jeito antigo (ilhas de volume) engolia sílaba: num teste, 43 dos 48 cortes caíam
    no meio de uma palavra, porque a oclusiva de um /p/ ou /t/ parece silêncio no envelope.

    As bordas de cada faixa também ganham ar: a borda encosta no silêncio, não na vogal. Onde a faixa termina no meio
    de uma pausa, o trecho respira até pouco antes da próxima palavra (mesmo que ela tenha sido descartada)."""
    def antes_de(t): return max((w["e"] for w in W if w["e"] <= t + 0.001), default=None)
    def depois_de(t): return min((w["t"] for w in W if w["t"] >= t - 0.001), default=None)
    out = []
    for a, b in faixas:
        ws = [w for w in W if w["e"] > a + 0.01 and w["t"] < b - 0.01]
        if not ws: continue
        def abre(p):                                        # começo do trecho: recua até o respiro, sem tocar na anterior
            ant = antes_de(p["t"] - 0.001)
            alvo = p["t"] - respiro
            return round(max(alvo if ant is None else max(alvo, ant + 0.06), 0.0), 3)
        def fecha(p):                                       # fim do trecho: respira, sem invadir a próxima palavra
            prox = depois_de(p["e"] + 0.001)
            alvo = p["e"] + cauda
            if prox is not None: alvo = min(alvo, prox - 0.06)
            return round(max(p["e"] + 0.05, alvo), 3)
        cur = [abre(ws[0]), None]
        for k in range(len(ws) - 1):
            if ws[k + 1]["t"] - ws[k]["e"] > pausa_min:
                cur[1] = fecha(ws[k]); out.append(cur); cur = [abre(ws[k + 1]), None]
        cur[1] = fecha(ws[-1]); out.append(cur)
    out.sort(key=lambda t: t[0]); juntos = [out[0]]
    for t in out[1:]:
        if t[0] - juntos[-1][1] < 0.12: juntos[-1][1] = max(juntos[-1][1], t[1])   # sobra ínfima não vira corte
        else: juntos.append(t)
    return [t for t in juntos if t[1] - t[0] > 0.15]

def segmentos(e, faixas, HEAD=0.08, TAIL=0.14, MINGAP=0.24):
    """Ilhas de fala (limiar adaptativo) dentro de cada faixa; garante o fim real da última palavra.
    As faixas ficam na ordem recebida (a do roteiro): um lead gravado depois do corpo continua abrindo o vídeo."""
    import numpy as np
    p20, p90 = np.percentile(e, [20, 90]); thr = p20 + 0.55 * (p90 - p20); segs = []
    for a, b in faixas:
        ia, ib = int(max(0, a - 0.1) * 100), int((b + 0.2) * 100); m = e[ia:ib] > thr; isl = []; i = 0
        while i < len(m):
            if m[i]:
                j = i
                while j < len(m) and m[j]: j += 1
                if j - i >= 3: isl.append([(ia + i) / 100, (ia + j) / 100])
                i = j
            else: i += 1
        if not isl: continue
        mer = [isl[0]]
        for s in isl[1:]:
            if s[0] - mer[-1][1] < MINGAP: mer[-1][1] = s[1]
            else: mer.append(s)
        mer[0][0] = min(mer[0][0], a); mer[-1][1] = max(mer[-1][1], b)
        for s in mer: segs.append([round(max(a - 0.12, s[0] - HEAD), 3), round(min(b + 0.2, s[1] + TAIL), 3)])
    out = [segs[0]]
    for s in segs[1:]:
        if out[-1][0] <= s[0] <= out[-1][1] + 0.02: out[-1][1] = max(out[-1][1], s[1])
        else: out.append(s)
    return out

def jump_cut(copia, voz48, segs, vd):
    snap = lambda t: round(t * FPS) / FPS; pv, pa, lv, la, mapa, t = [], [], [], [], [], 0
    for i, (a, b) in enumerate(segs):
        a, b = snap(a), snap(b); dur = b - a
        pv.append(f"[0:v]trim=start={a:.5f}:end={b:.5f},setpts=PTS-STARTPTS[v{i}]")
        af = f"atrim=start={a:.5f}:end={b:.5f},asetpts=PTS-STARTPTS,afade=t=in:d=0.012,afade=t=out:st={dur - 0.015:.5f}:d=0.015[a{i}]"
        pa.append("[1:a]" + af); lv.append(f"[v{i}][a{i}]"); la.append(f"[a{i}]"); mapa.append((a, b, round(t, 4))); t += dur
    open(os.path.join(vd, "_jc.txt"), "w").write(";".join(pv + pa) + ";" + "".join(lv) + f"concat=n={len(segs)}:v=1:a=1[v][a]")
    ff("-i", copia, "-i", voz48, opcao_grafo_arquivo(), os.path.join(vd, "_jc.txt"), "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "13",
       "-preset", "fast", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "pcm_s16le", os.path.join(vd, "jc.mov"))
    ff("-i", os.path.join(vd, "jc.mov"), "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", os.path.join(vd, "voz.wav"))
    ff("-i", os.path.join(vd, "voz.wav"), "-ac", "1", "-ar", "16000", os.path.join(vd, "voz16k.wav"))
    os.remove(os.path.join(vd, "_jc.txt")); json.dump(dict(mapa=mapa, dur=round(t, 4)), open(os.path.join(vd, "mapa.json"), "w"))

def verificar(vd, r, frases):
    """Depois do jump cut: repetição que escapou e frase do roteiro que ficou faltando."""
    from cortes_auto import tok
    from difflib import SequenceMatcher
    pal = palavras_de(r); T = [tok(p["w"]) for p in pal]; L = ["# Verificação do jump cut", ""]
    # Tokens ASR sem duração não provam uma repetição audível. Filtrar somente
    # esta detecção; conservar a transcrição e a comparação com roteiro intactas.
    pal_rep = [p for p in pal if p["e"] > p["t"]]; R = [tok(p["w"]) for p in pal_rep]
    reps = []
    for p in range(len(R) - 2):
        for q in range(p + 1, min(len(R) - 2, p + 12)):
            if R[p:p + 3] == R[q:q + 3] and len(R[p]) > 1: reps.append((pal_rep[p]["t"], " ".join(x["w"] for x in pal_rep[p:q + 3]))); break
        if len(R[p]) > 3 and R[p] == R[p + 1]: reps.append((pal_rep[p]["t"], pal_rep[p]["w"] + " " + pal_rep[p + 1]["w"]))
    L += [f"## Repetições que sobraram ({len(reps)})", ""] + [f"- {t:.1f}s: \"{s}\"" for t, s in reps]
    alvo = [x for fr in (frases or []) for x in fr.get("texto", "").split()]; A = [tok(w) for w in alvo if tok(w)]
    if A:
        sm = SequenceMatcher(None, A, T, autojunk=False); falta = []
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op in ("delete", "replace") and i2 - i1 >= 3: falta.append(" ".join(alvo[i1:i2]))
        L += ["", f"## Trechos do roteiro que não aparecem ({len(falta)})", ""] + [f"- \"{x}\"" for x in falta]
        L += ["", f"semelhança roteiro × fala: {sm.ratio():.2f}"]
    else:
        L += ["", "Roteiro não fornecido; comparação de cobertura com roteiro não aplicada."]
    open(os.path.join(vd, "verificacao.md"), "w").write("\n".join(L)); print("\n".join(L)[:1500])


# ---------------------------------------------------------------- preparar
def cmd_preparar(a):
    d = proj_dir(a.projeto); P = ler_proj(d)
    for v in P["versoes"]:
        vd = os.path.join(d, "versoes", v["id"]); print(f"versão {v['id']}: máscaras…", flush=True); t0 = time.time()
        mascaras(vd); print(f"  máscaras em {time.time() - t0:.0f}s; proxy da prévia…", flush=True)
        ff("-i", os.path.join(vd, "jc.mov"), "-framerate", "30", "-start_number", "0", "-i", os.path.join(vd, "masks", "f%05d.png"),
           "-filter_complex", "[0:v]scale=540:960[p];[1:v]format=gray,scale=540:960,format=yuv420p[m];[p][m]hstack=inputs=2[v]",
           "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-g", "15", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", os.path.join(vd, "pm.mp4"))
        ganhos(vd)
    print("pronto; próximo: estudio.py acervo / rascunho / plano")

# O recorte da pessoa. Medido: o resnet50 quase não melhora a borda (0,66% contra 0,68% de pixels piscando)
# e custa 2,4x o tempo, então o padrão é o mobilenetv3. Dá para trocar em config.json → "matte".
MATTE = dict(res=1080, modelo="mobilenetv3", procs=4)

def mascaras(vd, partes=3):
    """Recorte da pessoa com RobustVideoMatting (lib/matte.py).

    Era a Vision do macOS, que decide cada quadro do zero: a borda tremia (4,97% dos pixels piscando) e
    às vezes um pedaço do fundo vinha junto, agarrado acima da cabeça. O RVM é feito para vídeo, leva o
    estado de um quadro para o outro e devolve alfa contínuo — medido no mesmo trecho, 0,66% piscando e
    cabelo com fio solto preservado. Mais lento, e vale: é a imagem que vai ao ar."""
    import matte
    cfg = {**MATTE, **(comum.CONFIG.get("matte") or {})}
    n = matte.gerar_paralelo(vd, res=int(cfg["res"]), modelo=cfg["modelo"], procs=int(cfg.get("procs") or 4),
                             log=lambda m: print("  " + m, flush=True))
    q = comum.quadros_video(os.path.join(vd, "jc.mov"))
    if q and n != q: sai(f"máscaras incompletas: {n}/{q}")

def ganhos(vd):
    import numpy as np, wave
    w = wave.open(os.path.join(vd, "voz.wav")); x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    gpk = float(10 ** (-3 / 20) / np.max(np.abs(x)))
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", os.path.join(vd, "voz.wav"), "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
    I = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r)[-1]); gln = float(10 ** ((-14 - (I + 20 * np.log10(gpk))) / 20))
    json.dump(dict(gpk=gpk, gln=gln), open(os.path.join(vd, "ganhos.json"), "w"))

# ---------------------------------------------------------------- acervo
def front(p):
    t = open(p, encoding="utf-8").read(); m = re.match(r"---\n(.*?)\n---", t, re.S); d = {}
    if m:
        for l in m.group(1).splitlines():
            if ":" in l: k, v = l.split(":", 1); d[k.strip()] = v.strip().strip('"')
    return d

def cmd_acervo(a):
    """Catálogo do editor. B-roll é sempre novo: num projeto com a lista "buscas" (todos os criados depois de 2026-09-19),
    só entram as fichas baixadas nessas buscas (--busca ID acrescenta). Projeto antigo, sem a lista: todas as fichas da sigla.
    --pasta DIR copia para assets/ os vídeos e imagens de uma pasta que o usuário indicou."""
    d = proj_dir(a.projeto); P = ler_proj(d); sig = a.sigla or P.get("sigla"); ac = os.path.join(d, "acervo")
    os.makedirs(os.path.join(ac, "proxy"), exist_ok=True); os.makedirs(os.path.join(ac, "thumbs"), exist_ok=True)
    if a.busca:
        P.setdefault("buscas", [])
        P["buscas"] += [b for b in a.busca if b not in P["buscas"]]; grava_proj(d, P)
    if getattr(a, "biblioteca", None):
        P.setdefault("biblioteca", [])
        P["biblioteca"] += [b for b in a.biblioteca if b not in P["biblioteca"]]; grava_proj(d, P)
    if a.pasta:
        n = 0; os.makedirs(os.path.join(d, "assets"), exist_ok=True)
        for f_ in sorted(glob.glob(os.path.join(os.path.expanduser(a.pasta), "**", "*"), recursive=True)):
            if f_.lower().endswith((".mp4", ".mov", ".jpg", ".jpeg", ".png")) and not os.path.exists(os.path.join(d, "assets", os.path.basename(f_))):
                shutil.copy2(f_, os.path.join(d, "assets", os.path.basename(f_))); n += 1
        print(f"{n} arquivo(s) copiados de {a.pasta} para assets/")
    buscas = P.get("buscas")
    itens = []; vault = comum.CONFIG.get("vault_edicao", os.path.expanduser("~/Keeps/03 Edição"))
    if sig and buscas != []:
        for p in sorted(glob.glob(f"{vault}/*/*/*/{sig}-BR*.md")):
            f_ = front(p)
            if buscas is not None and f_.get("busca") not in buscas: continue
            if f_.get("tipo") == "broll" and os.path.exists(f_.get("caminho", "")):
                itens.append(dict(id=f_["id"], categoria=os.path.basename(os.path.dirname(p)), nome=os.path.basename(p)[len(f_["id"]) + 1:-3],
                                  descricao=f_.get("descricao", ""), src=f_["caminho"]))
    def lado(nome):                                   # origem dos B-rolls baixados para o projeto
        try: return json.load(open(os.path.join(d, "assets", nome)))
        except (OSError, ValueError): return {}
    yts, tts = lado("youtube.json"), lado("tiktok.json")     # lib/youtube.py e lib/tiktok.py
    for f_ in sorted(glob.glob(os.path.join(d, "assets", "*"))):
        ext = os.path.splitext(f_)[1].lower()
        if ext in (".mp4", ".mov", ".jpg", ".jpeg", ".png") and os.path.getsize(f_):
            nome = os.path.splitext(os.path.basename(f_))[0]; yt = yts.get(os.path.basename(f_)); tt = tts.get(os.path.basename(f_))
            if tt:
                itens.append(dict(id="TT-" + re.sub(r"\W+", "-", re.sub(r"^tt-", "", nome))[:24], categoria="TikTok · " + tt.get("busca", "").rsplit("-", 1)[0],
                                  nome=nome, descricao=f"{tt.get('descricao') or ''} · {tt.get('autor', '')}".strip(" ·"), src=f_, origem=tt.get("url", "")))
                continue
            if yt:
                itens.append(dict(id="YT-" + re.sub(r"\W+", "-", re.sub(r"^yt-", "", nome))[:24], categoria="YouTube", nome=nome,
                                  descricao=f"{yt['descricao']} · {yt.get('canal') or 'YouTube'}", src=f_, origem=yt.get("url", "")))
                continue
            itens.append(dict(id=("IMG-" if ext in (".jpg", ".jpeg", ".png") else "AS-") + re.sub(r"\W+", "-", nome)[:24],
                              categoria="Assets do projeto", nome=nome, descricao="", src=f_))
    anterior = {x["src"]: x for x in (json.load(open(os.path.join(ac, "acervo.json"))) if os.path.exists(os.path.join(ac, "acervo.json")) else [])}
    for it in itens:                                     # preserva recortes/nomes ajustados antes
        if it["src"] in anterior:
            for k in ("crop", "nome", "descricao", "id"):
                if anterior[it["src"]].get(k): it[k] = anterior[it["src"]][k]
    def prep(it):
        h = hashlib.md5(it["src"].encode()).hexdigest()[:12]; thumb = os.path.join(ac, "thumbs", h + ".jpg")
        if it["src"].lower().endswith((".jpg", ".jpeg", ".png")):
            if not os.path.exists(thumb): subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", it["src"], "-vf", "scale=240:-2", thumb])
            it.update(proxy=it["src"], thumb=thumb, dur=0, w=0, h=0); return it
        pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration", "-of", "json",
                                        it["src"]], capture_output=True, text=True).stdout)
        dur = float(pr["format"].get("duration", 0) or 0); proxy = os.path.join(ac, "proxy", h + ".mp4")
        if not os.path.exists(proxy):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", it["src"], "-an", "-vf", "scale='min(540,iw)':-2", "-c:v", "libx264", "-preset", "veryfast",
                            "-crf", "27", "-g", "15", "-pix_fmt", "yuv420p", "-movflags", "+faststart", proxy])
        if not os.path.exists(thumb):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{min(2.0, dur / 3):.2f}", "-i", it["src"], "-frames:v", "1", "-vf", "scale=240:-2", thumb])
        it.update(proxy=proxy, thumb=thumb, dur=round(dur, 2), w=pr["streams"][0]["width"], h=pr["streams"][0]["height"]); return it
    with ThreadPoolExecutor(6) as ex: itens = list(ex.map(prep, itens))
    if P.get("biblioteca"):                              # B-rolls da biblioteca (~/B-rolls/biblioteca) separados para este vídeo
        import biblioteca
        itens += biblioteca.acervo_de(P["biblioteca"], categoria="Biblioteca · escolhidos para este vídeo")
    json.dump(itens, open(os.path.join(ac, "acervo.json"), "w"), ensure_ascii=False, indent=1)
    cats = {}
    for it in itens: cats[it["categoria"]] = cats.get(it["categoria"], 0) + 1
    print(f"{len(itens)} itens no acervo do projeto:"); [print(f"  {k}: {n}") for k, n in cats.items()]

def acervo_por_id(d):
    a = []
    for arq in ("acervo/acervo.json", "acervo/uploads.json"):
        p = os.path.join(d, arq)
        if os.path.exists(p): a += json.load(open(p))
    return {x["id"]: x for x in a}

# ---------------------------------------------------------------- rascunho e plano
def cmd_rascunho(a):
    d = proj_dir(a.projeto); P = ler_proj(d); dest = os.path.join(d, "edicao.py")
    if os.path.exists(dest) and not a.forcar: sai("edicao.py já existe (use --forcar para recriar)")
    L = ['"""Roteiro de edição — estilo ' + P["estilo"] + '. Âncoras são palavras da fala (veja o topo de lib/dsl.py).',
         'B-roll pelo ID do acervo (acervo/acervo.json). Rode: estudio.py plano "' + P["nome"] + '" [--versao A]"""', "from dsl import *", "", "def editar(E):"]
    for v in P["versoes"]:
        r = json.load(open(os.path.join(d, "versoes", v["id"], "whisper.json")))
        L.append(f"    # ---------------- fala da versão {v['id']} ({v['nome']})")
        for s in r["segments"]:
            L.append(f"    # [{v['id']} {s['start']:6.1f}] {s['text'].strip()}")
    L += ["    pass", ""]
    open(dest, "w").write("\n".join(L)); print(f"criado {dest}")

def zoom_auto(P, E, dur=None):
    """Trilha do zoom: um movimento lento por trecho (aproxima, depois afasta), em vez de pular de um valor para outro
    a cada corte. Trecho curto não inverte o movimento — sem isso o zoom fica indo e voltando em frações de segundo.
    Cada ponto é [t, z, "suave"] (caminha até ali) ou [t, z] (seco, é o punch do letreiro)."""
    z = E["zoom"]
    def limita(valor): return min(z.get("maximo", float("inf")), max(z.get("minimo", -float("inf")), valor))
    alvos = [limita(v) for v in z["alterna"]]; lo = alvos[0]
    minmov = z.get("min_movimento", 2.2)                  # trecho mais curto que isso segue o movimento que já vinha
    cortes = sorted(set([0.0] + list(P["cortes"]))); fim_v = dur or P.get("dur") or (cortes[-1] + 4)
    curva, i = [], 1                                      # segue a ordem de `alterna` (o orgânico tem 4 valores)
    for k, c in enumerate(cortes):
        f = cortes[k + 1] if k + 1 < len(cortes) else fim_v
        if f - c < minmov: continue                       # sem ponto novo: o movimento anterior continua
        curva.append((round(f - 0.03, 3), round(alvos[i % len(alvos)], 3))); i += 1
    def base_em(t):
        pt, pz = 0.0, lo
        for tt, zz in curva:
            if t >= tt: pt, pz = tt, zz; continue
            e = (t - pt) / (tt - pt) if tt > pt else 1.0; e = max(0.0, min(1.0, e))
            return pz + (zz - pz) * (e * e * (3 - 2 * e))
        return pz
    out = [[t, zz, "suave"] for t, zz in curva]
    subida = z.get("punch_subida", 0.12)                  # o punch aproxima em 0,12 s: é um empurrão, não um pulo
    espaco = z.get("punch_intervalo", 1.2)                # letreiros colados não ganham um punch cada: vira tremedeira
    ult = -99.0
    for b in sorted(P["blocos"], key=lambda b: min(l[2] for l in b["linhas"])):
        ch = [l for l in b["linhas"] if l[0] in z["estilos_punch"]]
        if not ch or not z["punch"]: continue
        t0, t1 = float(ch[0][2]), float(b["fim"])
        if t0 - ult < espaco or t1 - t0 < subida * 2: continue
        out.append([round(t0 - subida, 3), round(base_em(t0 - subida), 3), "suave"])
        out.append([round(t0, 3), round(base_em(t0) + z["punch"], 3), "suave"])
        out.append([round(t1, 3), round(base_em(t1), 3), "suave"])
        ult = t1
    for ponto in out: ponto[1] = limita(ponto[1])
    out.sort(key=lambda e: e[0])
    return out

def ler_mapa(vd):
    """Trechos da versão (início e fim na fonte, início na versão) e duração, de versoes/<V>/mapa.json."""
    m = json.load(open(os.path.join(vd, "mapa.json")))
    segs = [(float(a), float(b), float(t)) for a, b, t in (m["mapa"] if isinstance(m, dict) else m)]
    dur = float(m["dur"]) if isinstance(m, dict) else (segs[-1][2] + segs[-1][1] - segs[-1][0] if segs else 0.0)
    return segs, dur

def cmd_plano(a):
    """edicao.py (roteiro escrito no chat, âncoras em palavras) ou, se não existir, edicao_ia.json (feito pela edição
    automática, âncoras no número da palavra da fonte; lib/ops.py) -> versoes/<V>/plano.json + legendas. Confere as regras
    de ritmo do modelo (estilo.json -> "regras"). --relatorio grava a conferência de cada versão (JSON)."""
    import importlib.util, dsl, regras, ops
    d = proj_dir(a.projeto); P = ler_proj(d); E = comum.estilo(P["estilo"]); ac = acervo_por_id(d); rels = {}; mod = O = None
    if os.path.exists(os.path.join(d, "edicao.py")):
        spec = importlib.util.spec_from_file_location("edicao", os.path.join(d, "edicao.py")); mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, LIB); spec.loader.exec_module(mod)
    elif os.path.exists(os.path.join(d, "edicao_ia.json")):
        O = json.load(open(os.path.join(d, "edicao_ia.json"))); W = ops.palavras(json.load(open(os.path.join(d, "fonte", "whisper.json"))))
    else: sai("falta o edicao.py (estudio.py rascunho cria um com a fala) ou o edicao_ia.json")
    for v in P["versoes"]:
        if a.versao and v["id"] != a.versao: continue
        vd = os.path.join(d, "versoes", v["id"]); destino = os.path.join(vd, "plano.json")
        if os.path.exists(destino) and not a.forcar:
            print(f"versão {v['id']}: plano.json já existe (tem edições do editor). Use --forcar para sobrescrever."); continue
        r = json.load(open(os.path.join(vd, "whisper.json"))); segs, dur = ler_mapa(vd)
        if mod:
            ed = dsl.Edicao(v["id"], palavras_de(r), dur, ac, E); mod.editar(ed); res = ed.resultado(); avisos = ed.avisos
        else:
            res, rel = ops.construir(O, W, segs, dur, ac, E); rels[v["id"]] = rel; avisos = rel["erros"] + rel["avisos"]
        Pl = dict(src="jc.mov", masks="masks", whisper="whisper.json", estilo=P["estilo"], dur=dur, cortes=[x[2] for x in segs],
                  correcoes=getattr(mod, "CORRECOES", {}) if mod else O.get("correcoes", {}), **res)
        n = 1
        for c in Pl["cenas"]: c["uid"] = f"c{n}"; n += 1
        for b in Pl["blocos"]: b["uid"] = f"b{n}"; n += 1
        Pl.update(_prox_uid=n, sfx_edicoes={}, sfx_extras=[], zoom=zoom_auto(Pl, E))
        if os.path.exists(destino): shutil.copy(destino, os.path.join(vd, "historico", f"plano_{time.strftime('%Y%m%d-%H%M%S')}.json")) if os.path.isdir(os.path.join(vd, "historico")) else None
        json.dump(Pl, open(destino, "w"), ensure_ascii=False, indent=1)
        subprocess.run([PY, os.path.join(LIB, "motor.py"), vd, "legendas"], check=True, stdout=subprocess.DEVNULL)
        if os.path.isdir(os.path.join(vd, "masks")): subprocess.run([PY, os.path.join(LIB, "motor.py"), vd, "cabecas"], stdout=subprocess.DEVNULL)
        print(f"versão {v['id']}: {len(Pl['blocos'])} letreiros, {len(Pl['cenas'])} B-rolls, {len(Pl['escuro'])} modo escuro")
        for w in avisos: print("  aviso:", w)
        if mod:
            for w in regras.conferir(Pl, E): print("  regra:", w)
    if getattr(a, "relatorio", None): json.dump(rels, open(a.relatorio, "w"), ensure_ascii=False, indent=1)

def cmd_conferir(a):
    """Confere as regras de ritmo do modelo no plano.json atual (inclusive o que o usuário mexeu no editor)."""
    import regras
    d = proj_dir(a.projeto); P = ler_proj(d); E = comum.estilo(P["estilo"])
    if not E.get("regras"): print(f"o modelo {E['nome']} não tem regras de ritmo para conferir"); return
    for v in P["versoes"]:
        if a.versao and v["id"] != a.versao: continue
        Pl = json.load(open(os.path.join(d, "versoes", v["id"], "plano.json"))); av = regras.conferir(Pl, E)
        print(f"versão {v['id']}: " + ("tudo dentro das regras" if not av else f"{len(av)} ponto(s)"))
        for w in av: print("  regra:", w)

# ---------------------------------------------------------------- render, broll, abrir, status
def cmd_render(a):
    d = proj_dir(a.projeto); P = ler_proj(d)
    for v in P["versoes"]:
        if a.versao and v["id"] != a.versao: continue
        vd = os.path.join(d, "versoes", v["id"]); print(f"renderizando {v['nome']}…", flush=True)
        p = subprocess.Popen([PY, os.path.join(LIB, "render.py"), vd], stdout=subprocess.PIPE, text=True); saida = None
        for l in p.stdout:
            if l.startswith("saida"): saida = l[6:].strip()
            elif l.startswith("tempo"): print(" ", l.strip())
        p.wait()
        if p.returncode: sai("render falhou")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "3", "-i", saida, "-frames:v", "1", "-vf", "scale=360:-2", os.path.join(vd, "capa.jpg")])
        print(f"  {saida}")

def cmd_broll(a):
    d = proj_dir(a.projeto); vd = os.path.join(d, "versoes", a.versao); Pl = json.load(open(os.path.join(vd, "plano.json")))
    cenas = sorted(Pl["cenas"], key=lambda c: c["ini"])
    if not 1 <= a.n <= len(cenas): sai(f"B-roll {a.n} não existe (há {len(cenas)})")
    c = cenas[a.n - 1]; it = acervo_por_id(d).get(a.id)
    if not it: sai(f"{a.id} não está no acervo do projeto")
    antes = c.get("id"); c.update(src=it["src"], id=it["id"], slot="troca", src_ini=a.ini or 0.0)
    c.pop("crop", None)
    if it.get("crop"): c["crop"] = it["crop"]
    if a.tipo: c["tipo"] = a.tipo
    os.makedirs(os.path.join(vd, "historico"), exist_ok=True)
    shutil.copy(os.path.join(vd, "plano.json"), os.path.join(vd, "historico", f"plano_{time.strftime('%Y%m%d-%H%M%S')}.json"))
    json.dump(Pl, open(os.path.join(vd, "plano.json"), "w"), ensure_ascii=False, indent=1)
    print(f"B-roll {a.n} ({c['ini']:.1f}–{c['fim']:.1f}s): {antes} -> {it['id']} ({it.get('nome', '')})")

def cmd_abrir(a):
    porta = comum.PORTA
    try:
        import urllib.request; urllib.request.urlopen(f"http://127.0.0.1:{porta}/api/projetos", timeout=1)
    except Exception:
        subprocess.Popen([PY, os.path.join(SKILL, "app", "servidor.py")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True); time.sleep(1.5)
    if sys.platform == "darwin" or os.environ.get("DISPLAY"):
        import webbrowser; webbrowser.open(f"http://localhost:{porta}")
    print(f"Estúdio em http://localhost:{porta}")

def cmd_status(a):
    d = proj_dir(a.projeto); P = ler_proj(d); print(f"{P['nome']} · estilo {P['estilo']} · {d}")
    etapas = [("fonte/whisper.json", "transcrição"), ("fonte/cortes.json", "proposta de cortes"), ("edicao.py", "roteiro de edição"),
              ("acervo/acervo.json", "acervo")]
    for arq, nome in etapas: print(f"  [{'x' if os.path.exists(os.path.join(d, arq)) else ' '}] {nome}")
    for v in P["versoes"]:
        vd = os.path.join(d, "versoes", v["id"]); ok = lambda f: "x" if os.path.exists(os.path.join(vd, f)) else " "
        rs = sorted(glob.glob(os.path.join(vd, "renders", "*.mp4")), key=os.path.getmtime)
        print(f"  {v['nome']}: [{ok('jc.mov')}] jump cut [{ok('masks')}] máscaras [{ok('pm.mp4')}] prévia [{ok('plano.json')}] plano  renders: {len(rs)}")

def main():
    ap = argparse.ArgumentParser(description="Estúdio de Edição"); sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("novo"); p.add_argument("--nome", required=True); p.add_argument("--video", required=True); p.add_argument("--roteiro")
    p.add_argument("--whisper", help="transcrição pronta (json da leva): pula o Whisper")
    p.add_argument("--parte", help="'VSL PARTE 01'; sem isso (ou 'auto') detecta pela fala"); p.add_argument("--estilo", default="ultradinamico")
    p.add_argument("--sigla"); p.add_argument("--expert"); p.add_argument("--oferta")
    p = sp.add_parser("cortes"); p.add_argument("projeto"); p.add_argument("--aplicar", action="store_true"); p.add_argument("--sem-refino", action="store_true")
    p.add_argument("--manter-copia", action="store_true"); p.add_argument("--versoes", help="só estas versões, ex.: A,B")
    p = sp.add_parser("preparar"); p.add_argument("projeto")
    p = sp.add_parser("acervo"); p.add_argument("projeto"); p.add_argument("--sigla")
    p.add_argument("--busca", nargs="+", help="IDs das buscas do obsidian-broll feitas para este vídeo")
    p.add_argument("--pasta", help="pasta que o usuário indicou: copia os vídeos/imagens para assets/")
    p.add_argument("--biblioteca", nargs="+", help="IDs da biblioteca de B-rolls (B0012…) que este vídeo vai usar")
    p = sp.add_parser("rascunho"); p.add_argument("projeto"); p.add_argument("--forcar", action="store_true")
    p = sp.add_parser("plano"); p.add_argument("projeto"); p.add_argument("--versao"); p.add_argument("--forcar", action="store_true")
    p.add_argument("--relatorio", help="grava a conferência de cada versão (JSON) — edição automática")
    p = sp.add_parser("conferir"); p.add_argument("projeto"); p.add_argument("--versao")
    p = sp.add_parser("render"); p.add_argument("projeto"); p.add_argument("--versao")
    p = sp.add_parser("broll"); p.add_argument("projeto"); p.add_argument("versao"); p.add_argument("n", type=int); p.add_argument("--id", required=True)
    p.add_argument("--ini", type=float); p.add_argument("--tipo")
    sp.add_parser("abrir")
    p = sp.add_parser("status"); p.add_argument("projeto")
    a = ap.parse_args(); globals()["cmd_" + a.cmd](a)

if __name__ == "__main__":
    main()
