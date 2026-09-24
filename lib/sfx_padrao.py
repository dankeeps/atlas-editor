"""Efeitos sonoros padrão para templates sem sfx_regras.py próprio — os criados pela skill criar-template
sempre caem aqui (ver lib/template_escrita.py: nunca aceita um .py vindo de IA, porque comum.regras_sfx()
executa esse arquivo de verdade). Um humano pode escrever um sfx_regras.py customizado depois, à mão, para um
template específico; até lá, este arquivo genérico atende.

Só usa sinal ESTRUTURAL do plano (tipo de transição, estilo de letreiro, emoji já decidido por outra etapa) —
nunca o texto da fala/letreiro em si. Os arquivos de sfx_regras.py escritos à mão têm regras que reconhecem
palavras específicas do anúncio original; isso é exatamente o que não generaliza para um anúncio diferente,
então fica de fora daqui de propósito.

eventos(P) -> mesmo formato de estilos/*/sfx_regras.py: lista de dict(chave, t, f, g, ini, dur, fade, a, auto).
"""
ATAQUE = {"pxb-swoosh-015.wav": 0.50, "pxb-fast-whoosh.wav": 0.55, "pxb-whoosh-simple.wav": 0.16, "pxb-swipe-whoosh3.wav": 0.16,
          "pxb-whoosh-effect.wav": 0.15, "pxb-swipe-whoosh1.wav": 0.09, "sfx-pop.wav": 0.02, "pxb-pop-dragon.wav": 0.20,
          "pxb-pop2-dragon.wav": 0.12, "pxb-bubblepop06.wav": 0.12, "pxb-ding.wav": 0.08, "pxb-subboom.wav": 0.06,
          "pxb-vhs-glitch.wav": 0.04, "pxb-ui-interface.wav": 0.15, "pxb-impact-hit.wav": 0.17, "pxb-shutter.wav": 0.15,
          "sfx-escrevendo.wav": 0.0, "sfx-cronometro-rapido.wav": 0.0, "pxb-laugh-sitcom.wav": 0.2, "pxb-riser-small.wav": 0.98}
WT = ["pxb-whoosh-simple.wav", "pxb-swipe-whoosh3.wav", "pxb-whoosh-effect.wav", "pxb-swipe-whoosh1.wav"]
WC = ["pxb-swoosh-015.wav", "pxb-fast-whoosh.wav"]
POPS = ["pxb-pop2-dragon.wav", "pxb-bubblepop06.wav", "sfx-pop.wav", "pxb-pop-dragon.wav"]
DUR_PADRAO = {"pxb-subboom.wav": 1.2, "sfx-escrevendo.wav": 1.3, "sfx-cronometro-rapido.wav": 1.2,
              "pxb-laugh-sitcom.wav": 2.6, "pxb-riser-small.wav": 1.0}
EMOJI_SOM = {"✅": ("pxb-ding.wav", -19, None), "📋": ("sfx-escrevendo.wav", -19, 1.3), "⏰": ("sfx-cronometro-rapido.wav", -19, 1.2),
             "😂": ("pxb-laugh-sitcom.wav", -26, 2.6)}
ESTILO_SOM = {"num": ("pxb-impact-hit.wav", -17, None), "branca": ("pxb-riser-small.wav", -24, 1.0)}


def fade_de(f):
    return 0.4 if "boom" in f else 0.06


def eventos(P):
    rod = {"wt": 0, "wc": 0, "pop": 0}

    def prox(k, lst):
        f = lst[rod[k] % len(lst)]; rod[k] += 1; return f

    ev = []

    def add(chave, t, f, g, ini=0.0, dur=None, pr=1):
        ev.append((t, f, g, ini, dur, pr, chave))

    for n, c in enumerate(sorted(P.get("cenas", []), key=lambda c: c["ini"])):
        u = c.get("uid", f"i{n}"); tr = c.get("trans", "whip"); t = c["ini"] + 0.08
        if c.get("celeb"): add(f"cena:{u}:impacto", c["ini"] + 0.02, "pxb-subboom.wav", -12, 0, 1.2, 3)
        if tr == "corte" and not c.get("celeb"): add(f"cena:{u}:entra", t, prox("pop", POPS), -20, 0, None, 1); continue
        if tr == "corte": continue
        if c.get("tipo") == "card": add(f"cena:{u}:entra", t, prox("wc", WC), -16, pr=2)
        elif tr == "whip": add(f"cena:{u}:entra", c["ini"] + 0.12, prox("wc", WC), -15, pr=2)
        elif tr == "zoom": add(f"cena:{u}:entra", c["ini"] + 0.13, "pxb-impact-hit.wav", -17, pr=2)
        elif tr == "glitch": add(f"cena:{u}:entra", c["ini"] + 0.05, "pxb-vhs-glitch.wav", -15, pr=2)
        elif tr == "flash": add(f"cena:{u}:entra", c["ini"] + 0.13, "pxb-shutter.wav", -19, pr=2)
        elif tr == "slide": add(f"cena:{u}:entra", t, prox("wt", WT), -16, pr=2)
        s = c.get("saida", "corte")
        if s in ("whip", "slide"): add(f"cena:{u}:sai", c["fim"] - 0.08, prox("wt", WT), -19, pr=1)
        elif s == "glitch": add(f"cena:{u}:sai", c["fim"] - 0.10, "pxb-vhs-glitch.wav", -18, pr=1)

    for a, b in P.get("escuro", []):
        add(f"escuro:{a:.2f}", a + 0.02, "pxb-subboom.wav", -13, 0, 1.2, 2)
    for g0, gd in P.get("glitch", []):
        add(f"glitch:{g0:.2f}", g0, "pxb-vhs-glitch.wav", -16, pr=2)

    for n, b in enumerate(sorted(P.get("blocos", []), key=lambda b: min(l[2] for l in b["linhas"]))):
        u = b.get("uid", f"i{n}")
        for k, (est, _txt, t, em) in enumerate(b["linhas"]):
            ch = f"letreiro:{u}:{k}"
            if em in EMOJI_SOM:
                f, g, dur = EMOJI_SOM[em]; add(ch, t + (0.03 if em == "✅" else 0.0), f, g, 0, dur, 2); continue
            if est in ESTILO_SOM:
                f, g, dur = ESTILO_SOM[est]; add(ch, t + (0.02 if est == "num" else 0.0), f, g, 0, dur, 2 if est == "num" else 1); continue
            if k == 0: add(ch, t + 0.08, prox("wt", WT), -18, pr=1)
            else: add(ch, t + 0.04, prox("pop", POPS), -19, pr=1)

    ev.sort(key=lambda e: (e[0], -e[5])); final = []
    for e in ev:
        if final and e[0] - final[-1][0] < 0.18 and "subboom" not in e[1] and "subboom" not in final[-1][1]:
            if e[5] > final[-1][5]: final[-1] = e
            continue
        final.append(e)

    ed = P.get("sfx_edicoes", {}); out = []
    for t, f, g, ini, dur, pr, chave in final:
        o = ed.get(chave, {})
        if o.get("f") and o["f"] != f: f, ini, dur = o["f"], 0.0, DUR_PADRAO.get(o["f"])
        g = o.get("g", g); t = t + o.get("dt", 0.0)
        out.append(dict(chave=chave, t=round(t, 4), f=f, g=g, ini=ini, dur=dur, fade=fade_de(f),
                        a=round(max(0.0, t - ATAQUE.get(f, 0.0)), 4), auto=True, editado=bool(o), off=bool(o.get("off"))))
    for n, x in enumerate(P.get("sfx_extras", [])):
        f = x["f"]; t = float(x["t"])
        out.append(dict(chave=f"extra:{n}", t=round(t, 4), f=f, g=x.get("g", -18), ini=0.0, dur=DUR_PADRAO.get(f), fade=fade_de(f),
                        a=round(max(0.0, t - ATAQUE.get(f, 0.0)), 4), auto=False, editado=True, off=False))
    out.sort(key=lambda e: e["a"])
    return out
