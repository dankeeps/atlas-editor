"""Motor de edição do Estúdio (estilo lido de estilos/<nome>/estilo.json).
uso: python3 motor.py <pasta da versão> preview t1 t2 ...        -> prev/quadro_<t>.jpg
     python3 motor.py <pasta da versão> video saida.mp4 [--quadros A:B]
     python3 motor.py <pasta da versão> legendas                  -> grava a lista de legendas padrão no plano
A pasta da versão contém plano.json, jc.mov, masks/, whisper.json."""
import sys, os, json, re, shutil, subprocess, unicodedata, math
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARGS = sys.argv[1:]
def _opcao(nome, padrao=None):
    if nome in ARGS:
        i = ARGS.index(nome); v = ARGS[i + 1]; del ARGS[i:i + 2]; return v
    return padrao
PLANO = _opcao("--plano", "plano.json"); QUADROS = _opcao("--quadros")
PASTA = os.path.abspath(ARGS[0]); os.chdir(PASTA)
P = json.load(open(PLANO))
EST = json.load(open(os.path.join(SKILL, "estilos", P.get("estilo", "ultradinamico"), "estilo.json")))
W, H, FPS = 1080, 1920, 30
from fontes import emoji_img
L_ = EST["letreiro"]; ENTRADA = L_["entrada"]; LARG_MAX = L_["largura_max"]
TR_IN, TR_OUT = EST["transicao"]["entrada"], EST["transicao"]["saida"]
SRC = P["src"]
_pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                      "-of", "csv=p=0", SRC], capture_output=True, text=True).stdout.strip().split(",")
try: SW, SH = int(_pr[0]), int(_pr[1])
except (ValueError, IndexError): SW, SH = W, H     # modo `amostras`: não há vídeo, só os letreiros do estilo

# O recorte é buscado pelo número do quadro: máscara que sobra ou falta sai deslocada da pessoa.
if os.path.isdir(P["masks"]):
    _nm = len([f for f in os.listdir(P["masks"]) if f.endswith(".png")])
    _nq = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=nb_frames",
                          "-of", "csv=p=0", SRC], capture_output=True, text=True).stdout.strip()
    if _nq.isdigit() and _nm != int(_nq):
        sys.exit(f"máscaras não batem com {SRC}: {_nm} máscaras para {_nq} quadros. "
                 f"O jump cut mudou depois do recorte — rode 'estudio.py preparar' antes de renderizar.")

# ---------------------------------------------------------------- texto
_fc = {}
def fonte(tipo, px):
    k = (tipo, px)
    if k not in _fc:
        d = EST["fontes"][tipo]; arq = d["arquivo"] if d["arquivo"].startswith("/") else os.path.join(SKILL, d["arquivo"])
        f = ImageFont.truetype(arq, px, index=d.get("indice", 0))
        if d.get("peso"): f.set_variation_by_axes([d["peso"]])
        _fc[k] = f
    return _fc[k]

ESTILOS = {k: (v["familia"], v["px"], tuple(v["cor"]), v["tracking"], v["efeito"]) for k, v in L_["estilos"].items()}

def texto_mascara(txt, f, track_px, pad):
    xs = [f.getlength(txt[:i]) + i * track_px for i in range(len(txt))]
    larg = int(f.getlength(txt) + (len(txt) - 1) * track_px)
    asc, desc = f.getmetrics()
    m = Image.new("L", (larg + 2 * pad, asc + desc + 2 * pad), 0); d = ImageDraw.Draw(m)
    for ch, x in zip(txt, xs):
        d.text((pad + x, pad + asc), ch, font=f, fill=255, anchor="ls")
    return m, pad + asc

def camada_linha(estilo, txt, emoji, px=None):
    fam, base_px, cor, track, efeito = ESTILOS[estilo]
    px = px or base_px
    f = fonte(fam, px); pad = int(px * 0.35)
    m, base_y = texto_mascara(txt, f, track * px, pad)
    larg = m.width - 2 * pad; emo = None
    if emoji:
        emo = emoji_img(emoji, int(px * (0.80 if fam == "sans" else 0.55)))
        larg += emo.width + int(px * 0.12)
    if larg > W * LARG_MAX:
        return camada_linha(estilo, txt, emoji, int(px * W * LARG_MAX / larg))
    rgba = Image.new("RGBA", m.size, (0, 0, 0, 0))
    def cam(c, alpha, blur, dx=0, dy=0):
        a = m.filter(ImageFilter.GaussianBlur(blur)) if blur else m
        a = a.point(lambda v: int(min(255, v * alpha)))
        l = Image.new("RGBA", m.size, c + (0,)); l.putalpha(a)
        return l.transform(m.size, Image.AFFINE, (1, 0, -dx, 0, 1, -dy)) if (dx or dy) else l
    s = px / 100
    for r, g, b, alpha, blur, dy in L_["efeitos"][efeito]:
        rgba = Image.alpha_composite(rgba, cam((r, g, b), alpha, blur * s, 0, dy * s))
    rgba = Image.alpha_composite(rgba, cam(cor, 1.0, 0))
    if emo:
        full = Image.new("RGBA", (rgba.width + emo.width + int(px * 0.12), rgba.height), (0, 0, 0, 0))
        full.alpha_composite(emo, (pad, int(base_y - emo.height * (0.95 if fam == "sans" else 1.0))))
        full.alpha_composite(rgba, (emo.width + int(px * 0.12), 0)); rgba = full
    return rgba, base_y, px, fam

def montar_blocos():
    out = []
    for b in P["blocos"]:
        linhas = []; y_base = None; prev = None
        for (estilo, txt, t_in, emoji) in b["linhas"]:
            img, base_y, px, fam = camada_linha(estilo, txt, emoji, b.get("tam") if estilo == "branca" else None)
            if y_base is None:
                y_base = b["topo"] * H + px * (0.78 if fam == "sans" else 0.80)
            elif fam == "serif" and prev == "sans":
                y_base += px * 0.74
            elif fam == "serif":
                y_base += px * (0.92 if estilo == "lista" else 0.80)
            elif prev == "serif":
                y_base += px * 1.10
            else:
                y_base += px * 1.0
            linhas.append(dict(img=img, x=int(W / 2 - img.width / 2), y=int(y_base - base_y), t=t_in))
            prev = fam
        out.append(dict(linhas=linhas, fim=b["fim"], atras=b.get("atras", False), ini=min(l["t"] for l in linhas),
                        texto=" ".join(l[1] for l in b["linhas"])))
    return out

def anim_linha(l, t):
    p = (t - l["t"]) / ENTRADA
    if p >= 1: return l["img"], l["x"], l["y"]
    e = 1 - (1 - max(p, 0)) ** 3; img = l["img"]; esc = 1.07 - 0.07 * e
    nw, nh = int(img.width * esc), int(img.height * esc)
    im = img.resize((nw, nh), Image.BILINEAR)
    if (1 - e) * 16 > 0.5: im = im.filter(ImageFilter.GaussianBlur((1 - e) * 16))
    im.putalpha(im.getchannel("A").point(lambda v: int(v * e)))
    return im, l["x"] - (nw - img.width) // 2, l["y"] - (nh - img.height) // 2

# ---------------------------------------------------------------- legenda
def norm(w):
    w = unicodedata.normalize("NFD", w.lower()); w = "".join(c for c in w if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9%]", "", w)

def montar_legenda(blocos):
    r = json.load(open(P["whisper"])); cor = P.get("correcoes", {}); ws = []
    for s in r["segments"]:
        for w in s["words"]:
            txt = w["word"].strip()
            if not txt: continue
            if txt == "%" and ws: ws[-1]["w"] += "%"; ws[-1]["e"] = w["end"]; continue
            ws.append(dict(w=cor.get(txt, txt), t=w["start"], e=w["end"]))
    LG = EST["legenda"]; chunks, cur = [], []
    minp = LG.get("min_palavras", 1); antes = set(LG.get("quebra_antes", []))       # frase natural: antes de "e", "que"…
    ligacao = set(LG.get("nao_termina_em", []))                                      # e nunca termina em "o", "de", "que"…
    if LG.get("segmentacao") == "frases": ws_it = []; chunks = frases(ws, LG, antes, ligacao)
    else: ws_it = ws
    for w in ws_it:
        cruza = LG.get("quebra_no_corte", True) and len(cur) >= LG.get("min_corte", minp) and any(cur[0]["t"] < c <= w["t"] for c in P["cortes"])
        conj = len(cur) >= max(minp, 4) and norm(w["w"]) in antes
        pont = cur and cur[-1]["w"][-1] in ",.?!" and (len(cur) >= minp or cur[-1]["w"][-1] in ".?!")
        cheio = cur and (len(cur) >= LG["max_palavras"] or len(" ".join(x["w"] for x in cur + [w])) > LG["max_caracteres"])
        if cur and (cheio or pont or cruza or conj):
            passa = []
            if not pont:
                while len(cur) > 2 and norm(cur[-1]["w"]) in ligacao: passa.insert(0, cur.pop())
            chunks.append(cur); cur = passa
        cur.append(w)
    if cur: chunks.append(cur)
    out = []
    for i, c in enumerate(chunks):
        ini = c[0]["t"]; fim = chunks[i + 1][0]["t"] if i + 1 < len(chunks) else P["dur"]
        fim = min(fim, c[-1]["e"] + LG["segura"]); pal = [norm(x["w"]) for x in c]; oculto = False
        for b in blocos:
            if b["ini"] - 0.05 <= ini < b["fim"]:
                bt = set(norm(x) for x in b["texto"].split())
                if sum(p in bt for p in pal) * 3 >= len(pal): oculto = True
        legenda = dict(ini=ini, fim=fim, txt=texto_legenda(" ".join(x["w"] for x in c)), oculto=oculto)
        if LG.get("karaoke"):
            legenda["pals"] = [[texto_legenda(x["w"]), round(x["t"], 3), round(x["e"], 3)]
                               for x in c if texto_legenda(x["w"])]
        out.append(legenda)
    return out

def frases(ws, LG, antes, ligacao):
    """Legenda por frase (estilo orgânico): escolhe TODAS as quebras juntas (programação dinâmica), pontuando cada uma:
    pontuação e pausa puxam a quebra; frase perto de `alvo_palavras`; terminar em palavra de ligação ou durar pouco pesa."""
    n = len(ws); alvo = LG.get("alvo_palavras", 6); INF = float("inf")
    def custo(ch, prox):
        c = 0.35 * (len(ch) - alvo) ** 2
        if prox is None: return c
        u = ch[-1]["w"]
        if u[-1] in ".?!": c -= 7
        elif u[-1] in ",;:": c -= 4.5
        c -= min(max(prox["t"] - ch[-1]["e"], 0.0), 0.6) * 8
        if any(ch[-1]["t"] < ct <= prox["t"] for ct in P["cortes"]): c -= 1.5
        if norm(prox["w"]) in antes: c -= 2
        if norm(u) in ligacao: c += 7
        if ch[-1]["e"] - ch[0]["t"] < 0.7: c += 3
        return c
    melhor = [0.0] + [INF] * n; de = [0] * (n + 1)
    for j in range(1, n + 1):
        for i in range(max(0, j - LG["max_palavras"]), j):
            if j - i > 1 and len(" ".join(x["w"] for x in ws[i:j])) > LG["max_caracteres"]: continue
            c = melhor[i] + custo(ws[i:j], ws[j] if j < n else None)
            if c < melhor[j]: melhor[j], de[j] = c, i
    out, j = [], n
    while j > 0: out.insert(0, ws[de[j]:j]); j = de[j]
    return out

def texto_legenda(s):
    """Transformações do estilo aplicadas na hora de gerar (o texto salvo é o que aparece): minúsculas, sem pontuação."""
    LG = EST["legenda"]
    if LG.get("minusculas"): s = s.lower()
    if LG.get("caixa_alta"): s = s.upper()
    if LG.get("sem_pontuacao"): s = re.sub(r"\s+", " ", re.sub(r"(?<!\d)[.,](?!\d)|[!?;:…\"“”]", "", s)).strip()
    return s

def quebra_linhas(txt, f, track_px, larg_max):
    """Quebra gulosa por largura (mesma regra da prévia do editor)."""
    linhas, cur = [], ""
    for w in txt.split():
        prova = (cur + " " + w).strip()
        if cur and f.getlength(prova) + (len(prova) - 1) * track_px > larg_max: linhas.append(cur); cur = w
        else: cur = prova
    if cur: linhas.append(cur)
    return linhas or [""]

def legendas_do_plano(blocos):
    """Usa P["legendas"] (editável no editor). oculto=None => automático (some quando o letreiro já mostra as palavras)."""
    out = []
    for lg in P["legendas"]:
        pal = [norm(x) for x in lg["txt"].split()]; auto = False
        for b in blocos:
            if b["ini"] - 0.05 <= lg["ini"] < b["fim"]:
                bt = set(norm(x) for x in b["texto"].split())
                if pal and sum(p in bt for p in pal) * 3 >= len(pal): auto = True
        oc = lg.get("oculto"); out.append(dict(lg, oculto=auto if oc is None else bool(oc)))
    return out

def pals_legenda(lg):
    """Ignore timings made stale by a manual text edit, split, or moved caption.

    The stored text remains authoritative. Never draw old words from `pals`
    over newer text, or guess timings for newly written words.
    """
    pals = lg.get("pals")
    if not isinstance(pals, list) or not pals: return None
    try:
        ini, fim = float(lg["ini"]), float(lg["fim"])
        if not math.isfinite(ini) or not math.isfinite(fim) or fim <= ini: return None
        anterior = -math.inf
        for pal in pals:
            if not isinstance(pal, (list, tuple)) or len(pal) != 3: return None
            w, a, b = pal
            if not isinstance(w, str) or not w.strip(): return None
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (a, b)): return None
            if a < anterior or b <= a or a < ini - 0.06 or b > fim + 0.06: return None
            anterior = a
        if " ".join(str(lg.get("txt", "")).split()) != " ".join(" ".join(p[0].split()) for p in pals): return None
    except (TypeError, ValueError, KeyError):
        return None
    return pals


def palavra_ativa(pals, t):
    """Prefer the spoken interval; short padding only bridges a tiny silence."""
    return next((j for j, (_, a, b) in enumerate(pals) if a <= t < b),
                next((j for j, (_, a, b) in enumerate(pals) if a - 0.02 <= t < b + 0.04), -1))


def camada_legenda_karaoke(pals, t):
    """Original Simples Dinâmico word layout, with a single cyan active word."""
    if not pals: return camada_legenda("")
    E = EST["legenda"]; tam = int(P.get("legenda_pos", {}).get("tam", E["tam"])); tr = E["tracking"] * tam
    cor_on = tuple(E.get("destaque", [33, 210, 219])); f = fonte(E["familia"], tam); pad = E["pad"]
    esp = f.getlength(" ") + tr; limite = W * E.get("largura_max", 0.9)
    linhas, cur, larg = [], [], 0.0
    for j, (w, a, b) in enumerate(pals):
        lw = f.getlength(w) + tr * max(0, len(w) - 1)
        if cur and larg + esp + lw > limite: linhas.append(cur); cur, larg = [], 0.0
        cur.append((w, j, lw)); larg += (esp if len(cur) > 1 else 0) + lw
    if cur: linhas.append(cur)
    lh = int(tam * E.get("entrelinha", 1.25)); asc, desc = f.getmetrics()
    larguras = [sum(x[2] for x in linha) + esp * (len(linha) - 1) for linha in linhas]
    largura = int(max(larguras)) + 2 * pad; altura = asc + desc + 2 * pad + lh * (len(linhas) - 1)
    m = Image.new("L", (largura, altura), 0); dm = ImageDraw.Draw(m)
    mc = Image.new("L", (largura, altura), 0); dc = ImageDraw.Draw(mc)
    base_y = pad + asc; ativa = palavra_ativa(pals, t)
    for k, linha in enumerate(linhas):
        x = (largura - larguras[k]) / 2; y = base_y + k * lh
        for w, j, lw in linha:
            alvo = dc if j == ativa else dm
            for h, ch in enumerate(w):
                alvo.text((x + f.getlength(w[:h]) + tr * h, y), ch, font=f, fill=255, anchor="ls")
            x += lw + esp
    tudo = ImageChops.lighter(m, mc)
    rgba = Image.new("RGBA", (largura, altura), (0, 0, 0, 0))
    for r, g, b, alpha, blur, dy in E["sombras"]:
        a = tudo.filter(ImageFilter.GaussianBlur(blur)).point(lambda v, al=alpha: int(min(255, v * al)))
        c = Image.new("RGBA", tudo.size, (r, g, b, 0)); c.putalpha(a)
        if dy: c = c.transform(tudo.size, Image.AFFINE, (1, 0, 0, 0, 1, -dy))
        rgba = Image.alpha_composite(rgba, c)
    if E.get("contorno"):
        r, g, b, alpha, px = E["contorno"]
        a = tudo.filter(ImageFilter.MaxFilter(2 * int(px) + 1)).filter(ImageFilter.GaussianBlur(0.6)).point(lambda v: int(min(255, v * alpha)))
        c = Image.new("RGBA", tudo.size, (r, g, b, 0)); c.putalpha(a); rgba = Image.alpha_composite(rgba, c)
    br = Image.new("RGBA", tudo.size, (255, 255, 255, 0)); br.putalpha(m); rgba = Image.alpha_composite(rgba, br)
    on = Image.new("RGBA", tudo.size, cor_on + (0,)); on.putalpha(mc)
    return Image.alpha_composite(rgba, on), base_y


def camada_da_legenda(lg, t, cache):
    pals = pals_legenda(lg) if EST["legenda"].get("karaoke") else None
    if pals:
        key = ("karaoke", tuple(p[0] for p in pals), palavra_ativa(pals, t))
        if key not in cache: cache[key] = camada_legenda_karaoke(pals, t)
    else:
        key = lg["txt"]
        if key not in cache: cache[key] = camada_legenda(lg["txt"])
    return cache[key]


def camada_legenda(txt):
    """Legenda: uma ou mais linhas (quebra em largura_max), centralizadas; camadas = sombras, contorno, texto branco.
    Devolve a imagem e a linha de base da PRIMEIRA linha (as outras descem a partir dela)."""
    E = EST["legenda"]; tam = int(P.get("legenda_pos", {}).get("tam", E["tam"])); tr = E["tracking"] * tam
    f = fonte(E["familia"], tam); pad = E["pad"]
    linhas = quebra_linhas(txt, f, tr, W * E.get("largura_max", 0.9)); lh = int(tam * E.get("entrelinha", 1.25))
    mk = [texto_mascara(l, f, tr, pad) for l in linhas]; base_y = mk[0][1]
    larg = max(x[0].width for x in mk); m = Image.new("L", (larg, mk[0][0].height + lh * (len(mk) - 1)), 0)
    for k, (ml, _) in enumerate(mk): m.paste(ml, ((larg - ml.width) // 2, k * lh), ml)
    rgba = Image.new("RGBA", m.size, (0, 0, 0, 0))
    for r, g, b, alpha, blur, dy in E["sombras"]:
        a = m.filter(ImageFilter.GaussianBlur(blur)).point(lambda v, al=alpha: int(min(255, v * al)))
        c = Image.new("RGBA", m.size, (r, g, b, 0)); c.putalpha(a)
        if dy: c = c.transform(m.size, Image.AFFINE, (1, 0, 0, 0, 1, -dy))
        rgba = Image.alpha_composite(rgba, c)
    if E.get("contorno"):                                 # [r, g, b, alfa, espessura px]
        r, g, b, alpha, px = E["contorno"]
        a = m.filter(ImageFilter.MaxFilter(2 * int(px) + 1)).filter(ImageFilter.GaussianBlur(0.6)).point(lambda v: int(min(255, v * alpha)))
        c = Image.new("RGBA", m.size, (r, g, b, 0)); c.putalpha(a); rgba = Image.alpha_composite(rgba, c)
    c = Image.new("RGBA", m.size, (255, 255, 255, 0)); c.putalpha(m)
    return Image.alpha_composite(rgba, c), base_y

# ---------------------------------------------------------------- apresentador
def zoom_em(t):
    """Valor do zoom em t. Ponto com "suave" = o zoom caminha até ele (movimento lento dentro da cena);
    sem marca = degrau seco (o punch do letreiro). Planos antigos, só com [t, z], seguem funcionando."""
    z = pz = 1.0; pt = 0.0
    for ev in P["zoom"]:
        tt, zz = ev[0], ev[1]; suave = len(ev) > 2 and ev[2] == "suave"
        if t + 1e-6 >= tt: z = pz = zz; pt = tt; continue
        if suave and tt > pt:
            e = (t - pt) / (tt - pt); e = e * e * (3 - 2 * e); z = pz + (zz - pz) * e
        break
    for a, b in P.get("escuro", []):
        if a <= t < b: z *= 1 + EST["zoom"]["escuro_push"] * (t - a) / (b - a)
    return z

def caixa(z):
    ax, ay = P.get("ancora", EST["zoom"]["ancora"])
    return (ax * SW * (1 - 1 / z), ay * SH * (1 - 1 / z), ax * SW * (1 - 1 / z) + SW / z, ay * SH * (1 - 1 / z) + SH / z)

def intensidade(t, faixas, fin=0.17, fout=0.10):
    for a, b in faixas:
        if a <= t < b: return min(1, (t - a) / fin, (b - t) / fout if b < P["dur"] - 0.01 else 1)
    return 0.0

yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
_e = EST["escuro"]
HOLOFOTE = (_e["piso"] + (1 - _e["piso"]) * np.exp(-(((xx - _e["centro"][0] * W) / (_e["raio"][0] * W)) ** 2 + ((yy - _e["centro"][1] * H) / (_e["raio"][1] * H)) ** 2)))[..., None]

def mascara(i, box):
    p = os.path.join(P["masks"], f"f{i:05d}.png")
    if not os.path.exists(p): return None
    m = Image.open(p).convert("L"); sx, sy = m.width / SW, m.height / SH
    m = m.resize((W, H), Image.BILINEAR, box=(box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy))
    return np.asarray(m, dtype=np.float32)[..., None] / 255.0

# ---------------------------------------------------------------- B-roll
def _rounded(w, h, r):
    m = Image.new("L", (w, h), 0); ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], r, fill=255); return m

class Leitor:
    """Lê quadros de um B-roll (vídeo ou imagem) já no tamanho pedido (cover), em loop."""
    def __init__(self, c, w, h, extra=0.0):
        self.w, self.h = w, h; self.img = None; self.p = None
        src = c["src"]; crop = c.get("crop")
        if src.lower().endswith((".jpg", ".jpeg", ".png")):
            im = Image.open(src).convert("RGB")
            if crop: im = im.crop((crop[0], crop[1], crop[0] + crop[2], crop[1] + crop[3]))
            if c.get("encaixe") == "contain":
                self.img = im.resize((w, h), Image.LANCZOS)
            else:
                s = max(w / im.width, h / im.height); im = im.resize((int(im.width * s + 0.5), int(im.height * s + 0.5)), Image.LANCZOS)
                self.img = im.crop(((im.width - w) // 2, (im.height - h) // 2, (im.width - w) // 2 + w, (im.height - h) // 2 + h))
        else:
            vf = (f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}," if crop else "")
            vf += f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},fps={FPS}"
            vel = c.get("vel", 1.0)
            if vel != 1.0: vf = f"setpts=PTS/{vel}," + vf
            self.p = subprocess.Popen(["ffmpeg", "-v", "error", "-stream_loop", "-1", "-ss", f"{c.get('src_ini', 0) + extra * c.get('vel', 1.0):.3f}", "-i", src,
                                       "-an", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE, bufsize=w * h * 3 * 2)
            self.ult = None
    def prox(self):
        if self.img is not None: return self.img
        buf = self.p.stdout.read(self.w * self.h * 3)
        if len(buf) == self.w * self.h * 3:
            self.ult = Image.frombuffer("RGB", (self.w, self.h), buf, "raw", "RGB", 0, 1)
        return self.ult
    def fechar(self):
        if self.p:
            self.p.stdout.close(); self.p.kill(); self.p.wait()

def tam_card(c):
    if c["src"].lower().endswith((".jpg", ".jpeg", ".png")):
        im = Image.open(c["src"]); w, h = im.size
    else:
        pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                             "-of", "csv=p=0", c["src"]], capture_output=True, text=True).stdout.strip().split(",")
        w, h = int(pr[0]), int(pr[1])
    if c.get("crop"): w, h = c["crop"][2], c["crop"][3]
    asp = c.get("aspecto") or (w / h)
    K = EST["card"]
    asp = max(asp, K.get("aspecto_min", 1.0))        # card nunca em pé: clipe vertical entra quadrado (recorte central)
    cw = min(vaga_card(c), H * K["altura_max"] * asp); ch = cw / asp
    return int(cw) // 2 * 2, int(ch) // 2 * 2

def vaga_card(c):
    """Largura da vaga do card: sozinho = largura_max; em fila [i, n] = a fila dividida em n vagas iguais."""
    K = EST["card"]; fl = c.get("fila")
    if not fl or fl[1] <= 1: return W * K["largura_max"]
    n = fl[1]; return min(W * K["largura_max"], (W * K.get("fila_largura", 0.92) - (n - 1) * W * K.get("fila_gap", 0.025)) / n)

def pos_card(c, cw, ch):
    """Canto superior esquerdo do card: centralizado, ou na vaga i de n (fila centralizada, cards alinhados pelo topo)."""
    K = EST["card"]; y = int(c.get("topo", K["topo"]) * H); fl = c.get("fila")
    if not fl or fl[1] <= 1: return (W - cw) // 2, y
    i, n = fl; vw = vaga_card(c); gap = W * K.get("fila_gap", 0.025); x0 = (W - (n * vw + (n - 1) * gap)) / 2
    return int(x0 + (i - 1) * (vw + gap) + (vw - cw) / 2), y

# ---------------------------------------------------------------- efeitos
def blur_h(a, px):
    if px < 2: return a
    offs = np.linspace(-px / 2, px / 2, 7).astype(int)
    return np.mean([np.roll(a, o, axis=1) for o in offs], axis=0)

def glitch(a, k, forca=1.0):
    rng = np.random.default_rng(k + 7); d = int((14 + 6 * (k % 2)) * forca)
    a = a.copy(); a[..., 0] = np.roll(a[..., 0], d, axis=1); a[..., 2] = np.roll(a[..., 2], -d, axis=1)
    for _ in range(5):
        y0 = int(rng.integers(0, H - 80)); hg = int(rng.integers(12, 70))
        a[y0:y0 + hg] = np.roll(a[y0:y0 + hg], int(rng.integers(-60, 60) * forca), axis=1)
    return np.clip(a + rng.normal(0, 18 * forca, (H, W, 1)).astype(np.float32), 0, 255)

def escala_centro(a, s):
    """Zoom digital em torno do centro (s>1 aproxima)."""
    im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    cw, ch = W / s, H / s; x0, y0 = (W - cw) / 2, (H - ch) / 2
    return np.asarray(im.resize((W, H), Image.BILINEAR, box=(x0, y0, x0 + cw, y0 + ch)), dtype=np.float32)

def transicao(A, B, p, tipo, k):
    """Mistura A->B com p em [0,1]."""
    e = p * p * (3 - 2 * p)
    if tipo == "whip":
        off = int(e * W); vel = abs(6 * p * (1 - p)) * 180
        out = np.empty_like(A)
        out[:, :W - off] = A[:, off:] if off < W else 0
        out[:, W - off:] = B[:, :off] if off > 0 else out[:, W - off:]
        return blur_h(out, vel)
    if tipo == "zoom":
        sa = escala_centro(A, 1 + 0.6 * e); sb = escala_centro(B, 1.3 - 0.3 * e)
        m = sa * (1 - e) + sb * e
        fl = max(0, 1 - abs(p - 0.5) * 4) * 0.55
        return m * (1 - fl) + 255 * fl
    if tipo == "glitch":
        base = A if p < 0.5 else B
        return glitch(base, k, 1.4 * (1 - abs(p - 0.5) * 1.6))
    if tipo == "flash":
        base = A if p < 0.5 else B; fl = max(0, 1 - abs(p - 0.5) * 2.2)
        return base * (1 - fl) + 255 * fl
    if tipo == "slide":
        off = int((1 - e) * H); out = A.copy()
        out[off:] = B[:H - off] if off < H else out[off:]
        return out
    if tipo == "fade": return A * (1 - e) + B * e
    return B

def compor_canto(pres, m, bg, lado, s_prog, busto=None):
    """Apresentador recortado no canto, B-roll atrás. s_prog: 0 (tela cheia) -> 1 (no canto).
    busto = [x, y, w, h]: recorte da cintura para cima, para ela aparecer maior em vez de um bonequinho de corpo
    inteiro. A caixa (e o tamanho no canto) caminham junto com s_prog, então não há salto no primeiro quadro."""
    K = EST["canto"]; e = s_prog
    if busto:
        bx0, by0, bw0, bh0 = [float(v) for v in busto]
        rx, ry = bx0 * e, by0 * e                                   # a caixa fecha da tela inteira até o busto
        rw, rh = W * (1 - e) + bw0 * e, H * (1 - e) + bh0 * e
        tw_f = W * K["escala"]; th_f = tw_f * bh0 / max(1.0, bw0)   # mesma largura de sempre no canto, altura pela caixa
        tw, th = int(W * (1 - e) + tw_f * e), int(H * (1 - e) + th_f * e)
    else:
        S = K["escala"] + (1 - K["escala"]) * (1 - e)
        rx, ry, rw, rh = 0.0, 0.0, float(W), float(H); tw, th = int(W * S), int(H * S)
    cx_alvo = K["x"][0] * W if lado == "esq" else K["x"][1] * W
    x0 = int((W / 2) * (1 - e) + cx_alvo * e - tw / 2); y0 = int(H - th)
    eb = min(1.0, e * 2.5)
    fundo = bg * eb + pres * (1 - eb)
    corta = (int(rx), int(ry), int(rx + rw), int(ry + rh))
    pi = Image.fromarray(np.clip(pres, 0, 255).astype(np.uint8)).resize((tw, th), Image.BILINEAR, box=corta)
    mi = Image.fromarray((np.clip(m[..., 0], 0, 1) * 255).astype(np.uint8)).resize((tw, th), Image.BILINEAR, box=corta)
    if K.get("borda_suave"): mi = mi.filter(ImageFilter.GaussianBlur(K["borda_suave"]))   # borda macia, sem serrilhado
    contorno = mi.filter(ImageFilter.MaxFilter(K["contorno"])) if K.get("contorno") else None
    sombra = mi.filter(ImageFilter.GaussianBlur(K["sombra_blur"]))
    out = Image.fromarray(np.clip(fundo, 0, 255).astype(np.uint8)).convert("RGBA")
    L = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sh = Image.new("RGBA", (tw, th), (0, 0, 0, 0)); sh.putalpha(sombra.point(lambda v: int(v * K["sombra_alpha"] * e)))
    L.alpha_composite(sh, (max(0, x0 + 10), y0 + 12) if x0 + 10 >= 0 else (0, y0 + 12), (0 if x0 + 10 >= 0 else -(x0 + 10), 0))
    pp = pi.convert("RGBA"); pp.putalpha(mi.point(lambda v: int(v * e)) if e < 1 else mi)
    camadas = [pp]
    if contorno is not None:
        ct = Image.new("RGBA", (tw, th), (255, 255, 255, 0)); ct.putalpha(contorno.point(lambda v: int(v * e)))
        camadas.insert(0, ct)
    for camada in camadas:
        cx = max(0, x0); ox = max(0, -x0)
        L.alpha_composite(camada, (cx, y0), (ox, 0))
    out = Image.alpha_composite(out, L)
    return np.asarray(out.convert("RGB"), dtype=np.float32)

def caixa_busto(c):
    """Janela do apresentador para o canto: da cabeça até a CINTURA, na proporção da tela. Não é um recorte
    da silhueta colado no rodapé — é o vídeo inteiro reposicionado e aproximado dentro do bloco, então não
    existe corte reto no tronco: o que sai de quadro sai pela borda, como em qualquer enquadramento.

    O topo vem da máscara e a cintura da pose (Vision), medidos em 7 quadros da cena porque ela se mexe.
    Fica no plano (c["busto"] = [x, y, w, h, ini, fim]) pelo modo `cabecas`; o editor lê o mesmo número."""
    b = c.get("busto")
    if isinstance(b, list) and len(b) == 6 and abs(b[4] - c["ini"]) < 0.01 and abs(b[5] - c["fim"]) < 0.01: return b[:4]
    K = EST["canto"]; topos, centros, amostras = [], [], []
    for k in range(7):
        t = c["ini"] + (c["fim"] - c["ini"]) * (k + 0.5) / 7; i = int(round(t * FPS)); box = caixa(zoom_em(i / FPS))
        m = mascara(i, box)
        if m is None: continue
        a = m[..., 0] > 0.5
        linhas = np.where(a.any(axis=1))[0]
        if len(linhas) < 10: continue
        y_topo = int(linhas[0])
        cols = np.where(a[y_topo:y_topo + int(0.45 * H)].any(axis=0))[0]      # centro pelo tronco, não pelos pés
        if len(cols) < 10: continue
        topos.append(y_topo); centros.append(float(cols[0] + cols[-1]) / 2); amostras.append((i, box))
    if not topos: return None
    y = float(np.median(topos)) - K.get("ar_cabeca", 0.02) * H                # um respiro acima da cabeça
    y = float(np.clip(y, 0, H * K.get("topo_max", 0.5)))
    cint, pesc, omb = juntas(amostras)
    if cint is not None: h = cint + K.get("folga_cintura", 0.05) * H - y      # fecha na cintura
    elif pesc is not None: h = (pesc - y) * K.get("cintura_por_pescoco", 2.1)  # pose sem quadril: pela altura do busto
    else: h = H - y                                                           # sem pose: como antes, até a base
    if omb: h = max(h, omb * (1 + K.get("margem_ombros", 0.35)) * H / W)      # os ombros têm que caber na largura
    h = float(np.clip(h, K.get("altura_min", 0.4) * H, H - y))
    if os.environ.get("DEBUG_BUSTO"):
        print(f"  [dbg] topo={np.median(topos):.0f} cint={cint and round(cint)} omb={omb and round(omb)} h={h:.0f}", file=sys.stderr)
    w = h * W / H                                                             # mesma proporção da tela: sem distorcer
    if w > W: h, w = H, W
    x = float(np.clip(np.median(centros) - w / 2, 0, max(0, W - w)))
    return [round(x), round(y), round(w), round(h)]

def juntas(amostras):
    """Cintura, pescoço e largura dos ombros (em pixels da tela) pela pose ONNX nos quadros pedidos.
    amostras = [(quadro, caixa do zoom)]. Quando há gente ao fundo, fica a pessoa de maior tronco."""
    if not amostras: return None, None, None
    import tempfile
    d = tempfile.mkdtemp(prefix="pose")
    try:
        for n, (i, box) in enumerate(amostras):
            x0, y0, x1, y1 = [int(round(v)) for v in box]
            subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{i / FPS:.3f}", "-i", SRC, "-frames:v", "1", "-vf",
                            f"crop={x1 - x0}:{y1 - y0}:{x0}:{y0},scale=540:960", os.path.join(d, f"{n:03d}.jpg")])
        from pose import juntas_pasta
        return juntas_pasta(d, W, H)
    except Exception:
        return None, None, None
    finally:
        shutil.rmtree(d, ignore_errors=True)

def topo_cabeca(c):
    """Onde começa o recorte do apresentador na tela dividida: mediana do topo da cabeça em 7 quadros da cena (a pessoa
    se mexe; o 1º quadro sozinho engana). Fica gravado no plano (c["y0"] = [valor, ini, fim]) pelo modo `cabecas`, e o
    editor usa o mesmo número. Determinístico: igual em qualquer pedaço do render paralelo."""
    y = c.get("y0")
    if isinstance(y, list) and len(y) == 3 and abs(y[1] - c["ini"]) < 0.01 and abs(y[2] - c["fim"]) < 0.01: return y[0]
    vals = []
    for k in range(7):
        t = c["ini"] + (c["fim"] - c["ini"]) * (k + 0.5) / 7; i = int(round(t * FPS)); m = mascara(i, caixa(zoom_em(i / FPS)))
        if m is None: continue
        col = m[:, int(0.35 * W):int(0.65 * W), 0].max(axis=1); linhas = np.where(col > 0.5)[0]
        if len(linhas): vals.append(linhas[0])
    if not vals: return None
    return int(np.clip(np.median(vals) - EST["dividida"]["folga_cabeca"] * H, 0, H // 2))

def faixa_emenda(c):
    """Altura (px) da faixa em que as duas imagens se dissolvem. 0 = emenda reta, sem nada."""
    K = EST["dividida"]
    if c.get("emenda") == "reto": return 0
    return int(c.get("emenda_px", K.get("fusao", 90)))

def compor_dividida(pres, topo, prog, y0=None, inverte=False, f=0):
    """Tela dividida: B-roll numa metade, apresentador (rosto) na outra. prog 0->1 desliza o painel do B-roll.
    inverte=True troca os lados. f = faixa em que uma imagem some na outra (o B-roll vem com f linhas a mais)."""
    out = pres.copy()
    y0 = int(0.13 * H) if y0 is None else int(y0)   # recorte do apresentador a partir do topo da cabeça
    meio = H // 2; off = int((1 - prog) * meio)
    if inverte:
        out[:meio] = pres[y0:y0 + meio]
        if prog < 1: out[meio:] = pres[meio:] * EST["dividida"]["escurece_antes"]
        if meio - off > 0: out[meio + off:] = topo[:meio - off]        # o B-roll sobe de baixo
    else:
        out[meio:] = pres[y0:y0 + meio]
        if prog < 1: out[:meio] = pres[:meio] * EST["dividida"]["escurece_antes"]
        out[:meio - off] = topo[off:meio]                              # o B-roll desce de cima
    if f >= 2 and prog >= 1: dissolve(out, pres, topo, meio, y0, f, inverte)
    return out

def dissolve(out, pres, topo, meio, y0, f, inverte):
    """Na faixa de 2f linhas em volta da emenda, uma imagem some na outra — com imagem de verdade dos dois lados:
    o B-roll continua além da metade (o leitor traz linhas a mais) e a pessoa continua acima do corte."""
    a, b = meio - f, meio + f
    if a < 0 or b > H: return
    if inverte:
        cima = pres[y0 + a:y0 + b]                     # a pessoa segue abaixo da metade
        baixo = topo[:2 * f] if topo.shape[0] >= 2 * f else None
        if baixo is None: return
        baixo = np.concatenate([np.zeros((0, W, 3), np.float32), baixo])[:2 * f]
        alvo_cima, alvo_baixo = cima, baixo
    else:
        alvo_cima = topo[meio - f:meio + f] if topo.shape[0] >= meio + f else None
        if alvo_cima is None: return
        alvo_baixo = pres[y0 - f:y0 + f] if y0 - f >= 0 else None
        if alvo_baixo is None: return
    p = np.linspace(0, 1, 2 * f, dtype=np.float32)[:, None, None]; p = p * p * (3 - 2 * p)
    out[a:b] = np.clip(alvo_cima * (1 - p) + alvo_baixo * p, 0, 255)

# ---------------------------------------------------------------- quadro
class Estado:
    def __init__(self):
        self.blocos = montar_blocos()
        self.leg = legendas_do_plano(self.blocos) if P.get("legendas") else montar_legenda(self.blocos); self.leg_cache = {}
        self.cenas = sorted(P.get("cenas", []), key=lambda c: c["ini"]); self.leitores = {}
        for c in self.cenas:
            if c["tipo"] == "card":
                c["_cw"], c["_ch"] = tam_card(c)
                K = EST["card"]; r = int(min(c["_cw"], c["_ch"]) * K["raio"]); c["_mask"] = _rounded(c["_cw"], c["_ch"], r)
                pad = 50; sh = Image.new("L", (c["_cw"] + 2 * pad, c["_ch"] + 2 * pad), 0); sh.paste(c["_mask"], (pad, pad + 10))
                sh = sh.filter(ImageFilter.GaussianBlur(K["sombra_blur"])).point(lambda v: int(v * K["sombra_alpha"]))
                so = Image.new("RGBA", sh.size, (0, 0, 0, 0)); so.putalpha(sh); c["_sombra"] = so

    def leitor(self, c, t=None):
        k = id(c)
        if k not in self.leitores:
            ex = max(0.0, (t if t is not None else c["ini"]) - c["ini"])   # começa no quadro certo mesmo no meio da cena
            ex = round(ex * FPS) / FPS
            if c["tipo"] == "card": self.leitores[k] = Leitor(c, c["_cw"], c["_ch"], ex)
            elif c["tipo"] == "dividida": self.leitores[k] = Leitor(c, W, H // 2 + faixa_emenda(c), ex)
            else: self.leitores[k] = Leitor(c, W, H, ex)
        return self.leitores[k]

    def liberar(self, t):
        for c in self.cenas:
            if c["fim"] + 0.1 < t and id(c) in self.leitores:
                self.leitores.pop(id(c)).fechar()

def quadro(i, src, E):
    t = i / FPS; z = zoom_em(t); box = caixa(z)
    a = np.asarray(src.resize((W, H), Image.BICUBIC, box=box), dtype=np.float32)
    ativos = [c for c in E.cenas if c["ini"] <= t < c["fim"]]
    kpb = intensidade(t, P.get("pb", []), EST["pb"]["entra"], EST["pb"]["sai"])
    if kpb > 0:
        g = (a @ np.array([0.299, 0.587, 0.114], np.float32))[..., None]; g = np.clip((g - 128) * EST["pb"]["contraste"] + 128, 0, 255)
        a = a * (1 - kpb) + np.repeat(g, 3, 2) * kpb
    kesc = intensidade(t, P.get("escuro", []), EST["escuro"]["entra"], EST["escuro"]["sai"])
    precisa_m = kesc > 0 or bool(ativos) or any(b["atras"] and b["ini"] <= t < b["fim"] for b in E.blocos)
    m = mascara(i, box) if precisa_m else None
    if kesc > 0 and m is not None:
        a = a * (1 - kesc) + a * (m + (1 - m) * HOLOFOTE * EST["escuro"]["forca"]) * (0.96 + 0.04 * m) * kesc
    pres = a
    lp = P.get("legenda_pos", {}); modo = "normal"; div_inv = False; leg_xy = (lp.get("x", EST["legenda"]["x"]), lp.get("y", EST["legenda"]["y"]))
    cards = []
    for c in ativos:
        tp = c["tipo"]; fr = E.leitor(c, t).prox()
        if tp == "card":
            cards.append((c, fr)); continue
        B = np.asarray(fr, dtype=np.float32)
        p_in = (t - c["ini"]) / TR_IN; p_out = (c["fim"] - t) / TR_OUT
        if tp == "cheia":
            dur = max(0.1, c["fim"] - c["ini"])
            B = escala_centro(B, 1.0 + EST["cheia"]["push"] * (t - c["ini"]) / dur)
            if c.get("pb"): B = np.repeat((B @ np.array([0.299, 0.587, 0.114], np.float32))[..., None], 3, 2)
            if c.get("escurecer"): B = B * c["escurecer"]
            if p_in < 1 and c.get("trans", "whip") != "corte":
                a = transicao(pres, B, max(p_in, 0), c.get("trans", "whip"), i)
            elif p_out < 1 and c.get("saida", "corte") != "corte":
                a = transicao(B, pres, 1 - max(p_out, 0), c["saida"], i)
            else:
                a = B
            modo = "cheia"
        elif tp == "canto" and m is not None:
            if c.get("trans") == "corte": p_in = 1
            if c.get("saida") == "corte": p_out = 1
            prog = min(1, max(0, p_in)) if p_in < 1 else (min(1, max(0, p_out)) if p_out < 1 else 1)
            prog = prog * prog * (3 - 2 * prog)
            if "_bu" not in c: c["_bu"] = caixa_busto(c) if EST["canto"].get("enquadra", "busto") == "busto" else None
            a = compor_canto(pres, m, B * c.get("escurecer", EST["canto"]["escurecer"]), c.get("lado", "esq"), prog, c["_bu"])
            LE = EST["legenda"]; modo = "canto"
            leg_xy = (LE["canto_x"][0] if c.get("lado", "esq") == "esq" else LE["canto_x"][1], float(np.clip(leg_xy[1] + LE["canto_dy"], 0.05, 0.95)))
        elif tp == "dividida":
            if c.get("trans") == "corte": p_in = 1
            if c.get("saida") == "corte": p_out = 1
            prog = min(1, max(0, p_in)) if p_in < 1 else (min(1, max(0, p_out)) if p_out < 1 else 1)
            if c.get("pb"): B = np.repeat((B @ np.array([0.299, 0.587, 0.114], np.float32))[..., None], 3, 2)
            prog = 1 - (1 - prog) ** 3
            if "_y0" not in c: c["_y0"] = topo_cabeca(c)
            a = compor_dividida(pres, B, prog, c.get("_y0"), c.get("inverte", False), faixa_emenda(c))
            div_inv = bool(c.get("inverte"))
            dy = EST["legenda"]["dividida_dy"] * (-1 if div_inv else 1)             # invertida: a legenda sobe
            modo = "dividida"; leg_xy = (leg_xy[0], float(np.clip(leg_xy[1] + dy, 0.05, 0.95)))
    base = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGBA")
    out = base.copy()
    if cards and modo == "normal":
        for c, fr in cards:
            im = fr.convert("RGBA"); im.putalpha(c["_mask"])
            cw, ch = c["_cw"], c["_ch"]; x, y = pos_card(c, cw, ch)
            L = Image.new("RGBA", (W, H), (0, 0, 0, 0)); p = (t - c["ini"]) / EST["card"]["entrada"]
            if p < 1:
                e = 1 - (1 - max(p, 0)) ** 3; esc = 0.90 + 0.10 * e
                nw, nh = int(cw * esc), int(ch * esc); im2 = im.resize((nw, nh), Image.BILINEAR)
                if (1 - e) * 10 > 0.5: im2 = im2.filter(ImageFilter.GaussianBlur((1 - e) * 10))
                im2.putalpha(im2.getchannel("A").point(lambda v: int(v * e)))
                L.alpha_composite(im2, (x + (cw - nw) // 2, y + (ch - nh) // 2))
            else:
                if EST["card"]["sombra_alpha"] > 0: L.alpha_composite(c["_sombra"], (x - 50, y - 50))
                L.alpha_composite(im, (x, y))
            out = Image.alpha_composite(out, L)
        if m is not None and EST["card"].get("pessoa_na_frente", True):
            o = np.asarray(out, dtype=np.float32); bs = np.asarray(base, dtype=np.float32)
            mm = np.clip((m - 0.15) / 0.7, 0, 1); out = Image.fromarray((o * (1 - mm) + bs * mm).astype(np.uint8), "RGBA")
    for b in E.blocos:
        if not (b["ini"] <= t < b["fim"]): continue
        L = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dyb = H // 2 if div_inv else 0                     # dividida invertida: o letreiro vai para o lado do B-roll
        for l in b["linhas"]:
            if t < l["t"]: continue
            im, x, y = anim_linha(l, t); y += dyb
            L.alpha_composite(im, (max(0, x), max(0, y)), (max(0, -x), max(0, -y)))
        out = Image.alpha_composite(out, L)
        if b["atras"] and m is not None and modo == "normal":
            o = np.asarray(out, dtype=np.float32); bs = np.asarray(base, dtype=np.float32)
            mm = np.clip((m - 0.15) / 0.7, 0, 1); out = Image.fromarray((o * (1 - mm) + bs * mm).astype(np.uint8), "RGBA")
    for lg in E.leg:
        if lg["oculto"] or not (lg["ini"] <= t < lg["fim"]): continue
        im, by = camada_da_legenda(lg, t, E.leg_cache)
        lx = lg.get("x") if lg.get("x") is not None else leg_xy[0]; ly = lg.get("y") if lg.get("y") is not None else leg_xy[1]
        x0 = int(np.clip(W * lx - im.width / 2, 0, W - im.width)); out.alpha_composite(im, (x0, int(H * ly - by)))
    rgb = np.asarray(out.convert("RGB"), dtype=np.float32)
    for g0, gd in P.get("glitch", []):
        if g0 <= t < g0 + gd: rgb = glitch(rgb, int((t - g0) * FPS))
    for f0 in P.get("flash", []):
        if f0 <= t < f0 + EST["flash"]["duracao"]: rgb = np.clip(rgb * 1.18 + 18, 0, 255)
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))

def ler_quadros(inicio=0):
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{inicio / FPS:.5f}", "-i", SRC, "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE, bufsize=SW * SH * 3 * 2)
    i = inicio
    while True:
        buf = p.stdout.read(SW * SH * 3)
        if len(buf) < SW * SH * 3: break
        yield i, Image.frombuffer("RGB", (SW, SH), buf, "raw", "RGB", 0, 1); i += 1
    p.stdout.close(); p.kill(); p.wait()

if __name__ == "__main__":
    modo = ARGS[1]
    if modo == "cabecas":                                 # grava o recorte da tela dividida no plano (render e editor usam o mesmo)
        n = 0
        for c in P.get("cenas", []):
            if c["tipo"] == "dividida":
                v = topo_cabeca(dict(c, y0=None))
                if v is not None: c["y0"] = [int(v), c["ini"], c["fim"]]; n += 1
            elif c["tipo"] == "canto" and EST["canto"].get("enquadra", "busto") == "busto":
                b = caixa_busto(dict(c, busto=None))
                if b: c["busto"] = [*b, c["ini"], c["fim"]]; n += 1
        json.dump(P, open(PLANO, "w"), ensure_ascii=False, indent=1); print(n, "recortes medidos"); sys.exit()
    if modo == "amostras":                                # amostra dos letreiros do estilo, para a aba Templates
        pares = json.loads(ARGS[2]); saida = ARGS[3]
        linhas = []
        for e, t, em in pares:
            img = camada_linha(e, t, em)[0]
            bb = img.getbbox()                            # sem o vazio da máscara: a tira fica compacta
            linhas.append([img.crop(bb) if bb else img])
        pad, esp = 30, 26
        larg = max(l[0].width for l in linhas) + pad * 2
        alt = sum(l[0].height for l in linhas) + esp * (len(linhas) - 1) + pad * 2
        im = Image.new("RGBA", (larg, alt), (17, 17, 19, 255)); y = pad
        for (img,) in linhas:
            im.alpha_composite(img, ((larg - img.width) // 2, y)); y += img.height + esp
        im.convert("RGB").save(saida, quality=92); print(saida); sys.exit()
    if modo == "legendas":
        lista = montar_legenda(montar_blocos()); E_ = EST["legenda"]
        P["legendas"] = [dict(ini=round(l["ini"], 3), fim=round(l["fim"], 3), txt=l["txt"], x=None, y=None, oculto=None,
                              **({"pals": l["pals"]} if l.get("pals") else {})) for l in lista]
        P.setdefault("legenda_pos", dict(x=E_["x"], y=E_["y"], tam=E_["tam"]))
        json.dump(P, open(PLANO, "w"), ensure_ascii=False, indent=1); print(len(lista), "legendas"); sys.exit()
    if modo == "preview":
        os.makedirs("prev", exist_ok=True)
        for ts in ARGS[2:]:
            E = Estado(); i = int(round(float(ts) * FPS))
            for j, src in ler_quadros(i):
                quadro(j, src, E).save(f"prev/quadro_{float(ts):07.2f}.jpg", quality=88); break
            for l in E.leitores.values(): l.fechar()
    else:
        saida = ARGS[2]; E = Estado(); total = int(round(P["dur"] * FPS))
        a, b = (int(x) for x in QUADROS.split(":")) if QUADROS else (0, total)
        enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                                "-i", "-", "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-threads", "2" if QUADROS else "0", "-pix_fmt", "yuv420p", saida],
                               stdin=subprocess.PIPE)
        for i, src in ler_quadros(a):
            if i >= b: break
            enc.stdin.write(quadro(i, src, E).tobytes())
            if i % 30 == 0: E.liberar(i / FPS)
            if (i - a) % 30 == 0: print("quadro", i - a, b - a, flush=True)
        enc.stdin.close(); enc.wait(); print("fim", flush=True)
