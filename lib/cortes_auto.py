"""Proposta automática de cortes: alinha a transcrição (com tempo por palavra) ao roteiro e escolhe,
para cada frase do roteiro, a ÚLTIMA tomada boa. O que sobra entre as tomadas escolhidas (retomadas,
conversa com a equipe, testes) sai. Seções cujo título começa com LEAD/HOOK/GANCHO viram versões alternativas.

uso (via estudio.py): proposta(palavras, roteiro_txt) -> dict(versoes=[...], frases=[...], descartes=[...])"""
import re, unicodedata
from difflib import SequenceMatcher

def norm(w):
    w = unicodedata.normalize("NFD", w.lower()); w = "".join(c for c in w if unicodedata.category(c) != "Mn")
    w = re.sub(r"[^a-z0-9%]", "", w)
    return {"pra": "para", "pro": "para", "ta": "esta", "to": "estou"}.get(w, w)

NUM = {"um": "1", "uma": "1", "dois": "2", "duas": "2", "tres": "3", "quatro": "4", "cinco": "5", "seis": "6", "sete": "7", "oito": "8",
       "nove": "9", "dez": "10", "doze": "12", "vinte": "20", "trinta": "30", "cinquenta": "50", "cem": "100", "mil": "1000"}
def tok(w):
    n = norm(w); return NUM.get(n, n)

TITULO = re.compile(r"^\s*(VSL\s+PARTE\s+\d+|LEAD\s*\d+|HOOK\s*\d*|GANCHO\s*\d*|MECANISMO|FUTURE\s+PACING|FECHAMENTO|BÔNUS|BONUS|OFERTA|CTA|"
                    r"TRANSI[ÇC][ÃA]O\s+PARA\s+OFERTA|PROVA|HIST[ÓO]RIA|INTRODU[ÇC][ÃA]O)\s*$", re.I)

def secoes(roteiro, parte=None):
    """Divide o roteiro em seções (título, frases). parte='VSL PARTE 01' limita à parte pedida."""
    linhas = [l.strip() for l in roteiro.splitlines()]
    out, atual, dentro = [], None, parte is None
    for l in linhas:
        if not l: continue
        if re.match(r"^\s*VSL\s+PARTE", l, re.I):
            dentro = parte is None or norm(l) == norm(parte); continue
        if not dentro: continue
        if TITULO.match(l) or (len(l) < 40 and l.isupper()):
            atual = dict(titulo=l.strip(), frases=[]); out.append(atual); continue
        if atual is None: atual = dict(titulo="INÍCIO", frases=[]); out.append(atual)
        l = l.replace("✅", "").strip()
        for fr in re.split(r"(?<=[.!?…])\s+", l):
            ws = [tok(w) for w in fr.split() if tok(w)]
            if len(ws) >= 2: atual["frases"].append(dict(texto=fr.strip(), toks=ws))
    return [s for s in out if s["frases"]]

def partes(roteiro):
    """Títulos 'VSL PARTE N' do roteiro, na ordem."""
    return [l.strip() for l in roteiro.splitlines() if re.match(r"^\s*VSL\s+PARTE\s+\d+\s*$", l, re.I)]

def detectar_parte(roteiro, palavras):
    """Qual parte do roteiro foi gravada neste vídeo: a que tem mais trios de palavras em comum com a fala."""
    ps = partes(roteiro)
    if len(ps) < 2: return ps[0] if ps else None
    T = [tok(p["w"]) for p in palavras]; fala = {tuple(T[i:i + 3]) for i in range(len(T) - 2)}; melhor = (0, None)
    for p in ps:
        toks = [t for s in secoes(roteiro, p) for f in s["frases"] for t in f["toks"]]
        tri = [tuple(toks[i:i + 3]) for i in range(len(toks) - 2)]
        if tri: melhor = max(melhor, (sum(t in fala for t in tri) / len(tri), p))
    return melhor[1]

def melhor_tomada(frase, palavras, a_partir, ate):
    """Acha todas as janelas que casam com a frase entre a_partir e ate, agrupa as que se sobrepõem (uma tomada)
    e devolve a da ÚLTIMA tomada: a janela de maior semelhança, empatando pela que começa antes (não come o início)."""
    alvo = frase["toks"]; n = len(alvo); T = [tok(p["w"]) for p in palavras]; cands = []
    for i in range(a_partir, min(ate, len(T))):
        if T[i] not in alvo[:3]: continue
        for L in range(max(2, int(n * 0.6)), int(n * 1.35) + 2):
            j = i + L
            if j > len(T): break
            r = SequenceMatcher(None, alvo, T[i:j], autojunk=False).ratio()
            if r >= 0.72: cands.append((i, j, r))
    if not cands: return None
    cands.sort(); grupos = [[cands[0]]]
    for c in cands[1:]:                       # nova tomada quando o início anda mais que meia frase
        if c[0] - grupos[-1][-1][0] > max(3, n * 0.5): grupos.append([c])
        else: grupos[-1].append(c)
    g = grupos[-1]; melhor = max(r for _, _, r in g)
    return sorted([c for c in g if c[2] >= melhor - 0.01], key=lambda c: (-c[2], -(c[1] - c[0])))[0]

def repeticoes(toks, alvo):
    """Trechos [p, q) a remover dentro de uma tomada: gaguejada (palavra repetida em seguida) e
    retomada curta (um 3-grama que reaparece logo depois), desde que o roteiro não repita aquilo."""
    cortes = []; ngr = lambda l, i, k: tuple(l[i:i + k])
    alvo_ng = [ngr(alvo, i, 3) for i in range(len(alvo) - 2)]
    p = 0
    while p < len(toks) - 1:
        feito = False
        for q in range(p + 1, min(len(toks) - 2, p + 11)):
            if ngr(toks, p, 3) == ngr(toks, q, 3) and len(ngr(toks, p, 3)) == 3 and alvo_ng.count(ngr(toks, p, 3)) < 2:
                cortes.append((p, q)); p = q; feito = True; break
        if not feito:
            if toks[p] == toks[p + 1] and alvo.count(toks[p]) < 2 and len(toks[p]) > 2: cortes.append((p, p + 1))
            p += 1
    return cortes

def proposta(palavras, roteiro, parte=None):
    """palavras: [{w, t, e}] da fonte inteira. Devolve versões (faixas em segundos), frases e descartes."""
    S = secoes(roteiro, parte); frases = []
    alt = [s for s in S if re.match(r"^(LEAD|HOOK|GANCHO)", s["titulo"], re.I)]
    # passo 1: acha cada frase na ordem, pegando a última tomada boa antes da próxima frase
    cursor = 0
    todas = [(s, f) for s in S for f in s["frases"]]
    for k, (s, f) in enumerate(todas):
        # limite: a primeira ocorrência da frase seguinte (a tomada desta tem que vir antes dela)
        lim = len(palavras)
        if k + 1 < len(todas):
            nx = todas[k + 1][1]; primeira = None
            for i in range(cursor, len(palavras)):
                if tok(palavras[i]["w"]) == nx["toks"][0]:
                    b = melhor_tomada(nx, palavras, i, i + 1)
                    if b and b[2] >= 0.8: primeira = i; break
            if primeira is not None: lim = primeira + 1
        b = melhor_tomada(f, palavras, cursor, lim)
        if b is None:
            b2 = melhor_tomada(f, palavras, cursor, len(palavras))
            frases.append(dict(secao=s["titulo"], texto=f["texto"], ok=False, ratio=round(b2[2], 2) if b2 else 0,
                               ini=None, fim=None)); continue
        i, j, r = b
        toks = [tok(p["w"]) for p in palavras[i:j]]; rep = repeticoes(toks, f["toks"])
        pedacos, ini_k = [], i                  # divide a tomada tirando as repetições internas
        for p0, q0 in rep:
            if p0 > 0 and i + p0 > ini_k: pedacos.append((ini_k, i + p0))
            ini_k = i + q0
        pedacos.append((ini_k, j))
        frases.append(dict(secao=s["titulo"], texto=f["texto"], ok=r >= 0.8, ratio=round(r, 2), i=i, j=j,
                           ini=palavras[i]["t"], fim=palavras[j - 1]["e"], dito=" ".join(p["w"] for p in palavras[i:j]),
                           pedacos=[[palavras[a]["t"], palavras[b2 - 1]["e"]] for a, b2 in pedacos if b2 > a],
                           removido=[" ".join(p["w"] for p in palavras[i + a:i + b2]) for a, b2 in rep]))
        cursor = j
    # passo 2: descartes = trechos de fala fora das tomadas escolhidas
    usados = set()
    for fr in frases:
        if fr.get("ini") is not None:
            for a, b2 in fr["pedacos"]:
                usados.update(k for k in range(fr["i"], fr["j"]) if a <= palavras[k]["t"] <= b2)
    descartes, cur = [], []
    for i, p in enumerate(palavras):
        if i in usados:
            if cur: descartes.append(cur); cur = []
        else: cur.append(i)
    if cur: descartes.append(cur)
    desc = [dict(ini=palavras[d[0]]["t"], fim=palavras[d[-1]]["e"], dito=" ".join(palavras[i]["w"] for i in d)) for d in descartes]
    # passo 3: versões — cada seção alternativa (LEAD 01, LEAD 02...) + todas as seções comuns
    comuns = [s["titulo"] for s in S if s not in alt]
    def faixas(secs):
        return [pc for fr in frases if fr["secao"] in secs and fr.get("ini") is not None for pc in fr["pedacos"]]
    if len(alt) >= 2:
        versoes = [dict(id=chr(65 + k), nome=f"Versão {chr(65 + k)} · {s['titulo'].title()}", secoes=[s["titulo"]] + comuns,
                        faixas=faixas([s["titulo"]] + comuns)) for k, s in enumerate(alt)]
    else:
        versoes = [dict(id="A", nome="Versão A", secoes=[s["titulo"] for s in S], faixas=faixas([s["titulo"] for s in S]))]
    return dict(versoes=versoes, frases=frases, descartes=desc)

def refinar_pausas(prop, palavras, audio16k, transcrever):
    """Pausa longa ou palavra esticada dentro de uma tomada costuma esconder uma retomada que a transcrição
    engoliu. Retranscreve só esse trecho; se aparecer um 2-grama repetido, corta da 1ª ocorrência até a 2ª.
    transcrever(inicio, fim) -> [{w, t, e}] em segundos absolutos."""
    achados = []
    for fr in prop["frases"]:
        if fr.get("ini") is None: continue
        novos = []
        for a, b in fr["pedacos"]:
            ws = [p for p in palavras if a <= p["t"] <= b]; cortes = []
            for k in range(len(ws)):
                gap = ws[k + 1]["t"] - ws[k]["e"] if k + 1 < len(ws) else 0
                esticada = ws[k]["e"] - ws[k]["t"] > 1.1
                if gap > 0.9 or esticada:
                    j0, j1 = ws[max(0, k - 3)]["t"], min(b, (ws[k + 1]["e"] if k + 1 < len(ws) else ws[k]["e"]) + 3.0)
                    iso = transcrever(j0, j1); T = [tok(x["w"]) for x in iso]
                    for p in range(len(T) - 1):
                        if len(T[p]) > 3 and T[p] == T[p + 1]:                 # "segundo, segundo"
                            cortes.append((iso[p]["t"], iso[p + 1]["t"])); break
                        for q in range(p + 2, min(len(T) - 1, p + 12)):
                            if T[p] and T[p:p + 2] == T[q:q + 2] and len(T[p]) > 1:
                                cortes.append((iso[p]["t"], iso[q]["t"])); break
                        else: continue
                        break
            if not cortes: novos.append([a, b]); continue
            ini = a
            for c0, c1 in sorted(cortes):
                if c0 > ini: novos.append([ini, c0 - 0.02])
                ini = max(ini, c1 - 0.05); achados.append(dict(frase=fr["texto"], de=c0, ate=c1))
            novos.append([ini, b])
        fr["pedacos"] = [p for p in novos if p[1] - p[0] > 0.15]
    for v in prop["versoes"]:
        v["faixas"] = [pc for fr in prop["frases"] if fr["secao"] in v["secoes"] and fr.get("ini") is not None for pc in fr["pedacos"]]
    prop["retomadas_escondidas"] = achados
    return prop

def relatorio(prop):
    L = ["# Proposta de cortes", ""]
    for v in prop["versoes"]:
        tot = sum(b - a for a, b in v["faixas"]); L.append(f"- **{v['nome']}**: {len(v['faixas'])} frases, ~{tot:.0f}s de fala")
    ruins = [f for f in prop["frases"] if not f["ok"]]
    L += ["", f"## Frases para conferir ({len(ruins)})", ""]
    for f in ruins:
        L.append(f"- [{f['secao']}] roteiro: \"{f['texto']}\"" + (f"\n  dito ({f['ratio']}): \"{f.get('dito', '')}\" em {f['ini']:.1f}s" if f.get("ini") is not None else "\n  NÃO ENCONTRADA"))
    rem = [(f, r) for f in prop["frases"] for r in f.get("removido", [])]
    if rem:
        L += ["", f"## Repetições tiradas dentro das frases ({len(rem)})", ""] + [f"- \"{r}\"  (em: {f['texto'][:60]}…)" for f, r in rem]
    esc = prop.get("retomadas_escondidas", [])
    if esc:
        L += ["", f"## Retomadas escondidas em pausas ({len(esc)})", ""] + [f"- {e['de']:.1f}–{e['ate']:.1f}s  (em: {e['frase'][:60]}…)" for e in esc]
    L += ["", f"## Trechos descartados ({len(prop['descartes'])})", ""]
    for d in prop["descartes"]:
        L.append(f"- {d['ini']:.1f}–{d['fim']:.1f}s: \"{d['dito']}\"")
    return "\n".join(L)
