"""Regras de efeito sonoro do estilo Ultradinâmico (mapa de referência) — fonte única do render (mix.py) e da prévia do editor.
eventos(P) -> lista de dict(chave, t=pico no vídeo, f=arquivo em sfx/, g=ganho dB do pico, ini, dur, fade, a=início no vídeo, auto).
Edições do usuário ficam no plano e são aplicadas por cima das regras:
  P["sfx_edicoes"][chave] = {"f": troca o arquivo, "g": ganho, "dt": desloca (s), "off": true silencia (o evento continua na lista com off=True)}
  P["sfx_extras"] = [{"t": pico, "f": arquivo, "g": ganho}]   (sons acrescentados à mão)"""

ATAQUE = {"pxb-swoosh-015.wav": 0.50, "pxb-fast-whoosh.wav": 0.55, "pxb-whoosh-simple.wav": 0.16, "pxb-swipe-whoosh3.wav": 0.16,
          "pxb-whoosh-effect.wav": 0.15, "pxb-swipe-whoosh1.wav": 0.09, "pxb-zap.wav": 0.05, "sfx-pop.wav": 0.02,
          "pxb-pop-dragon.wav": 0.20, "pxb-pop2-dragon.wav": 0.12, "pxb-bubblepop06.wav": 0.12, "pxb-ding.wav": 0.08,
          "pxb-levelup.wav": 0.18, "pxb-subboom.wav": 0.06, "pxb-vhs-glitch.wav": 0.04, "sfx-escrevendo.wav": 0.0,
          "sfx-cronometro-rapido.wav": 0.0, "pxb-ui-interface.wav": 0.15, "pxb-ui-scan.wav": 0.02, "pxb-laugh-sitcom.wav": 0.2,
          "pxb-riser-small.wav": 0.98, "pxb-impact-hit.wav": 0.17, "pxb-shutter.wav": 0.15}
WT = ["pxb-whoosh-simple.wav", "pxb-swipe-whoosh3.wav", "pxb-whoosh-effect.wav", "pxb-swipe-whoosh1.wav"]
WC = ["pxb-swoosh-015.wav", "pxb-fast-whoosh.wav"]
POPS = ["pxb-pop2-dragon.wav", "pxb-bubblepop06.wav", "sfx-pop.wav", "pxb-pop-dragon.wav"]
DUR_PADRAO = {"pxb-subboom.wav": 1.2, "sfx-escrevendo.wav": 1.3, "sfx-cronometro-rapido.wav": 1.2, "pxb-laugh-sitcom.wav": 2.6,
              "pxb-riser-small.wav": 1.0}

def fade_de(f): return 0.4 if "boom" in f else 0.06

def eventos(P):
    rod = {"wt": 0, "wc": 0, "pop": 0}
    def prox(k, lst):
        f = lst[rod[k] % len(lst)]; rod[k] += 1; return f
    ev = []
    def add(chave, t, f, g, ini=0.0, dur=None, pr=1): ev.append((t, f, g, ini, dur, pr, chave))

    for n, c in enumerate(sorted(P["cenas"], key=lambda c: c["ini"])):
        u = c.get("uid", f"i{n}"); tr = c.get("trans", "whip"); t = c["ini"] + 0.08
        if c.get("celeb"): add(f"cena:{u}:impacto", c["ini"] + 0.02, "pxb-subboom.wav", -12, 0, 1.2, 3)
        if tr == "corte" and not c.get("celeb"): add(f"cena:{u}:entra", t, prox("pop", POPS), -20, 0, None, 1); continue
        if tr == "corte": continue
        if c["tipo"] == "card": add(f"cena:{u}:entra", t, prox("wc", WC), -16, pr=2)
        elif tr == "whip": add(f"cena:{u}:entra", c["ini"] + 0.12, prox("wc", WC), -15, pr=2)
        elif tr == "zoom": add(f"cena:{u}:entra", c["ini"] + 0.13, "pxb-impact-hit.wav", -17, pr=2); add(f"cena:{u}:entra2", c["ini"] + 0.10, "pxb-whoosh-effect.wav", -18, pr=1)
        elif tr == "glitch": add(f"cena:{u}:entra", c["ini"] + 0.05, "pxb-vhs-glitch.wav", -15, pr=2)
        elif tr == "flash": add(f"cena:{u}:entra", c["ini"] + 0.13, "pxb-shutter.wav", -19, pr=2); add(f"cena:{u}:entra2", c["ini"] + 0.13, "pxb-whoosh-simple.wav", -19, pr=1)
        elif tr == "slide": add(f"cena:{u}:entra", t, prox("wt", WT), -16, pr=2)
        s = c.get("saida", "corte")
        if s in ("whip", "slide"): add(f"cena:{u}:sai", c["fim"] - 0.08, prox("wt", WT), -19, pr=1)
        elif s == "glitch": add(f"cena:{u}:sai", c["fim"] - 0.10, "pxb-vhs-glitch.wav", -18, pr=1)
    for a, b in P["escuro"]:
        add(f"escuro:{a:.2f}", a + 0.02, "pxb-subboom.wav", -13, 0, 1.2, 2)
    for g0, gd in P["glitch"]:
        add(f"glitch:{g0:.2f}", g0, "pxb-vhs-glitch.wav", -16, pr=2)

    def tem(txt, *ch): return any(k in txt.lower() for k in ch)
    for n, b in enumerate(sorted(P["blocos"], key=lambda b: min(l[2] for l in b["linhas"]))):
        u = b.get("uid", f"i{n}"); marca = False
        for k, (est, txt, t, em) in enumerate(b["linhas"]):
            ch = f"letreiro:{u}:{k}"
            if em == "✅": add(ch, t + 0.03, "pxb-ding.wav", -19 - (0 if k == 0 else 2), pr=2); continue
            if em == "📋": add(ch, t, "sfx-escrevendo.wav", -19, 0, 1.3, 2); continue
            if em == "⏰": add(ch, t, "sfx-cronometro-rapido.wav", -19, 0, 1.2, 2); continue
            if em == "😂": add(ch, t + 0.1, "pxb-laugh-sitcom.wav", -26, 0, 2.6, 2); continue
            if tem(txt, "metodologia", "fatores", "coisas", "picos de testosterona", "estímulo"):
                add(ch, t + 0.05, "pxb-ui-interface.wav", -19, pr=2); add(ch + ":b", t + 0.30, "pxb-ui-scan.wav", -25, pr=1)
                if est != "sans": continue
            if tem(txt, "shape") and not marca: add(ch, t + 0.03, "pxb-zap.wav", -17, pr=2); marca = True; continue
            if marca and tem(txt, "natural"): continue
            if tem(txt, "+1.500", "testosterona alta", "+ pico", "pico de"): add(ch, t + 0.05, "pxb-levelup.wav", -17, pr=2); continue
            if est == "num": add(ch, t + 0.02, "pxb-impact-hit.wav", -17, pr=2); continue
            if est == "branca" and not em: add(ch, t, "pxb-riser-small.wav", -24, 0, 1.0, 1); continue
            if k == 0 and est == "sans" and em not in ("❌",): add(ch, t + 0.08, prox("wt", WT), -18, pr=1)
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
