"""Exemplo aprovado de um modelo de edição: transforma uma edição que o cliente aprovou (plano.json da versão) numa linha do
tempo legível — cada letreiro, B-roll e efeito com a fala por baixo — e grava em estilos/<estilo>/exemplo.md.
A edição automática põe esse texto no pedido do PLANO: é o que ensina o ritmo (onde entra cada coisa, quanto fica, que
palavras viram letreiro), que regra nenhuma captura inteira.

  /usr/bin/python3 lib/exemplo.py "<projeto>" [versão] [--estilo nome]"""
import os, sys, json

LIB = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, LIB)
import comum

def fala(W, a, b):
    return " ".join(w["w"] for w in W if a - 0.05 <= w["ini"] < b).strip()

def palavra_em(W, t):
    w = min(W, key=lambda w: abs(w["ini"] - t)) if W else None
    return w["w"].strip(" ,.!?") if w else "?"

def f(t): return f"{t:.1f}".replace(".", ",")

def gerar(proj, v="A"):
    d = os.path.join(comum.RAIZ, proj); vd = os.path.join(d, "versoes", v)
    P = json.load(open(os.path.join(vd, "plano.json"))); pj = json.load(open(os.path.join(d, "projeto.json")))
    wj = json.load(open(os.path.join(vd, "whisper.json")))
    W = [dict(w=w["word"].strip(), ini=w["start"]) for s in wj.get("segments", []) for w in s.get("words", [])]
    ac = {}
    for arq in ("acervo/acervo.json", "acervo/uploads.json"):
        p = os.path.join(d, arq)
        if os.path.exists(p): ac.update({x["id"]: x for x in json.load(open(p))})
    itens = []
    for b in P["blocos"]:
        ini = min(l[2] for l in b["linhas"])
        linhas = " / ".join(f"[{l[0]}]{' ' + l[3] if l[3] else ''} \"{l[1]}\" (entra em \"{palavra_em(W, l[2])}\")" for l in b["linhas"])
        extra = (" · atrás da cabeça" if b.get("atras") else "") + (f" · topo {b['topo']}" if b.get("topo") not in (None, 0.12) else "")
        itens.append((ini, b["fim"], f"LETREIRO {linhas}{extra}"))
    for c in P["cenas"]:
        x = ac.get(c.get("id"), {}); o_que = x.get("descricao") or x.get("nome") or c.get("id", "")
        itens.append((c["ini"], c["fim"], f"B-ROLL {c['tipo']}{' ' + c.get('lado') if c.get('lado') else ''} "
                                          f"({f(c['fim'] - c['ini'])} s, entra com {c.get('trans', 'corte')}): {o_que[:110]}"))
    for a, b in P.get("escuro", []): itens.append((a, b, "MODO ESCURO (só a pessoa, holofote)"))
    for a, b in P.get("pb", []): itens.append((a, b, "P&B com glitch"))
    itens.sort(key=lambda x: x[0])
    L = [f"Edição aprovada pelo cliente: \"{pj.get('nome', proj)}\", versão {v} ({f(P['dur'])} s). Copie a LÓGICA (ritmo, "
         "respiro com a pessoa sozinha, quais palavras viram letreiro, quanto cada coisa fica, variedade de formato), não o texto.", ""]
    ant = 0.0
    for a, b, o in itens:
        if a - ant > 1.2: L.append(f"{f(ant)}–{f(a)} s · só a pessoa com a legenda · fala: \"{fala(W, ant, a)[:150]}\"")
        L.append(f"{f(a)}–{f(b)} s · {o} · fala: \"{fala(W, a, b)[:150]}\"")
        ant = max(ant, b)
    if P["dur"] - ant > 1.2: L.append(f"{f(ant)}–{f(P['dur'])} s · só a pessoa com a legenda · fala: \"{fala(W, ant, P['dur'])[:150]}\"")
    cen = P["cenas"]; tipos = {t: sum(1 for c in cen if c["tipo"] == t) for t in ("cheia", "canto", "dividida", "card")}
    L += ["", f"Resumo: {len(P['blocos'])} letreiros ({len(P['blocos']) / P['dur'] * 60:.0f} por minuto), {len(cen)} B-rolls "
              f"({', '.join(f'{n} {t}' for t, n in tipos.items() if n)}), {len(P.get('escuro', []))} modo escuro."]
    return "\n".join(L), pj.get("estilo") or P.get("estilo"), " ".join(w["w"] for w in W)

if __name__ == "__main__":
    a = sys.argv[1:]; est = None
    if "--estilo" in a: i = a.index("--estilo"); est = a[i + 1]; del a[i:i + 2]
    txt, e, fala_toda = gerar(a[0], a[1] if len(a) > 1 else "A")
    dest = os.path.join(comum.SKILL, "estilos", est or e, "exemplo.md"); open(dest, "w").write(txt + "\n")
    # a fala do vídeo do exemplo: se alguém subir ESSE vídeo de novo, a edição automática deixa o exemplo de fora
    open(os.path.join(os.path.dirname(dest), "exemplo_fonte.txt"), "w").write(fala_toda + "\n")
    print(txt); print(f"\n-> {dest}")
