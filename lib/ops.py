"""Roteiro de edição feito pela IA (edicao_ia.json), ancorado em PALAVRAS DA FONTE pelo número.

O número de cada palavra vem da transcrição da gravação original (fonte/whisper.json), então é o mesmo em todas
as versões: um item ancorado numa palavra do lead 01 só existe na versão A; um item do corpo aparece em A e B,
cada um no seu tempo. Sem busca por texto, sem âncora ambígua.

edicao_ia.json = {"letreiros": [...], "brolls": [...], "efeitos": [...]}
  letreiro: {linhas: [{estilo, texto, em, emoji}], ate, topo, atras, tam}
  broll:    {formato: cheia|canto|dividida|card, em, ate, broll: ID, ini, trans, saida, lado, pb, celeb, fila,
             recorte: nenhum|tira_topo|tira_base|tira_topo_e_base, enquadra: alto|meio|baixo}
  efeito:   {tipo: escuro|pb|glitch|flash, em, ate}
em = palavra em que entra (início dela); ate = última palavra que cobre (fim dela). Só usa a biblioteca padrão."""
import os, json

FOLGA = 0.25
DIVIDIDA = 1080 / 960                               # proporção do quadro de cima da tela dividida

def recorte(it, tipo, rec=None, enq=None):
    """Recorte em pixels do arquivo [x, y, w, h]: tira a faixa de cima e/ou de baixo (texto queimado) e, na tela dividida de
    um clipe vertical, escolhe a faixa que aparece (alto/meio/baixo = onde está o assunto). None = sem recorte."""
    w, h = it.get("w") or 0, it.get("h") or 0
    if not w or not h: return None
    y0, y1 = 0, h
    if rec in ("tira_topo", "tira_topo_e_base"): y0 = round(h * 0.2)
    if rec in ("tira_base", "tira_topo_e_base"): y1 = round(h * 0.82)
    if tipo == "dividida" and h > w:
        faixa = min(y1 - y0, round(w / DIVIDIDA)); centro = {"alto": 0.36, "meio": 0.5, "baixo": 0.64}.get(enq or "meio", 0.5) * h
        return [0, int(min(max(centro - faixa / 2, y0), y1 - faixa)), w, int(faixa)]
    return [0, y0, w, y1 - y0] if (y0, y1) != (0, h) else None

def ficha_de(it):
    """A ficha (estudo) do clipe na biblioteca, se ele veio de lá."""
    try:
        import biblioteca
        x = biblioteca.item(it.get("id") or "") or {}
        return x.get("estudo") or {}
    except Exception: return {}

def recorte_fino(it, tipo, rec, enq, ini, dur_cena, rel=None, n=0):
    """Recorte pela ficha do clipe, no trecho que vai aparecer. Card: usa a região limpa que a ficha marcou (e avisa se um
    texto entra nela nesse trecho). Nos outros formatos: tira a faixa exata do texto no topo/base e, na tela dividida,
    centra a faixa onde está o assunto. None = usa o recorte simples."""
    e = ficha_de(it); w, h = it.get("w") or 0, it.get("h") or 0
    if not e or not w or not h: return None
    txt = [t for t in e.get("texto_na_tela", []) if "y_ini" in t and t["ini"] < ini + dur_cena and t["fim"] > ini]
    cd = ((e.get("formatos") or {}).get("card")) or {}
    if tipo == "card":                                    # card = recorte limpo do quadro (pode ser quadrado ou vertical)
        if not (cd.get("serve") and any(k in cd for k in ("x_ini", "y_ini"))): return None
        cy0, cy1 = cd.get("y_ini", 0), cd.get("y_fim", 1)
        if rel is not None:                               # o recorte tem que ficar limpo o trecho INTEIRO que vai na tela
            entra = [t for t in txt if not (t["y_fim"] <= cy0 + 0.01 or t["y_ini"] >= cy1 - 0.01)]
            if entra:
                q = "; ".join(f"{t['ini']:.1f}–{t['fim']:.1f}s" for t in entra[:3])
                rel["avisos"].append(f"B-roll {n}: {it.get('id')} tem texto entrando no card entre {q} (mude o ini ou outro clipe)")
            tr = cd.get("trechos") or []
            if tr and not any(t["ini"] - 0.2 <= ini and ini + dur_cena <= t["fim"] + 0.2 for t in tr):
                q = ", ".join(f"{t['ini']:.1f}–{t['fim']:.1f}s" for t in tr[:4])
                rel["avisos"].append(f"B-roll {n}: o card de {it.get('id')} só fica limpo em {q}; comece dentro de um desses trechos")
        x0, x1 = round(w * cd.get("x_ini", 0)), round(w * cd.get("x_fim", 1)); yy0, yy1 = round(h * cy0), round(h * cy1)
        return [int(x0), int(yy0), int(x1 - x0), int(yy1 - yy0)] if x1 - x0 > 60 and yy1 - yy0 > 60 else None
    y0, y1 = 0.0, 1.0
    if rec in ("tira_topo", "tira_topo_e_base"): y0 = 0.2
    if rec in ("tira_base", "tira_topo_e_base"): y1 = 0.82
    for t in txt:
        if t["y_fim"] <= 0.45: y0 = max(y0, t["y_fim"] + 0.015)          # texto no alto: corta até logo abaixo dele
        elif t["y_ini"] >= 0.55: y1 = min(y1, t["y_ini"] - 0.015)        # texto embaixo: corta até logo acima
        elif rel is not None: rel["avisos"].append(f"B-roll {n}: {it.get('id')} tem texto no meio do quadro nesse trecho "
                                                   + ("(dá para usar como card)" if cd.get("serve") else "(outro ini ou outro clipe)"))
    if tipo != "dividida" and (y0 > 0.3 or y1 < 0.7 or y1 - y0 < 0.62):  # corte grande demais amplia muito o clipe
        if y1 - y0 < 0.62 and y1 < 1.0: y1 = 1.0                          # primeiro abre mão do corte de baixo (costuma ser o @ pequeno)
        if rel is not None: rel["avisos"].append(f"B-roll {n}: {it.get('id')} tem texto grande demais para recortar sem perder o quadro")
        y0, y1 = min(y0, 0.3), max(y1, 0.7)
    Y0, Y1 = round(h * y0), round(h * y1)
    if tipo == "dividida" and h > w:
        centro = ((e.get("formatos") or {}).get("dividida") or {}).get("centro")
        if centro is None: centro = {"alto": 0.36, "meio": 0.5, "baixo": 0.64}.get(enq or e.get("posicao_assunto") or "meio", 0.5)
        faixa = min(Y1 - Y0, round(w / DIVIDIDA))
        return [0, int(min(max(centro * h - faixa / 2, Y0), Y1 - faixa)), w, int(faixa)]
    return [0, Y0, w, Y1 - Y0] if (Y0, Y1) != (0, h) else None

def palavras(whisper):
    return [dict(w=w["word"].strip(), t=float(w["start"]), e=float(w["end"])) for s in whisper["segments"]
            for w in s.get("words", []) if w["word"].strip()]

def ler_mapa(vd):
    m = json.load(open(os.path.join(vd, "mapa.json")))
    segs = m["mapa"] if isinstance(m, dict) else m
    segs = [(float(a), float(b), float(t)) for a, b, t in segs]
    dur = float(m["dur"]) if isinstance(m, dict) else (segs[-1][2] + segs[-1][1] - segs[-1][0] if segs else 0.0)
    return segs, dur

def na_versao(ts, segs, fim=False, par=None):
    """Tempo na fonte -> tempo na versão; None se esse ponto foi cortado da versão. par = a outra ponta da palavra:
    o Whisper às vezes começa (ou termina) a palavra dentro do silêncio; se o resto dela está no trecho, ela conta."""
    for a, b, t in segs:
        if not fim:
            if a - FOLGA <= ts < b: return t + max(0.0, ts - a)
            if par is not None and ts < a < par - 0.12 and par <= b + FOLGA: return t
        else:
            if a < ts <= b + FOLGA: return t + min(b - a, ts - a)
            if par is not None and a - FOLGA <= par < b - 0.12 and ts > b: return t + (b - a)
    return None

def presenca(W, versoes):
    """versoes: [(id, segs)] -> {índice da palavra: {id: tempo na versão}}"""
    pres = {}
    for vid, segs in versoes:
        for k, w in enumerate(W):
            tv = na_versao(w["t"], segs, par=w["e"])
            if tv is not None: pres.setdefault(k, {})[vid] = tv
    return pres

def montagem(W, versoes, max_palavras=14):
    """Texto da fala de todas as versões com o número de cada palavra, na ordem do vídeo, agrupado pelo que é de
    cada versão (lead) e o que é comum. É o que o Claude lê para ancorar a edição."""
    pres = presenca(W, versoes); ids = [v for v, _ in versoes]; corridas, vistos = [], set()
    for vid, _ in versoes:
        seq = sorted((k for k in pres if vid in pres[k]), key=lambda k: pres[k][vid]); atual = None
        for k in seq:
            chave = tuple(v for v in ids if v in pres[k])
            if k in vistos: atual = None; continue
            if atual is None or atual[0] != chave: atual = [chave, []]; corridas.append(atual)
            atual[1].append(k); vistos.add(k)
    ordem = lambda c: (min(pres[c[1][0]].values()), ids.index(c[0][0]))
    L = []
    for chave, ks in sorted(corridas, key=ordem):
        if len(ids) > 1: L.append(("\n## Só na versão " + chave[0]) if len(chave) == 1 else ("\n## Comum às versões " + " e ".join(chave)))
        linha = []
        def fecha():
            if not linha: return
            k0 = linha[0]; cab = " · ".join(f"{v} {pres[k0][v]:.1f}s" for v in chave)
            L.append(f"[{cab}] " + " ".join(f"{k}:{W[k]['w']}" + ("" if j + 1 >= len(linha) or linha[j + 1] == k + 1 else " ✂") for j, k in enumerate(linha)))
            linha.clear()
        for k in ks:
            linha.append(k)
            if len(linha) >= max_palavras or W[k]["w"][-1:] in ".?!": fecha()
        fecha()
    return "\n".join(L).strip()

def _t(W, segs, k, fim=False):
    if not isinstance(k, int) or not 0 <= k < len(W): return None
    return na_versao(W[k]["e" if fim else "t"], segs, fim, par=W[k]["t" if fim else "e"])

def construir(ops, W, segs, dur, acervo, ES):
    """ops -> (itens do plano desta versão, relatório). O relatório lista erros (o Claude tem que corrigir),
    avisos (conferir) e ajustes feitos sozinho. Itens citados pelo número da lista do Claude (letreiro 3, B-roll 5)."""
    rel = dict(erros=[], avisos=[], ajustes=[], fora=[])
    estilos = ES["letreiro"]["estilos"]; topo_padrao = ES["letreiro"].get("topo_padrao", 0.12)
    blocos, cenas, escuro, pb, glitch, flash = [], [], [], [], [], []
    for n, L in enumerate(ops.get("letreiros", []), 1):
        linhas = []
        for l in L.get("linhas", []):
            if l.get("estilo") not in estilos: rel["erros"].append(f"letreiro {n}: estilo '{l.get('estilo')}' não existe (use {', '.join(estilos)})"); continue
            if not str(l.get("texto", "")).strip(): continue
            t = _t(W, segs, l.get("em"))
            if t is None:
                if not isinstance(l.get("em"), int) or not 0 <= l["em"] < len(W): rel["erros"].append(f"letreiro {n}: palavra {l.get('em')} não existe")
                continue
            linhas.append([l["estilo"], str(l["texto"]), round(t, 3), l.get("emoji") or None])
        if not linhas: rel["fora"].append(f"letreiro {n}"); continue
        linhas.sort(key=lambda x: x[2]); ini = linhas[0][2]
        fim = _t(W, segs, L.get("ate"), True) if L.get("ate") is not None else None
        if fim is None or fim <= linhas[-1][2] + 0.2:
            if L.get("ate") is not None: rel["avisos"].append(f"letreiro {n}: 'ate' ({L.get('ate')}) não serve (cortada ou antes da última linha); ficou 1,8s")
            fim = linhas[-1][2] + 1.8
        b = dict(linhas=linhas, fim=round(fim, 3), topo=float(L["topo"]) if L.get("topo") is not None else topo_padrao, atras=bool(L.get("atras")), ia_op=n)
        if L.get("tam"): b["tam"] = int(L["tam"])
        blocos.append(b)
    for n, B in enumerate(ops.get("brolls", []), 1):
        t0 = _t(W, segs, B.get("em"))
        if t0 is None:
            if not isinstance(B.get("em"), int) or not 0 <= B["em"] < len(W): rel["erros"].append(f"B-roll {n}: palavra {B.get('em')} não existe")
            else: rel["fora"].append(f"B-roll {n}")
            continue
        t0 = max(0.0, t0 - 0.08)
        t1 = _t(W, segs, B.get("ate"), True) if B.get("ate") is not None else None
        if t1 is None or t1 <= t0 + 0.4:
            if B.get("ate") is not None: rel["avisos"].append(f"B-roll {n}: 'ate' ({B.get('ate')}) não serve; ficou 2,5s")
            t1 = t0 + 2.5
        it = acervo.get(B.get("broll"))
        if not it: rel["erros"].append(f"B-roll {n}: '{B.get('broll')}' não está no acervo"); continue
        if B.get("formato") not in ("cheia", "canto", "dividida", "card"): rel["erros"].append(f"B-roll {n}: formato '{B.get('formato')}' não existe"); continue
        c = dict(tipo=B["formato"], ini=round(t0, 3), fim=round(min(t1, dur), 3), trans=B.get("trans") or "whip", saida=B.get("saida") or "corte",
                 src=it["src"], id=it["id"], slot="ia", src_ini=float(B.get("ini") or 0), ia_op=n)
        if B.get("lado") in ("esq", "dir"): c["lado"] = B["lado"]
        fl = B.get("fila")
        if c["tipo"] == "card" and isinstance(fl, list) and len(fl) == 2 and 1 <= int(fl[0]) <= int(fl[1]) <= 6 and int(fl[1]) > 1: c["fila"] = [int(fl[0]), int(fl[1])]
        if B.get("pb"): c["pb"] = True
        if B.get("celeb"): c["celeb"] = True
        cr = recorte_fino(it, c["tipo"], B.get("recorte"), B.get("enquadra"), c["src_ini"], c["fim"] - c["ini"], rel, n) \
            or recorte(it, c["tipo"], B.get("recorte"), B.get("enquadra"))
        if cr: c["crop"] = cr
        elif it.get("crop"): c["crop"] = it["crop"]
        if not it.get("dur") and c["tipo"] != "card": rel["avisos"].append(f"B-roll {n}: {it['id']} é imagem parada; imagem fica melhor como card")
        if it.get("dur") and c["src_ini"] + (c["fim"] - c["ini"]) > it["dur"] + 0.1:
            rel["avisos"].append(f"B-roll {n}: {it['id']} tem {it['dur']:.1f}s e começa em {c['src_ini']:.1f}s, mas a cena dura {c['fim'] - c['ini']:.1f}s (vai repetir o começo)")
        cenas.append(c)
    for n, F in enumerate(ops.get("efeitos", []), 1):
        t = _t(W, segs, F.get("em"))
        if t is None:
            if not isinstance(F.get("em"), int) or not 0 <= F["em"] < len(W): rel["erros"].append(f"efeito {n}: palavra {F.get('em')} não existe")
            else: rel["fora"].append(f"efeito {n}")
            continue
        fim = _t(W, segs, F.get("ate"), True) if F.get("ate") is not None else None
        tipo = F.get("tipo")
        if tipo == "escuro": a = max(0.0, t - 0.1); escuro.append([round(a, 3), round(fim if fim and fim > a + 0.3 else a + 2.0, 3)])
        elif tipo == "pb": a = max(0.0, t - 0.05); pb.append([round(a, 3), round(fim if fim and fim > a + 0.3 else a + 2.5, 3)]); glitch.append([round(a, 3), 0.2])
        elif tipo == "glitch": glitch.append([round(t, 3), 0.2])
        elif tipo == "flash": flash.append(round(t, 3))
        else: rel["erros"].append(f"efeito {n}: tipo '{tipo}' não existe")
    cenas.sort(key=lambda c: c["ini"]); blocos.sort(key=lambda b: b["linhas"][0][2])
    # ---- conferência automática
    sobra = lambda a0, a1, b0, b1: min(a1, b1) - max(a0, b0)
    grandes = [c for c in cenas if c["tipo"] != "card"]              # figuras (card) podem ficar juntas na tela; B-roll não
    for c in [c for c in cenas if c["tipo"] == "card"]:
        for g in grandes:
            if sobra(c["ini"], c["fim"], g["ini"], g["fim"]) > 0.3:
                rel["avisos"].append(f"B-roll {c['ia_op']} (figura) não aparece enquanto o B-roll {g['ia_op']} ({g['tipo']}) está na tela")
    for a, b in zip(grandes, grandes[1:]):
        if b["ini"] < a["fim"] - 0.03:
            if a["fim"] - b["ini"] <= 0.35 and b["ini"] > a["ini"] + 0.3: a["fim"] = b["ini"]      # encostada de corte seco: resolve sozinho
            else: rel["erros"].append(f"B-roll {a['ia_op']} ({a['ini']:.1f}–{a['fim']:.1f}s) e B-roll {b['ia_op']} ({b['ini']:.1f}–{b['fim']:.1f}s) se sobrepõem")
    usados = {}
    for c in cenas:
        for d in usados.get(c["id"], []):
            if abs(d["src_ini"] - c["src_ini"]) < 2.0: rel["avisos"].append(f"B-roll {c['ia_op']}: o mesmo trecho de {c['id']} já aparece no B-roll {d['ia_op']}")
        usados.setdefault(c["id"], []).append(c)
    for a, b in zip(blocos, blocos[1:]):
        if b["linhas"][0][2] < a["fim"] - 0.02:
            novo = round(b["linhas"][0][2] - 0.03, 3)
            if novo > a["linhas"][-1][2] + 0.1: a["fim"] = novo; rel["ajustes"].append(f"letreiro {a['ia_op']} encurtado para não encostar no letreiro {b['ia_op']}")
            else: rel["erros"].append(f"letreiros {a['ia_op']} e {b['ia_op']} ficam na tela ao mesmo tempo")
    for b in blocos:
        for c in cenas:
            s = sobra(b["linhas"][0][2], b["fim"], c["ini"], c["fim"])
            if c["tipo"] == "dividida" and s > 0.2 and b["topo"] > 0.07:
                b["topo"] = 0.05; rel["ajustes"].append(f"letreiro {b['ia_op']} subiu para o topo (tela dividida do B-roll {c['ia_op']})")
            if c["tipo"] == "card" and s > 0.3: rel["avisos"].append(f"letreiro {b['ia_op']} aparece junto com o card do B-roll {c['ia_op']} (os dois ficam no topo)")
    CF = ES.get("conferencia", {}); max_br = CF.get("max_sem_broll", 8.0); max_lt = CF.get("max_sem_letreiro", 7.0)   # ritmo do estilo
    cob = sorted([(c["ini"], c["fim"]) for c in cenas] + [tuple(x) for x in escuro] + [tuple(x) for x in pb]); fim_cob = 0.0
    for a, b in cob + [(dur, dur)]:
        if max_br and a - fim_cob > max_br: rel["avisos"].append(f"{fim_cob:.1f}–{a:.1f}s: {a - fim_cob:.0f}s sem B-roll nem efeito")
        fim_cob = max(fim_cob, b)
    ultimo = 0.0
    for b in (blocos + [dict(linhas=[[None, None, dur]], fim=dur)]) if max_lt else []:
        if b["linhas"][0][2] - ultimo > max_lt: rel["avisos"].append(f"{ultimo:.1f}–{b['linhas'][0][2]:.1f}s: sem letreiro")
        ultimo = max(ultimo, b["fim"])
    import regras                                     # regras de ritmo do modelo (Ultradinâmico Criativo…)
    for w in regras.conferir(dict(cenas=cenas, blocos=blocos, escuro=escuro, dur=dur), ES): rel["erros"].append("regra do modelo: " + w.strip())
    rel["numeros"] = dict(letreiros=len(blocos), brolls=len(cenas), escuro=len(escuro), pb=len(pb),
                          letreiros_por_minuto=round(len(blocos) / max(dur / 60, 0.1), 1), dur=round(dur, 1))
    return dict(blocos=blocos, cenas=cenas, escuro=sorted(escuro), pb=sorted(pb), glitch=sorted(glitch), flash=sorted(flash)), rel

def relatorio_texto(rels):
    """rels: {versão: relatório} -> texto curto para o Claude."""
    L = []
    for v, r in rels.items():
        n = r.get("numeros", {})
        L.append(f"### Versão {v}: {n.get('dur', 0)}s · {n.get('letreiros', 0)} letreiros ({n.get('letreiros_por_minuto', 0)}/min) · "
                 f"{n.get('brolls', 0)} B-rolls · {n.get('escuro', 0)} modo escuro · {n.get('pb', 0)} P&B")
        for k, nome in (("erros", "ERROS (corrigir)"), ("avisos", "Avisos"), ("ajustes", "Ajustado sozinho")):
            if r.get(k): L.append(f"{nome}:"); L += [f"- {x}" for x in r[k]]
    return "\n".join(L)
