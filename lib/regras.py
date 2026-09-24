"""Conferência das regras de ritmo do modelo de edição (estilo.json -> "regras"). Roda no fim do `estudio.py plano` e no
`estudio.py conferir`, e devolve avisos com o segundo de cada problema para eu corrigir o edicao.py antes de mostrar.
Modelo sem "regras" não é conferido (o Ultradinâmico normal fica como está).

  "regras": {
    "min_dur":   {"canto": 3.0, "dividida": 2.0, "cheia": 1.2, "card": 2.0},  duração mínima de cada formato (s)
    "respiro":   1.5,        só a pessoa, sem B-roll, entre duas cenas de B-roll (s)
    "encadeia":  [["dividida", "cheia"], ["cheia", "cheia"]],   passagens diretas permitidas (sem respiro)
    "max_encadeadas": 3,     cenas de B-roll seguidas sem respiro
    "letreiro_sobre_broll": false,   letreiro não fica em cima de B-roll...
    "empilha_ok_emoji": ["✅"],      ...a não ser a lista de checks
    "letreiro_apos_broll": 0.6,      letreiro espera isso depois que o B-roll sai (s)
    "max_blocos_juntos": 1,          letreiros na tela ao mesmo tempo
    "linhas_max": 2,                 linhas por letreiro (a lista de checks pode ter mais)
    "escuro_sobre_broll": false,     modo escuro não fica em cima de B-roll
    "letreiro_dur_max": 4.0,         segundos na tela por letreiro (lista de checks e letreiro da tela dividida podem mais)
    "palavras_max": 5,               palavras por letreiro, somando as linhas (lista de checks pode mais)
    "max_sem_broll": 10,             segundos seguidos sem B-roll de verdade (modo escuro não conta), do começo ao fim
    "min_fora_da_cheia": 0.4,        fração dos B-rolls em canto/dividida/card (com 4 ou mais B-rolls)
    "max_por_tipo": {"canto": 2},    teto de B-rolls por formato
    "max_transicoes": 3,             quantas entradas/saídas podem ter efeito (o resto é corte seco)
    "transicoes_ok": ["fade"],       quais efeitos este modelo aceita
    "sem_letreiro": true,            nenhum letreiro entra (só a legenda carrega o texto) — ignora as regras de letreiro acima
    "sem_escuro": true               nenhuma janela de modo escuro entra
  }
As quatro últimas vieram da comparação de 2026-09-19 entre a edição do AM29 feita no chat (aprovada) e a automática
(frases longas seguradas até 6,6 s, 6 de 9 B-rolls em tela cheia, 19 s sem B-roll)."""

def _t(s): return f"{s:5.1f}s"

def _texto(b): return " / ".join(l[1] for l in b["linhas"])[:40]

def conferir(Pl, E):
    R = E.get("regras")
    if not R: return []
    av = []; cenas = sorted(Pl["cenas"], key=lambda c: c["ini"])
    ok_emoji = set(R.get("empilha_ok_emoji", ["✅"]))
    checks = lambda b: any((l[3] or "") in ok_emoji for l in b["linhas"])
    blocos = sorted(Pl["blocos"], key=lambda b: min(l[2] for l in b["linhas"]))

    if R.get("sem_letreiro"):                                  # nenhum letreiro é permitido neste modelo
        for b in blocos: av.append(f"{_t(min(l[2] for l in b['linhas']))}  letreiro '{_texto(b)}' não é permitido neste modelo (sem letreiro: só a legenda)")
        blocos = []
    if R.get("sem_escuro"):                                    # nenhuma janela de modo escuro é permitida
        for a, b in Pl.get("escuro", []): av.append(f"{_t(a)}  modo escuro ({a:.1f}–{b:.1f}s) não é permitido neste modelo (sem modo escuro)")

    for c in cenas:                                            # formato que some rápido demais
        m = R.get("min_dur", {}).get(c["tipo"]); d = c["fim"] - c["ini"]
        if m and d < m - 0.05 and not c.get("celeb"):          # sequência de famosos é uma montagem só
            av.append(f"{_t(c['ini'])}  {c['tipo']} fica só {d:.1f}s (mínimo {m:.1f}s)")

    respiro, maxe = R.get("respiro", 0.0), R.get("max_encadeadas", 99)
    diretas = {tuple(x) for x in R.get("encadeia", [])}; cadeia = 1
    for a, b in zip(cenas, cenas[1:]):                         # troca de formato sem respiro
        gap = b["ini"] - a["fim"]
        if gap >= respiro: cadeia = 1; continue
        cadeia += 1
        if (a["tipo"], b["tipo"]) not in diretas and not (a.get("celeb") and b.get("celeb")):
            av.append(f"{_t(a['fim'])}  {a['tipo']} → {b['tipo']} com {max(0.0, gap):.1f}s de respiro (precisa {respiro:.1f}s; "
                      f"direto só {', '.join(' → '.join(x) for x in sorted(diretas)) or 'nenhum'})")
        if cadeia > maxe: av.append(f"{_t(b['ini'])}  {cadeia} B-rolls seguidos sem respiro (máximo {maxe})")

    for b in blocos:
        ini, fim = min(l[2] for l in b["linhas"]), b["fim"]
        if not R.get("letreiro_sobre_broll", True) and not checks(b):
            c = next((c for c in cenas if ini < c["fim"] - 0.05 and fim > c["ini"] + 0.05), None)
            if c: av.append(f"{_t(ini)}  letreiro '{_texto(b)}' em cima do B-roll {c['tipo']} ({c['ini']:.1f}–{c['fim']:.1f}s)")
        la = R.get("letreiro_apos_broll", 0.0)
        c = next((c for c in cenas if 0 <= ini - c["fim"] < la), None)
        if c: av.append(f"{_t(ini)}  letreiro '{_texto(b)}' entra {ini - c['fim']:.1f}s depois do {c['tipo']} sair (espere {la:.1f}s)")
        lm = R.get("linhas_max")
        if lm and len(b["linhas"]) > lm and not checks(b): av.append(f"{_t(ini)}  letreiro '{_texto(b)}' com {len(b['linhas'])} linhas (máximo {lm})")

    mb = R.get("max_blocos_juntos")
    if mb:
        for i, b in enumerate(blocos):                         # letreiros empilhados
            ini = min(l[2] for l in b["linhas"])
            juntos = [x for x in blocos[:i] if x["fim"] > ini + 0.05]
            if len(juntos) >= mb and not checks(b): av.append(f"{_t(ini)}  letreiro '{_texto(b)}' entra com outro ainda na tela")

    dm, pm = R.get("letreiro_dur_max"), R.get("palavras_max")
    for b in blocos:                                           # letreiro longo demais (texto ou tempo)
        if checks(b): continue
        ini, fim = min(l[2] for l in b["linhas"]), b["fim"]
        na_dividida = any(c["tipo"] == "dividida" and ini < c["fim"] - 0.05 and fim > c["ini"] + 0.05 for c in cenas)
        if dm and fim - ini > dm + 0.05 and not na_dividida:
            av.append(f"{_t(ini)}  letreiro '{_texto(b)}' fica {fim - ini:.1f}s na tela (máximo {dm:.1f}s: encurte o ate)")
        n = sum(len(str(l[1]).split()) for l in b["linhas"])
        if pm and n > pm: av.append(f"{_t(ini)}  letreiro '{_texto(b)}' tem {n} palavras (máximo {pm}: fique com a palavra-chave)")

    ms = R.get("max_sem_broll")
    if ms:                                                     # buraco sem B-roll (modo escuro e P&B não contam)
        dur = Pl.get("dur") or (max([c["fim"] for c in cenas] + [b["fim"] for b in blocos] + [0.0]))
        fim_ant = 0.0
        for c in cenas + [dict(ini=dur, fim=dur)]:
            if c["ini"] - fim_ant > ms + 0.05: av.append(f"{_t(fim_ant)}  {c['ini'] - fim_ant:.1f}s sem B-roll até {c['ini']:.1f}s (máximo {ms:.0f}s)")
            fim_ant = max(fim_ant, c["fim"])
    mf = R.get("min_fora_da_cheia")
    if mf and len(cenas) >= 4:                                 # variedade de formato
        fora = sum(1 for c in cenas if c["tipo"] != "cheia")
        if fora < mf * len(cenas) - 0.01:
            av.append(f"  0.0s  só {fora} de {len(cenas)} B-rolls fora da tela cheia (mínimo {mf:.0%} em canto/dividida/card)")

    mc = R.get("max_por_tipo") or {}                           # teto por formato (o recorte cansa se repetir)
    for tipo, teto in mc.items():
        n = sum(1 for c in cenas if c["tipo"] == tipo)
        if n > teto: av.append(f"  0.0s  {n} B-rolls em '{tipo}' (máximo {teto} neste modelo: troque os outros de formato)")
    mt = R.get("max_transicoes")
    if mt is not None:                                         # corte seco na maioria das trocas
        tr = [c for c in cenas if (c.get("trans") or "whip") != "corte"] + [c for c in cenas if (c.get("saida") or "corte") != "corte"]
        if len(tr) > mt: av.append(f"  0.0s  {len(tr)} transições com efeito (máximo {mt}: o resto entra e sai em corte seco)")
    ta = R.get("transicoes_ok")
    if ta:
        for c in cenas:
            for campo in ("trans", "saida"):
                v = c.get(campo) or ("whip" if campo == "trans" else "corte")
                if v != "corte" and v not in ta:
                    av.append(f"{_t(c['ini'])}  transição '{v}' não é deste modelo (use {', '.join(ta)} ou corte seco)")
    if not R.get("escuro_sobre_broll", True):
        for a, b in Pl.get("escuro", []):
            c = next((c for c in cenas if a < c["fim"] - 0.05 and b > c["ini"] + 0.05), None)
            if c: av.append(f"{_t(a)}  modo escuro em cima do B-roll {c['tipo']} ({c['ini']:.1f}–{c['fim']:.1f}s)")
    return av

def texto(R):
    """As regras do modelo em frases (vão para o pedido ao Claude e para a aba Templates)."""
    if not R: return []
    n = lambda v: f"{v:g}".replace(".", ",")
    L = []
    if R.get("sem_letreiro"): L.append("SEM LETREIRO: nenhum bloco de texto na tela, nem a lista de checks — só a legenda carrega a fala")
    if R.get("sem_escuro"): L.append("SEM MODO ESCURO: nenhuma janela de holofote/vinheta escura, nem no fechamento")
    if R.get("min_dur"): L.append("tempo mínimo na tela: " + ", ".join(f"{k} {n(v)} s" for k, v in R["min_dur"].items()))
    if R.get("respiro"): L.append(f"{n(R['respiro'])} s só com a pessoa entre dois B-rolls; passagens diretas só "
                                  + ", ".join(" → ".join(x) for x in R.get("encadeia", [])) + f"; no máximo {R.get('max_encadeadas', 99)} cenas seguidas")
    if not R.get("sem_letreiro"):
        if not R.get("letreiro_sobre_broll", True): L.append("letreiro não fica em cima de B-roll (exceto lista de " + " / ".join(R.get("empilha_ok_emoji", [])) + ")")
        if R.get("letreiro_apos_broll"): L.append(f"letreiro espera {n(R['letreiro_apos_broll'])} s depois que o B-roll sai")
        if R.get("max_blocos_juntos"): L.append(f"um letreiro por vez, até {R.get('linhas_max')} linhas")
        if R.get("palavras_max"): L.append(f"letreiro com no máximo {R['palavras_max']} palavras (somando as linhas) e {n(R.get('letreiro_dur_max', 99))} s na tela "
                                           "(a lista de checks e o letreiro da tela dividida podem mais)")
    if not R.get("sem_escuro") and not R.get("escuro_sobre_broll", True): L.append("modo escuro não fica em cima de B-roll")
    if R.get("max_sem_broll"): L.append(f"nunca mais de {n(R['max_sem_broll'])} s seguidos sem B-roll (modo escuro não conta), do começo ao fim")
    if R.get("min_fora_da_cheia"): L.append(f"pelo menos {R['min_fora_da_cheia']:.0%} dos B-rolls em canto, dividida ou card")
    for tipo, teto in (R.get("max_por_tipo") or {}).items(): L.append(f"no máximo {teto} B-roll(s) em '{tipo}' no vídeo inteiro")
    if R.get("max_transicoes") is not None: L.append(f"corte seco na maioria: no máximo {R['max_transicoes']} entradas/saídas com efeito"
                                                     + (f" (só {', '.join(R['transicoes_ok'])})" if R.get("transicoes_ok") else ""))
    return L

