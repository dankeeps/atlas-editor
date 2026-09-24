#!/usr/bin/env python3
"""Edição automática no fluxo da edição feita no chat: transcreve -> PLANEJA onde entra cada coisa -> biblioteca -> busca o
que falta -> monta. Claude pela API decide (com a edição aprovada do modelo como exemplo de ritmo), a Apify busca no TikTok,
e cada clipe é estudado UMA vez (ficha na biblioteca). O que o cliente não gostar, ele troca no editor.

  1 transcrição   estudio.py novo (palavra por palavra)
  2 cortes        limpeza conservadora: fala inteira menos erros comprovados -> VAD -> uma versão
  3 recorte       máscaras da pessoa, em paralelo
  4 plano         Claude planeja a edição inteira no modelo: letreiros, efeitos e os MOMENTOS de B-roll (formato, o que
                  precisa aparecer, termos de busca) -> conferência de ritmo e das regras do modelo -> corrige
  5 B-roll        para cada momento, primeiro a biblioteca (fichas prontas); o que faltar -> TikTok (Apify) e YouTube ->
                  tudo vai para a biblioteca e cada clipe novo é estudado uma vez (Claude pela folha de contato, ou Gemini)
                  -> Claude escolhe o clipe e o segundo de cada momento lendo as fichas
  6 montagem      Claude monta a edição final (ini no trecho limpo, recorte da legenda queimada, enquadramento da dividida)
                  -> conferência automática (sobreposição, ritmo, regras do modelo) -> corrige
  7 conferência   OPCIONAL (pedido "conferir"): quadros reais para o Claude (+ Gemini, se configurado) -> corrige
  8 render        vídeo final; os B-rolls que entraram passam para "usados" na biblioteca
Estado em <projeto>/auto/estado.json (a tela inicial acompanha). Rodando de novo, retoma de onde parou.
uso: .venv/bin/python lib/auto.py <pasta do projeto>        (o pedido fica em <projeto>/auto/pedido.json)"""
import os, sys, json, time, re, glob, shutil, signal, subprocess, threading, traceback, unicodedata, hashlib
from concurrent.futures import ThreadPoolExecutor
LIB = os.path.dirname(os.path.abspath(__file__)); SKILL = os.path.dirname(LIB); sys.path.insert(0, LIB)
import comum, chaves, ops, ia, cortes_auto, custos, biblioteca, gemini, youtube, tiktok, limpeza_cortes
from ia import texto

PY = sys.executable; ESTUDIO = os.path.join(LIB, "estudio.py")
MAX_BUSCAS = 6                 # buscas no TikTok por vídeo (cada uma ~US$ 0,18 + o estudo de cada clipe novo, uma vez)
MAX_YOUTUBE = 3
RECORTES = ["nenhum", "tira_topo", "tira_base", "tira_topo_e_base"]
ETAPAS = [("transcricao", "Transcrição da fala"), ("cortes", "Cortes: silêncios e erros"), ("preparar", "Recorte da pessoa e prévia"),
          ("plano", "Plano da edição"), ("broll", "B-roll: biblioteca, busca e escolha"), ("montagem", "Montagem e conferência automática"),
          ("conferencia", "Conferência quadro a quadro (opcional)"), ("render", "Render final")]

ETAPAS_BP = [("transcricao", "Transcrição da fala"), ("cortes", "Cortes: silêncios e erros"), ("preparar", "Recorte da pessoa e prévia"),
             ("encaixe", "B-rolls do anúncio e da oferta, montados no formato"), ("revisao", "Duplo check: onde faltou B-roll (busca específica)"),
             ("letreiros", "Letreiros e efeitos sonoros (+ regras do modelo)"), ("conferencia", "Conferência quadro a quadro (opcional)"),
             ("render", "Render final")]
MAX_BUSCAS_REVISAO = 3         # buscas específicas no TikTok na revisão do modo "B-roll primeiro"

class Falha(Exception): pass

def fmt(s): s = int(round(s)); return f"{s // 60}:{s % 60:02d}"

# ================================================================ estado (lido pela tela inicial)
class Estado:
    def __init__(self, d, etapas=ETAPAS):
        self.arq = os.path.join(d, "auto", "estado.json"); self.lock = threading.RLock()
        self.s = json.load(open(self.arq)) if os.path.exists(self.arq) else {}
        feitas = {e["id"]: e for e in self.s.get("etapas", [])}
        self.s["etapas"] = [feitas.get(i) or dict(id=i, nome=n, estado="pendente", detalhe="") for i, n in etapas]
        for e in self.s["etapas"]: e["nome"] = dict(etapas)[e["id"]]
        for e in self.s["etapas"]:
            if e["estado"] != "ok": e["estado"] = "pendente"
        self.s.update(rodando=True, status="rodando", pid=os.getpid(), mensagem="", fim=None)
        self.s.setdefault("inicio", time.time()); self.s.setdefault("log", []); self.s.setdefault("custo", {})
        self.salvar()
    def et(self, i): return next(e for e in self.s["etapas"] if e["id"] == i)
    def marca(self, i, estado, detalhe=None):
        with self.lock:
            e = self.et(i); e["estado"] = estado
            if detalhe is not None: e["detalhe"] = detalhe
            if estado == "rodando": e["inicio"] = time.time()
            if estado in ("ok", "erro"): e["fim"] = time.time()
            self.salvar()
    def log(self, msg):
        with self.lock:
            linha = time.strftime("%H:%M:%S ") + str(msg); print(linha, flush=True)
            self.s["log"] = (self.s["log"] + [linha])[-500:]; self.salvar()
    def custo(self, uso):
        with self.lock: self.s["custo"] = uso; self.salvar()
    def fim(self, status, msg=""):
        with self.lock:
            self.s.update(rodando=False, status=status, mensagem=msg, fim=time.time())
            for e in self.s["etapas"]:
                if e["estado"] == "rodando": e["estado"] = "erro" if status == "erro" else "pendente"
            self.salvar()
    def salvar(self):
        with self.lock:
            tmp = self.arq + ".tmp"; json.dump(self.s, open(tmp, "w"), ensure_ascii=False, indent=1); os.replace(tmp, self.arq)

def rodar(cmd, est=None, rotulo="", env=None, mostrar=None):
    """Roda um script e devolve as linhas da saída. mostrar(linha) -> texto para o log (ou None)."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, bufsize=1)
    linhas = []
    for l in p.stdout:
        l = l.rstrip("\n")
        if not l.strip() or "Warning" in l or "warnings.warn" in l: continue
        linhas.append(l)
        if est and mostrar:
            m = mostrar(l)
            if m: est.log(m)
    p.wait()
    if p.returncode: raise Falha(f"{rotulo or os.path.basename(cmd[1])} falhou:\n" + "\n".join(linhas[-10:]))
    return linhas

def prompt(nome, **subs):
    t = open(os.path.join(SKILL, "prompts", nome + ".md"), encoding="utf-8").read()
    for k, v in subs.items(): t = t.replace("{{" + k + "}}", v)
    return t

def slug(s, n=30):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:n] or "x"

# ================================================================ schemas (structured outputs)
def S(): return {"type": "string"}
def I(): return {"type": "integer"}
def N(): return {"type": "number"}
def B(): return {"type": "boolean"}
def arr(x): return {"type": "array", "items": x}
def nulo(x): return {"anyOf": [x, {"type": "null"}]}
def enum(v): return {"type": "string", "enum": list(v)}
def obj(**props): return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}

TRANS = ["whip", "zoom", "glitch", "flash", "slide", "corte"]; SAIDA = ["corte", "whip", "slide", "glitch"]; FORMATOS = ["cheia", "canto", "dividida", "card"]
TRECHO = obj(de=I(), ate=I(), versoes=arr(S()))
SCH_CORTES = obj(remocoes=arr(obj(de=I(), ate=I(), tipo=enum(limpeza_cortes.TIPOS),
                                  substituto_de=nulo(I()), substituto_ate=nulo(I()),
                                  motivo=S(), confianca=N())), observacoes=arr(S()))
SCH_CONF_CORTES = obj(ok=B(), problemas=arr(S()), trechos=arr(TRECHO))
def sch_letreiro(estilos): return obj(linhas=arr(obj(estilo=enum(estilos), texto=S(), em=I(), emoji=nulo(S()))), ate=nulo(I()), topo=nulo(N()), atras=B(), tam=nulo(I()))
EFEITO = obj(tipo=enum(["escuro", "pb", "glitch", "flash"]), em=I(), ate=nulo(I()))
def sch_plano(estilos):
    momento = obj(formato=enum(FORMATOS), em=I(), ate=nulo(I()), imagem=S(), termos=arr(S()), horizontal=B(), trans=enum(TRANS), saida=enum(SAIDA),
                  lado=nulo(enum(["esq", "dir"])), pb=B(), celeb=B(), fila=nulo(arr(I())))
    return obj(ok=B(), mudancas=arr(S()), letreiros=arr(sch_letreiro(estilos)), momentos=arr(momento), efeitos=arr(EFEITO))
def sch_escolha(ids):
    return obj(escolhas=arr(obj(momento=I(), broll=nulo(enum(ids or ["-"])), ini=N(), recorte=enum(RECORTES), enquadra=enum(["alto", "meio", "baixo"]), motivo=S())),
               buscas=arr(obj(nome=S(), termos=arr(S()), momentos=arr(I()))),
               youtube=arr(obj(nome=S(), termos=S(), pedido=S(), momentos=arr(I()))))
def sch_edicao(estilos, ids):
    broll = obj(formato=enum(FORMATOS), em=I(), ate=nulo(I()), broll=enum(ids or ["-"]), ini=N(), trans=enum(TRANS), saida=enum(SAIDA),
                lado=nulo(enum(["esq", "dir"])), pb=B(), celeb=B(), fila=nulo(arr(I())), recorte=enum(RECORTES), enquadra=enum(["alto", "meio", "baixo"]))
    return obj(ok=B(), mudancas=arr(S()), letreiros=arr(sch_letreiro(estilos)), brolls=arr(broll), efeitos=arr(EFEITO))
SCH_YT = obj(escolhidos=arr(obj(n=I(), motivo=S())))
SCH_TRECHO = obj(serve=B(), ini=N(), fim=N(), descricao=S(), problemas=arr(S()))

# ================================================================ contexto da execução
class Ctx:
    def __init__(self, d, ped, est):
        self.d, self.ped, self.est = d, ped, est; self.cl = None; self._W = None; self.conv_edicao = None
        self.th = None; self.erro_preparar = None; self.etapa = ""; self._reg = threading.Lock()
        self.etapas = ETAPAS; self.conv_bp = None; self.pre_bp = []
    def cobrou(self, info):
        custos.registrar(self.d, servico="claude", etapa=self.etapa, fonte="tokens informados pela API × preço de tabela", **info)
        self.est.custo(custos.resumo(self.d))
    def cobrou_estudo(self, info):
        servico = "gemini" if biblioteca.motor_estudo() == "gemini" else "claude"
        custos.registrar(self.d, servico=servico, etapa="broll", fonte="tokens informados pela API × preço de tabela (estudo do clipe)", **info)
        self.est.custo(custos.resumo(self.d))
    def cobrou_gemini(self, info):
        custos.registrar(self.d, servico="gemini", etapa=self.etapa, fonte="tokens informados pela API × preço de tabela", **info)
        self.est.custo(custos.resumo(self.d))
    def P(self): return json.load(open(os.path.join(self.d, "projeto.json")))
    def anuncio(self):
        if not hasattr(self, "_an"):
            self._an = anuncio_da_leva(self)
            if self._an: self.est.log(f"este vídeo é o \"{self._an[0]}\" da busca de B-roll {self._an[1]}: os clipes com a etiqueta dele vêm primeiro")
        return self._an
    def usa_exemplo(self):
        if not hasattr(self, "_ex"): self._ex = not mesmo_video_do_exemplo(self, self.P()["estilo"])
        return self._ex
    def grava_P(self, P): json.dump(P, open(os.path.join(self.d, "projeto.json"), "w"), ensure_ascii=False, indent=1)
    def W(self):
        if self._W is None: self._W = ops.palavras(json.load(open(os.path.join(self.d, "fonte", "whisper.json"))))
        return self._W
    def vd(self, v): return os.path.join(self.d, "versoes", v)
    def versoes_segs(self): return [(v["id"], ops.ler_mapa(self.vd(v["id"]))[0]) for v in self.P()["versoes"]]
    def montagem(self): return ops.montagem(self.W(), self.versoes_segs())
    def acervo(self):
        a = []
        for arq in ("acervo/acervo.json", "acervo/uploads.json"):
            p = os.path.join(self.d, arq)
            if os.path.exists(p): a += json.load(open(p))
        return a
    def aux(self, nome, dados=None):
        p = os.path.join(self.d, "auto", nome + ".json")
        if dados is not None: json.dump(dados, open(p, "w"), ensure_ascii=False, indent=1); return dados
        return json.load(open(p)) if os.path.exists(p) else None
    def pedir(self, conv, conteudo, schema, rotulo, max_tokens=64000):
        self.est.log(f"{rotulo}: Claude analisando…")
        r = conv.pedir(conteudo, schema, max_tokens=max_tokens, rotulo=rotulo)
        self.registrar(rotulo, conteudo, r); return r
    def registrar(self, rotulo, conteudo, r):
        """auto/conversas/NN-rotulo.md: o que o Claude recebeu (texto; imagens só contadas) e o que respondeu. Para auditar."""
        pasta = os.path.join(self.d, "auto", "conversas"); os.makedirs(pasta, exist_ok=True)
        blocos = conteudo if isinstance(conteudo, list) else [texto(conteudo)]
        corpo = "\n\n".join(b["text"] if b.get("type") == "text" else "[imagem]" for b in blocos)
        nome = re.sub(r"[^\w-]+", "-", rotulo.lower()).strip("-")[:40]
        with self._reg:
            n = len(os.listdir(pasta)) + 1
            open(os.path.join(pasta, f"{n:02d}-{nome}.md"), "w").write(f"# {rotulo}\n\n## Enviado\n\n{corpo}\n\n## Resposta\n\n```json\n"
                                                                         + json.dumps(r, ensure_ascii=False, indent=1) + "\n```\n")
    def iniciar_preparar(self):
        def alvo():
            try:
                self.est.marca("preparar", "rodando", "máscaras da pessoa (Vision) e proxy da prévia")
                self.est.marca("preparar", "ok", et_preparar(self))
            except Exception as e:
                self.erro_preparar = e; self.est.marca("preparar", "erro", str(e)[-400:])
        self.th = threading.Thread(target=alvo, daemon=True); self.th.start()
    def esperar_preparar(self):
        if self.th:
            if self.th.is_alive(): self.est.log("esperando o recorte da pessoa terminar…")
            self.th.join(); self.th = None
        if self.erro_preparar: raise Falha(f"recorte da pessoa: {self.erro_preparar}")

def roda(ctx, i, fn):
    if ctx.est.et(i)["estado"] == "ok": return
    ctx.etapa = i; ctx.est.marca(i, "rodando", ""); ctx.est.log(f"— {dict(ctx.etapas)[i]}")
    try: det = fn(ctx)
    except Exception as e: ctx.est.marca(i, "erro", str(e)[-400:]); raise
    ctx.est.marca(i, "ok", det or "")

# ================================================================ 1. transcrição
def et_transcricao(ctx):
    ped = ctx.ped
    if not os.path.exists(os.path.join(ctx.d, "projeto.json")):
        cmd = [PY, ESTUDIO, "novo", "--nome", ped["nome"], "--video", ped["video"], "--estilo", ped["estilo"], "--parte", "auto"]
        if ped.get("roteiro"): cmd += ["--roteiro", ped["roteiro"]]
        if ped.get("whisper") and os.path.exists(ped["whisper"]): cmd += ["--whisper", ped["whisper"]]   # veio da busca de B-roll
        for k in ("sigla", "expert", "oferta"):
            if ped.get(k): cmd += [f"--{k}", ped[k]]
        rodar(cmd, ctx.est, "transcrição", mostrar=lambda l: l if l.startswith(("extraindo", "transcrevendo", "parte do roteiro")) else None)
    P = ctx.P()
    if ped.get("video_ja_cortado"):
        from video_integral import validar_fonte
        validar_fonte(P, ped)
    if ped.get("nicho") and P.get("nicho") != ped["nicho"]: P["nicho"] = ped["nicho"]; ctx.grava_P(P)
    return f"{len(ctx.W())} palavras" + (f" · roteiro: {P['parte']}" if P.get("parte") else "") + ("" if P.get("roteiro") else " · sem roteiro")


def et_video_integral(ctx):
    """Upload pronto: só adequa o formato, sem decisão de cortes nem nova transcrição."""
    from video_integral import preparar, validar_fonte
    validar_fonte(ctx.P(), ctx.ped)
    dur = preparar(ctx.d, log=ctx.est.log)
    return f"Vídeo já cortado · versão única · {fmt(dur)} integral"


def buscas_permitidas(ped):
    """O reenvio que reaproveita B-rolls nunca pode voltar a buscar ou baixar clipes."""
    return not bool(ped.get("sem_novas_buscas"))

# ================================================================ 2. cortes
def numerada(W):
    """Transcrição com o número de cada palavra; ‖ marca pausa longa."""
    L, linha, t0 = [], [], 0.0
    def fecha(pausa=0.0):
        if linha: L.append(f"[{t0:.1f}s] " + " ".join(linha) + (f"  ‖{pausa:.1f}s" if pausa else "")); linha.clear()
    for k, w in enumerate(W):
        gap = w["t"] - W[k - 1]["e"] if k else 0.0
        if gap > 0.7:
            if linha: fecha(gap)
            elif L: L[-1] += f"  ‖{gap:.1f}s"
        if not linha: t0 = w["t"]
        linha.append(f"{k}:{w['w']}")
        if len(linha) >= 16 or w["w"][-1:] in ".?!": fecha()
    fecha(); return "\n".join(L)

def roteiro_texto(rot, parte):
    return "\n\n".join(f"## {s['titulo']}\n" + "\n".join(f["texto"] for f in s["frases"]) for s in cortes_auto.secoes(rot, parte))

def faixa_palavras(W, a, b):
    ks = [k for k, w in enumerate(W) if w["t"] >= a - 0.06 and w["e"] <= b + 0.06]
    return f"{ks[0]}–{ks[-1]}" if ks else None

def proposta_texto(prop, W):
    L = [f"- versão {v['id']} = {v['nome']} (seções: {', '.join(v['secoes'])})" for v in prop["versoes"]] + [""]
    for fr in prop["frases"]:
        if fr.get("ini") is None: L.append(f"[{fr['secao']}] NÃO ENCONTRADA · roteiro: {fr['texto']}"); continue
        rs = [x for x in (faixa_palavras(W, a, b) for a, b in fr["pedacos"]) if x]
        L.append(f"[{fr['secao']}] {'ok' if fr['ok'] else 'CONFERIR'} ({fr['ratio']}) palavras {', '.join(rs)} · roteiro: {fr['texto']}"
                 + (f" · tirou dentro: {'; '.join(fr['removido'])}" if fr.get("removido") else ""))
    for e in prop.get("retomadas_escondidas", []):
        L.append(f"retomada escondida numa pausa entre {e['de']:.1f}s e {e['ate']:.1f}s (sai sozinha, não precisa tratar)")
    return "\n".join(L)

def validar_trechos(ts, W, versoes, est):
    ids = [v["id"] for v in versoes]; out = []
    for t in ts:
        de, ate = sorted((int(t["de"]), int(t["ate"])))
        if de < 0 or ate >= len(W): est.log(f"  (trecho {de}–{ate} fora da transcrição, ignorado)"); continue
        vs = [v for v in ids if v in [x.strip().upper() for x in t.get("versoes", [])]] or ids
        out.append(dict(de=de, ate=ate, versoes=vs))
    if not out: raise Falha("o Claude não devolveu nenhum trecho válido")
    return out

def grava_cortes(d, prop, versoes, trechos, W):
    esc = sorted((prop or {}).get("retomadas_escondidas", []), key=lambda e: e["de"])
    sec = {v["id"]: v.get("secoes", []) for v in (prop or {}).get("versoes", [])}; V = []
    for v in versoes:
        fx = []
        for t in trechos:
            if v["id"] not in t["versoes"]: continue
            pedacos = [[W[t["de"]]["t"], W[t["ate"]]["e"]]]
            for e in esc:
                a, b = pedacos[-1]
                if a + 0.1 < e["de"] < b and e["ate"] <= b: pedacos[-1:] = [[a, e["de"] - 0.02], [max(a, e["ate"] - 0.05), b]]
            fx += [[round(a, 3), round(b, 3)] for a, b in pedacos if b - a > 0.12]
        V.append(dict(id=v["id"], nome=v["nome"], secoes=sec.get(v["id"], []), faixas=fx))
    json.dump(dict(versoes=V, frases=(prop or {}).get("frases", []), descartes=[], ia=dict(trechos=trechos)),
              open(os.path.join(d, "fonte", "cortes.json"), "w"), ensure_ascii=False, indent=1)

def faixas_por_versao(d):
    return {v["id"]: v["faixas"] for v in json.load(open(os.path.join(d, "fonte", "cortes.json")))["versoes"]}

def conferencia_cortes(ctx, versoes):
    W = ctx.W(); L = ["Assim ficou cada versão depois do corte. A transcrição foi feita de novo, no vídeo já cortado. Cada linha é um "
                      "pedaço contínuo: tempo na versão ← números das palavras da gravação que ele usa."]
    for v in versoes:
        vd = ctx.vd(v["id"])
        if not os.path.exists(os.path.join(vd, "mapa.json")): continue
        segs, dur = ops.ler_mapa(vd); Wv = ops.palavras(json.load(open(os.path.join(vd, "whisper.json"))))
        L.append(f"\n## {v['nome']} ({fmt(dur)})")
        for a, b, t in segs:
            ks = [k for k, w in enumerate(W) if a - 0.25 <= w["t"] < b]
            fala = " ".join(w["w"] for w in Wv if t - 0.05 <= w["t"] < t + (b - a) - 0.05)
            L.append(f"[{t:.1f}s ← {ks[0]}–{ks[-1]}] {fala}" if ks else f"[{t:.1f}s] {fala}")
        ver = os.path.join(vd, "verificacao.md")
        if os.path.exists(ver):
            linhas = [(l[:300] + "…\"") if len(l) > 300 else l for l in open(ver).read().split("\n")[1:]]
            L.append("\nVerificação automática desta versão:\n" + "\n".join(linhas).strip())
    L.append("\nConfira e responda.")
    return "\n".join(L)

def et_cortes(ctx):
    """Preserva a gravação inteira e subtrai somente erros comprovados, em uma versão."""
    import wave
    d, est, W = ctx.d, ctx.est, ctx.W()
    limpeza_cortes.validar_remocoes(W, [])  # valida a fonte antes de chamar a IA
    partes = [texto("# Transcrição completa e imutável\n\n" + numerada(W)),
              texto("Proponha SOMENTE remoções comprovadas. Toda fala não removida fica, na ordem original. "
                    "Uma versão única. Não selecionar/resumir trechos; não criar A/B. "
                    "Não retire pausas ou ruído pelo texto: o detector acústico cuida de ausência de fala.")]
    conv = ctx.cl.conversa(prompt("cortes"), esforco="high")
    r = ctx.pedir(conv, partes, SCH_CORTES, "Limpeza conservadora dos cortes")
    resultado = limpeza_cortes.validar_remocoes(W, r.get("remocoes") if isinstance(r, dict) else r)
    observacoes = r.get("observacoes", []) if isinstance(r, dict) else []
    resultado["observacoes_modelo"] = [str(x)[:600] for x in observacoes[:30]] if isinstance(observacoes, list) else []
    with wave.open(os.path.join(d, "fonte", "voz16k.wav"), "rb") as wav:
        duracao = wav.getnframes() / wav.getframerate()
    documento = limpeza_cortes.criar_documento(W, resultado, duracao)
    documento["observacoes"] += resultado["observacoes_modelo"]
    arq = os.path.join(d, "fonte", "cortes.json")
    temporario = arq + f".{os.getpid()}.tmp"
    try:
        with open(temporario, "w", encoding="utf-8") as arquivo:
            json.dump(documento, arquivo, ensure_ascii=False, indent=1, allow_nan=False)
            arquivo.flush(); os.fsync(arquivo.fileno())
        os.replace(temporario, arq)
    finally:
        if os.path.exists(temporario): os.remove(temporario)
    est.log(f"Versão única · {resultado['palavras_mantidas']}/{len(W)} palavras preservadas · "
            f"{len(resultado['remocoes'])} remoção(ões) comprovada(s)")
    for aviso in resultado["avisos"][:12]: est.log("Fala mantida para revisão: " + aviso)
    if len(resultado["avisos"]) > 12:
        est.log(f"{len(resultado['avisos'])} propostas rejeitadas no total; auditoria completa salva nos cortes.")
    for aviso in resultado["observacoes_modelo"][:6]: est.log("Observação da análise: " + aviso)
    rodar([PY, ESTUDIO, "cortes", d, "--aplicar", "--manter-copia", "--versoes", "A"],
          est, "limpeza e jump cut", mostrar=lambda l: l if l.startswith(("versão", "gerando cópia", "VAD")) else None)
    # Não há segunda chamada que substitua a cobertura completa por seleção livre.
    dur = ops.ler_mapa(ctx.vd("A"))[1]
    pendencias = f" · {len(resultado['rejeitadas'])} proposta(s) ambígua(s) preservada(s) para revisão" if resultado["rejeitadas"] else ""
    return "Versão única: " + fmt(dur) + pendencias

# ================================================================ 3. recorte da pessoa (em paralelo)
def et_preparar(ctx):
    rodar([PY, ESTUDIO, "preparar", ctx.d], ctx.est, "preparar",
          mostrar=lambda l: ("recorte: " + l.strip()) if l.startswith("versão") or "máscaras em" in l else None)
    return "máscaras e prévia prontas"

# ================================================================ 4. plano
def semelhanca(t1, t2):
    """Quanto duas falas são o mesmo texto (fração dos trechos de 3 palavras em comum, 0 a 1)."""
    norm = lambda t: re.findall(r"\w+", unicodedata.normalize("NFKD", t.lower()).encode("ascii", "ignore").decode())
    tri = lambda ws: {tuple(ws[i:i + 3]) for i in range(len(ws) - 2)}
    a, b = tri(norm(t1)), tri(norm(t2))
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0

def mesmo_video_do_exemplo(ctx, nome):
    """O exemplo aprovado é régua de estilo, igual para todo anúncio — não é histórico. Mas se o vídeo novo É o do exemplo,
    o exemplo fica de fora, para a edição sair do zero (mais de 30% dos trechos de 3 palavras iguais)."""
    p = os.path.join(SKILL, "estilos", nome, "exemplo_fonte.txt")
    return os.path.exists(p) and semelhanca(open(p, encoding="utf-8").read(), " ".join(w["w"] for w in ctx.W())) > 0.3

def anuncio_da_leva(ctx):
    """Nível 1 da procura de B-roll: se este vídeo é um anúncio de uma busca por leva (aba Busca de B-roll), devolve (nome, leva)
    e os clipes com a etiqueta dele vêm primeiro. Escolhido no "+ Novo vídeo" (anuncio = "leva|nome", ou "nenhum"); sem escolha,
    reconhece pela fala entre os anúncios da pasta escolhida."""
    origem = ctx.ped.get("anuncio_origem")
    if origem is not None:
        if not isinstance(origem, dict) or not isinstance(origem.get("nome"), str) or not isinstance(origem.get("leva"), str):
            raise Falha("A origem do anúncio precisa informar nome e leva.")
        nome, leva = origem["nome"].strip(), origem["leva"].strip()
        if not nome or not leva or leva in (".", "..") or os.path.basename(leva) != leva or "\\" in leva:
            raise Falha("A origem do anúncio é inválida.")
        return nome, leva
    esc = str(ctx.ped.get("anuncio") or "")
    if esc == "nenhum": return None
    if "|" in esc: leva, nome = esc.split("|", 1); return nome, leva
    F = filtro(ctx); fala = " ".join(w["w"] for w in ctx.W()); melhor = (0.0, None)
    base = os.path.join(biblioteca.BROLLS, F["expert"], F["oferta"]) if F["expert"] else os.path.join(biblioteca.BROLLS, "*", "*")
    for arq in glob.glob(os.path.join(base, ".levas", "*", "transcricoes", "*.json")):
        dl = os.path.dirname(os.path.dirname(arq))
        try:
            ped = json.load(open(os.path.join(dl, "pedido.json"))); nome = ped["anuncios"][int(os.path.basename(arq)[:2]) - 1]["nome"]
            sim = semelhanca(json.load(open(arq))["texto"], fala)
        except (OSError, ValueError, KeyError, IndexError): continue
        if sim > melhor[0]: melhor = (sim, (nome, os.path.basename(dl)))
    return melhor[1] if melhor[0] > 0.3 else None

def pasta_da_oferta(ctx):
    """(expert, oferta) das pastas de ~/B-rolls deste projeto: o que o "+ Novo vídeo" escolheu, ou a oferta com o mesmo nicho."""
    P = ctx.P(); A = biblioteca.arvore()
    for e in A:
        for o in e["ofertas"]:
            if P.get("expert") == e["expert"] and P.get("oferta") == o["oferta"]: return e["expert"], o["oferta"], [c["nome"] for c in o["categorias"]]
    for e in A:
        for o in e["ofertas"]:
            if P.get("nicho") and biblioteca.normal(o["nicho"]) == biblioteca.normal(P["nicho"]): return e["expert"], o["oferta"], [c["nome"] for c in o["categorias"]]
    return None, None, []

def filtro(ctx):
    """Onde a edição procura B-roll: SÓ a pasta Expert › Oferta escolhida (nicho é plano B de projeto antigo)."""
    ex, of, _ = pasta_da_oferta(ctx)
    if ex: return dict(nicho=None, expert=ex, oferta=of)
    P = ctx.P(); return dict(nicho=P.get("nicho") or P.get("oferta") or None, expert=None, oferta=None)

def disponiveis(ctx):
    """Os clipes que este anúncio pode usar: os livres + os que ele mesmo já reservou.

    Sem a segunda parte, refazer um card depois do encaixe o faria perder os próprios clipes (eles já constam
    como usados por ele). Os reservados por OUTRO anúncio continuam de fora, que é o ponto da reserva."""
    F = filtro(ctx); nome = ctx.P().get("nome")
    L = biblioteca.itens("disponivel", F["nicho"], None, F["expert"], F["oferta"])
    meus = [x for x in biblioteca.itens("usado", F["nicho"], None, F["expert"], F["oferta"])
            if any(u.get("projeto") == nome for u in x.get("usos", []))]
    return sorted(L + meus, key=lambda x: x["id"], reverse=True)

def rotulo_pasta(ctx):
    F = filtro(ctx); return f"{F['expert']} › {F['oferta']}" if F["expert"] else (F["nicho"] or "—")

def guardar_na_oferta(ctx, ids_novos, categoria):
    """Clipes novos de uma busca da edição vão para a pasta da categoria dentro da oferta (como na aba Busca de B-roll)."""
    ex, of, _ = pasta_da_oferta(ctx)
    if not ex or not categoria: return
    for i in ids_novos: biblioteca.mover(i, ex, of, categoria)

def reservar(ctx, ids, onde):
    """Tira da prateleira os clipes que ESTE anúncio acabou de escolher.

    Antes isso só acontecia no render, e no Kanban dois anúncios podiam escolher o mesmo clipe: eles passam pela
    edição um depois do outro, mas nenhum renderizou ainda, então a biblioteca mostrava tudo disponível para os dois.
    Repetir B-roll entre criativos da mesma oferta queima a performance, então a reserva é na ESCOLHA."""
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids: return
    P = ctx.P(); B = biblioteca.ler()["itens"]
    novos = [i for i in ids if i in B and B[i].get("estado") == "disponivel"]
    if novos:
        biblioteca.marcar_usado(novos, P["nome"])
        ctx.est.log(f"{len(novos)} B-roll(s) reservados para este anúncio em '{onde}' (saem da prateleira dos outros)")

def plano_da_leva(ctx):
    """O casamento que a busca já fez para este anúncio: cada imagem que a fala pede e o clipe escolhido para ela.
    A edição parte daqui em vez de refazer o plano do zero — era o mesmo trabalho pago duas vezes."""
    arq = (ctx.ped or {}).get("leva_termos")
    if not arq or not os.path.exists(arq): return []
    try: T = json.load(open(arq, encoding="utf-8"))
    except (OSError, ValueError): return []
    nome = (ctx.ped or {}).get("anuncio") or (ctx.ped or {}).get("nome")
    for a in T.get("por_anuncio", []):
        if a.get("anuncio") == nome:
            return [i for i in a.get("imagens", []) if i.get("o_que")]
    return []

def marcados_do_anuncio(ctx):
    """Os clipes (com ficha, disponíveis) que a busca da leva trouxe para ESTE anúncio."""
    tag = ctx.anuncio()
    if not tag: return []
    nome, leva = tag
    return [x["id"] for x in disponiveis(ctx) if nome in x.get("anuncios", []) and leva in x.get("levas", []) and x.get("estudo")]

PROC_PLANO = """O trabalho tem dois passos, nesta mesma conversa:
1. **PLANO** — onde entra cada letreiro, cada efeito e cada MOMENTO de B-roll (formato + o que precisa aparecer). Ainda
   sem escolher clipe: os clipes vêm depois, da biblioteca ou de busca nova.
2. **MONTAGEM** — você recebe o clipe escolhido para cada momento (com a ficha dele: trechos limpos, onde aparece texto,
   onde está o assunto) e devolve a edição final. Depois vem a conferência automática das regras.

A DISTRIBUIÇÃO (onde entra cada letreiro e cada B-roll, quanto fica, em que formato, onde a pessoa respira sozinha) é
decidida no PLANO, pela fala. É a parte mais importante: capriche nela como no exemplo aprovado mais abaixo."""

PROC_BP = """O trabalho tem três passos, nesta mesma conversa, na ordem de um editor que já tem os clipes na mão:
1. **B-ROLL PRIMEIRO** — você recebe a fala e as FICHAS dos clipes da biblioteca (o olhar de um editor sobre cada clipe: o
   que acontece em cada trecho, melhor momento, notas, formatos em que funciona, onde tem texto, o que ilustra). Coloque
   cada B-roll onde um clipe encaixa MELHOR na fala: literal, bonito, no formato certo. Não force: onde nenhum clipe está à
   altura, deixe sem e anote em `faltas` o que precisaria aparecer. Deixe a pessoa respirar entre os B-rolls: os letreiros
   entram nesses intervalos depois.
2. **REVISÃO** — o duplo check: você recebe os trechos que ficaram tempo demais sem B-roll e as suas faltas. Para cada um:
   um clipe da lista que você ainda não usou, uma busca específica no TikTok (o que vier é estudado e volta para você) ou
   deixar sem (o letreiro ou um efeito segura).
3. **LETREIROS E EFEITOS** — com os B-rolls fixados, você planeja os letreiros (palavra-chave, destaques, checks) e os
   efeitos (modo escuro, P&B) nos intervalos com a pessoa.
Depois vem a conferência automática das regras do modelo; o que quebrar volta para você corrigir.

Como escolher o clipe (lendo a ficha):
- mostra LITERALMENTE o que a fala diz ali ("ilustra" ajuda); nada de imagem genérica para preencher;
- entre clipes parecidos, decida pelas notas (assunto e luz primeiro) e pelo ângulo que a fala pede; evite ponto fraco
  que caia no trecho usado;
- `ini` dentro de um trecho limpo que caiba a cena, de preferência começando no melhor momento;
- só num formato que a ficha diz que funciona; no canto, `lado` = o lado livre da ficha; `enquadra` pela ficha;
- `card` serve para clipe com texto atravessando o quadro: a ficha dá a região limpa e só ela entra (pode ficar quadrado
  ou vertical), com a pessoa atrás;
- `recorte` tira a faixa de texto do topo/base (o sistema ajusta pela faixa exata da ficha);
- nunca o mesmo trecho do mesmo clipe duas vezes; cheia/canto/dividida pedem clipe vertical.
Neste modelo não há lista de "momentos": você escreve direto os `brolls` (formato, em, ate, broll, ini, …)."""

def sistema_edicao(E, nome, exemplo=True, processo=None):
    leia = open(os.path.join(SKILL, "estilos", nome, "LEIA.md"), encoding="utf-8").read()
    est = "\n".join(f"- `{k}`: {v.get('rotulo', k)} ({'serifada itálica' if v['familia'] == 'serif' else 'sem serifa'}, {v['px']}px)"
                    for k, v in E["letreiro"]["estilos"].items())
    import regras
    reg = "\n".join("- " + l for l in regras.texto(E.get("regras"))) or "(sem regras extras)"
    ex = os.path.join(SKILL, "estilos", nome, "exemplo.md")
    if not exemplo: txt_ex = "(sem exemplo nesta edição: siga o guia e as regras do modelo)"
    else: txt_ex = open(ex, encoding="utf-8").read() if os.path.exists(ex) else "(este modelo ainda não tem exemplo aprovado)"
    return prompt("edicao", NOME_ESTILO=E["nome"], LEIA=leia, ESTILOS=est, REGRAS=reg, EXEMPLO=txt_ex, PROCESSO=processo or PROC_PLANO)

def plano_para_ops(pl):
    """Plano (momentos sem B-roll ainda) no formato da montagem, com um B-roll de mentira por momento, para conferir ritmo."""
    br = [dict(formato=m["formato"], em=m["em"], ate=m["ate"], broll=f"SLOT-{k + 1}", ini=0, trans=m["trans"], saida=m["saida"],
               lado=m.get("lado"), pb=m.get("pb"), celeb=m.get("celeb"), fila=m.get("fila")) for k, m in enumerate(pl["momentos"])]
    return dict(letreiros=pl["letreiros"], brolls=br, efeitos=pl["efeitos"])

def relatorio_plano(ctx, pl):
    E = comum.estilo(ctx.P()["estilo"]); O = plano_para_ops(pl); W = ctx.W(); rels = {}
    falsos = {f"SLOT-{k + 1}": dict(id=f"SLOT-{k + 1}", src="", dur=999, w=1080, h=1920) for k in range(len(pl["momentos"]))}
    for v, segs in ctx.versoes_segs():
        _, rel = ops.construir(O, W, segs, ops.ler_mapa(ctx.vd(v))[1], falsos, E); rels[v] = rel
    return rels

def texto_rel(rels): return re.sub(r"B-roll (\d+)", r"momento \1", ops.relatorio_texto(rels))

def et_plano(ctx):
    P = ctx.P(); E = comum.estilo(P["estilo"]); est = list(E["letreiro"]["estilos"]); schema = sch_plano(est)
    durs = " · ".join(f"{v['nome']}: {fmt(ops.ler_mapa(ctx.vd(v['id']))[1])}" for v in P["versoes"])
    if not ctx.usa_exemplo(): ctx.est.log("este vídeo é o mesmo do exemplo aprovado do modelo: o exemplo fica de fora e a edição sai do zero")
    conv = ctx.conv_edicao = ctx.cl.conversa(sistema_edicao(E, P["estilo"], ctx.usa_exemplo()), esforco="high")
    pl = ctx.pedir(conv, [texto(f"# Vídeo: {P['nome']}\nVersões: {durs}\nPasta de B-roll: {rotulo_pasta(ctx)}\n\n"
                                f"# Transcrição (número:palavra · ✂ = corte entre as palavras)\n\n{ctx.montagem()}"),
                          texto("PASSO 1 — PLANO. Planeje a edição inteira deste vídeo no modelo acima: letreiros, efeitos e os MOMENTOS de B-roll "
                                "(ainda sem escolher clipe: diga o formato, o que precisa aparecer e os termos de busca em inglês). "
                                "Siga o ritmo e as regras do modelo e a lógica da edição aprovada (exemplo): a distribuição sai daqui.")], schema, "Plano da edição", max_tokens=96000)
    for rodada in range(3):
        rels = relatorio_plano(ctx, pl); er = sum(len(r["erros"]) for r in rels.values()); av = sum(len(r["avisos"]) for r in rels.values())
        ctx.est.log(f"conferência do plano: {er} erro(s), {av} aviso(s)")
        if (er == 0 and (av == 0 or rodada > 0)) or rodada == 2: break
        r = ctx.pedir(conv, [texto("Conferência automática do plano (momento N = o N-ésimo da sua lista de momentos; letreiro N idem; "
                                   "'regra do modelo' = regra de ritmo do modelo, é obrigatória).\n\n" + texto_rel(rels) +
                                   "\n\nCorrija os erros e os avisos que fizerem sentido. Devolva o plano COMPLETO.")], schema, "Correção do plano", max_tokens=96000)
        for m in r["mudancas"][:6]: ctx.est.log("  · " + m)
        if r["ok"] and not (r["letreiros"] or r["momentos"]): break
        if len(r["momentos"]) + len(r["letreiros"]) >= 0.6 * (len(pl["momentos"]) + len(pl["letreiros"])): pl = r
    ctx.aux("plano", pl)
    return f"{len(pl['letreiros'])} letreiros · {len(pl['momentos'])} momentos de B-roll · {len(pl['efeitos'])} efeitos"

# ================================================================ 5. B-roll
def duracoes_momentos(ctx, pl):
    """Quanto tempo cada momento fica na tela (o maior entre as versões)."""
    E = comum.estilo(ctx.P()["estilo"]); O = plano_para_ops(pl); W = ctx.W(); dur = {}
    falsos = {f"SLOT-{k + 1}": dict(id=f"SLOT-{k + 1}", src="", dur=999, w=1080, h=1920) for k in range(len(pl["momentos"]))}
    for v, segs in ctx.versoes_segs():
        res, _ = ops.construir(O, W, segs, ops.ler_mapa(ctx.vd(v))[1], falsos, E)
        for c in res["cenas"]: k = c["ia_op"] - 1; dur[k] = max(dur.get(k, 0), c["fim"] - c["ini"])
    return dur

def fala_do_momento(ctx, m):
    W = ctx.W(); a, b = m["em"], m["ate"] if m.get("ate") is not None else m["em"] + 6
    return " ".join(w["w"] for w in W[max(0, a):min(len(W), b + 1)])

def lista_momentos(ctx, pl, durs, quais):
    return "\n".join(f"momento {k + 1} · {pl['momentos'][k]['formato']}{' (horizontal)' if pl['momentos'][k].get('horizontal') else ''} · "
                     f"{durs.get(k, 0):.1f}s na tela · fala: \"{fala_do_momento(ctx, pl['momentos'][k])[:140]}\" · precisa: {pl['momentos'][k]['imagem']}"
                     for k in quais)

OLHO = dict(ok=False)                                     # o Gemini respondeu no teste do começo? (YouTube e conferência opcional)
ESTUDA = dict(ok=True)                                    # dá para estudar clipe novo (Claude sempre; vira False se o estudo parar)

def candidatos(pl, quais, F, extra=(), n_por=10, maximo=90):
    estudados = ESTUDA["ok"]                             # se o estudo parou (conta sem crédito), vale o título e o termo da busca
    ids, vistos = [], set()
    for i in extra:
        if i not in vistos: vistos.add(i); ids.append(i)
    for k in quais:
        m = pl["momentos"][k]
        for x in biblioteca.procurar(m["imagem"] + " " + " ".join(m["termos"]), n=n_por, so_estudados=estudados, **F):
            if x["id"] not in vistos: vistos.add(x["id"]); ids.append(x["id"])
    B = biblioteca.ler()["itens"]
    return [i for i in ids if i in B and B[i]["estado"] == "disponivel" and (B[i].get("estudo") or not estudados)][:maximo]

def ajusta_escolha(ctx, e, dur, formato=None):
    """Garante que o trecho usado (ini..ini+dur) cai num trecho limpo da ficha do clipe, e recorta legenda queimada no topo/base."""
    x = biblioteca.item(e["broll"]); es = x.get("estudo") or {}; limpos = es.get("trechos_limpos", []); total = x.get("dur") or 0
    if formato == "card":                                 # no card vale o trecho em que o RECORTE fica limpo
        limpos = ((es.get("formatos") or {}).get("card") or {}).get("trechos") or limpos
    ini = max(0.0, float(e.get("ini") or 0)); dentro = lambda t: t["ini"] - 0.2 <= ini and ini + dur <= t["fim"] + 0.2
    if limpos and not any(dentro(t) for t in limpos):
        cabe = [t for t in limpos if t["fim"] - t["ini"] >= dur - 0.2]; mm = es.get("melhor_momento") or {}
        perto = lambda t: abs((t["ini"] + t["fim"]) / 2 - (mm.get("ini", 0) + mm.get("fim", 0)) / 2) if mm else -(t["fim"] - t["ini"])
        melhor = min(cabe or limpos, key=perto)                  # o trecho limpo mais perto do melhor momento da ficha
        novo = round(max(melhor["ini"] + 0.15, min(mm.get("ini", melhor["ini"]), melhor["fim"] - dur)) if mm else melhor["ini"] + 0.15, 2)
        ctx.est.log(f"  · {x['id']}: início {ini:.1f}s saía do trecho limpo; usei {novo:.1f}s ({melhor['ini']:.1f}–{melhor['fim']:.1f}s)"); ini = novo
    if total and ini + dur > total: ini = max(0.0, round(total - dur - 0.1, 2))
    rec = e.get("recorte") or "nenhum"; onde = {t["onde"] for t in es.get("texto_na_tela", []) if t["ini"] < ini + dur and t["fim"] > ini}
    if "topo" in onde and rec in ("nenhum", "tira_base"): rec = "tira_topo_e_base" if rec == "tira_base" else "tira_topo"
    if "base" in onde and rec in ("nenhum", "tira_topo"): rec = "tira_topo_e_base" if rec == "tira_topo" else "tira_base"
    if "meio" in onde: ctx.est.log(f"  · {x['id']}: tem texto no meio do quadro entre {ini:.1f} e {ini + dur:.1f}s (conferir)")
    return dict(broll=x["id"], ini=round(ini, 2), recorte=rec, enquadra=e.get("enquadra") or es.get("posicao_assunto") or "meio")

def youtube_para_biblioteca(ctx, y, pl, nicho):
    """Um pedido de corte horizontal: busca -> Claude escolhe pelos títulos e capas -> acha o trecho (Gemini assistindo, ou o
    Claude pelos 24 quadros com o segundo de cada um) -> baixa só o trecho -> estuda."""
    if not buscas_permitidas(ctx.ped):
        raise Falha("Este anúncio deve usar somente os B-rolls já existentes; novas buscas estão desativadas.")
    est = ctx.est; pasta = os.path.join(ctx.d, "auto", "youtube", slug(y["nome"])); feitos = []
    cands = youtube.buscar(y["termos"], 10)
    if not cands: est.log(f"YouTube · {y['nome']}: nada encontrado"); return []
    folha = youtube.folha_capas(cands, os.path.join(pasta, "capas.jpg"))
    lista = "\n".join(f"{c['n']} · {c['dur'] // 60}:{c['dur'] % 60:02d} · {c['views']} views · {c['canal']} · {c['titulo'][:100]}" for c in cands)
    conv = ctx.cl.conversa(prompt("broll"), esforco="medium")
    r = ctx.pedir(conv, [texto(f"# YouTube: {y['nome']}\nPrecisa: {y['pedido']}\nEscolha até 2 vídeos para abrir (canal oficial, "
                               f"filmagem limpa, sem gente falando para a câmera).\n\n{lista}"), ia.imagem(folha)], SCH_YT, f"YouTube · {y['nome']}")
    por_n = {c["n"]: c for c in cands}
    for e in r["escolhidos"][:2]:
        c = por_n.get(e["n"])
        if not c: continue
        try:
            folha_q, ts, dur_q = youtube.quadros(c["id"], pasta)
            if OLHO["ok"]:
                tr = (gemini.trechos_na_folha(folha_q, ts, dur_q, y["pedido"], 1, ao_cobrar=ctx.cobrou_gemini, log=est.log).get("trechos") or [])
                t = dict(tr[0], serve=True) if tr else dict(serve=False, problemas=["nada limpo nos quadros"])
            else:
                t = ctx.pedir(ctx.cl.conversa(prompt("broll"), esforco="low"),
                              [ia.imagem(folha_q), texto(f"Quadros do vídeo '{c['titulo'][:90]}' ({dur_q:.0f} s analisados), com o segundo de cada um. "
                                                         f"Ache UM trecho contínuo de 4 a 10 s que mostre: {y['pedido']}. Imagem de apoio: sem gente "
                                                         "falando para a câmera, sem texto grande, sem vinheta/logo/tela de inscrição, sem corte no "
                                                         "meio (os quadros de dentro do trecho têm que ser da mesma cena). ini/fim em segundos. "
                                                         "Se nada servir, serve = false.")], SCH_TRECHO, f"YouTube · trecho de {c['id']}")
            if not t.get("serve"): est.log(f"YouTube · {y['nome']}: vídeo {c['n']} não serviu ({'; '.join(t.get('problemas', []))[:120]})"); continue
            dur = max(4.0, min(10.0, float(t["fim"]) - float(t["ini"])))
            arq = youtube.baixar_para(c["id"], float(t["ini"]), dur, os.path.join(biblioteca.pasta("videos"), f"yt-{c['id']}-{int(t['ini'])}.mp4"))
            i = biblioteca.adicionar(arquivo=arq, fonte="youtube", video_id=c["id"], yt_ini=float(t["ini"]), url=c["url"], autor=c["canal"],
                                     titulo=c["titulo"], termo=y["termos"], busca="youtube", nicho=nicho, views=c["views"])
            guardar_na_oferta(ctx, [i], "Cortes do YouTube")
            biblioteca.estudar_pendentes([i], contexto=y["pedido"], ao_cobrar=ctx.cobrou_estudo, log=est.log)
            est.log(f"YouTube · {y['nome']}: {i} ({c['canal']}, {int(t['ini'])}s)"); feitos.append(i)
        except Exception as ex: est.log(f"YouTube · {y['nome']}: vídeo {c['n']} falhou ({str(ex)[:160]})")
    for f in os.listdir(pasta) if os.path.isdir(pasta) else []:
        if f.startswith("baixa_"): os.remove(os.path.join(pasta, f))
    return feitos

def et_broll(ctx):
    d, est, ped, P = ctx.d, ctx.est, ctx.ped, ctx.P(); pl = ctx.aux("plano"); nicho = P.get("nicho") or P.get("oferta") or ""
    durs = duracoes_momentos(ctx, pl); todos = list(range(len(pl["momentos"]))); escolhas = {}
    pode_tt = bool(buscas_permitidas(ped) and ped.get("buscar_tiktok", True) and custos.token_apify())
    pode_yt = bool(buscas_permitidas(ped) and ped.get("buscar_youtube", True) and youtube.disponivel())
    pend = [x["id"] for x in disponiveis(ctx) if not x.get("estudo")]
    est.log(f"B-roll só da pasta {rotulo_pasta(ctx)}: 1º os clipes com a etiqueta do anúncio, 2º o resto da pasta"
            + ("; somente arquivos já salvos, sem novas buscas" if not buscas_permitidas(ped) else ", 3º busca no TikTok se faltar"))
    if pend:                                              # clipes da pasta ainda sem ficha (importados, buscas antigas): uma vez só
        est.log(f"estudando {len(pend)} clipe(s) da biblioteca que ainda não tinham ficha ({biblioteca.motor_estudo()}; fica guardado)…")
        erros = []
        biblioteca.estudar_pendentes(pend, contexto=nicho, ao_cobrar=ctx.cobrou_estudo, log=lambda m: (erros.append(m), est.log(m)))
        if any("o estudo parou" in m for m in erros): ESTUDA["ok"] = False; est.log("sem estudo: a escolha fica pelo título e pelo termo da busca")
    conv = ctx.cl.conversa(prompt("broll"), esforco="high")
    def escolher(quais, cands, primeira):
        B = biblioteca.ler()["itens"]
        msg = [texto(f"# Momentos que precisam de B-roll\n\n{lista_momentos(ctx, pl, durs, quais)}"),
               texto("# Biblioteca (a ficha de cada clipe; 'limpos' = trechos sem texto, sem fala para a câmera, sem corte)\n\n"
                     + ("\n".join(biblioteca.resumo_estudo(B[i]) for i in cands) or "(nada que combine)")),
               texto((f"Buscas novas no TikTok permitidas: {MAX_BUSCAS if pode_tt else 0}. Pedidos ao YouTube permitidos: {MAX_YOUTUBE if pode_yt else 0}.\n"
                      if primeira else "Estas são as opções depois das buscas. Não peça novas buscas (listas vazias).\n")
                     + "Para cada momento: escolha o clipe e o segundo de início (ini) DENTRO de um trecho limpo que caiba a duração do momento, "
                       "ou deixe broll = null e peça busca.")]
        r = ctx.pedir(conv, msg, sch_escolha(cands), "B-roll: escolha" if not primeira else "B-roll: biblioteca e buscas")
        ok = 0
        for e in r["escolhas"]:
            k = e["momento"] - 1
            if k in quais and e.get("broll") in cands and k not in escolhas:
                escolhas[k] = ajusta_escolha(ctx, e, durs.get(k, 3.0), pl["momentos"][k].get("formato")); ok += 1
        return r, ok
    cands = candidatos(pl, todos, filtro(ctx), extra=marcados_do_anuncio(ctx))
    est.log(f"biblioteca: {len(cands)} clipe(s) disponíveis combinam com os {len(todos)} momentos")
    r, ok = escolher(todos, cands, True); est.log(f"{ok} momento(s) resolvidos com a biblioteca")
    buscas = [b for b in r["buscas"] if b["termos"]][:MAX_BUSCAS] if pode_tt else []
    yts = r["youtube"][:MAX_YOUTUBE] if pode_yt else []
    novos, lock = [], threading.Lock()
    def uma_busca(b):
        try:
            if not buscas_permitidas(ped): raise Falha("Novas buscas desativadas para este anúncio.")
            ex, of, _ = pasta_da_oferta(ctx)
            res = tiktok.buscar(d, b["nome"], b["termos"][:3], n=15, estudar=ESTUDA["ok"], log=est.log, ao_cobrar_estudo=ctx.cobrou_estudo,
                                campos=dict(expert=ex, oferta=of, categoria=b["nome"].capitalize()) if ex else None)
            guardar_na_oferta(ctx, res["novos"], b["nome"].capitalize())
            with lock: novos.extend(res["ids"])
        except Exception as ex: est.log(f"busca '{b['nome']}' não deu certo: {str(ex)[:200]}")
        finally: est.custo(custos.resumo(d))
    def um_yt(y):
        try:
            ids = youtube_para_biblioteca(ctx, y, pl, nicho)
            with lock: novos.extend(ids)
        except Exception as ex: est.log(f"YouTube '{y['nome']}' não deu certo: {str(ex)[:200]}")
    if buscas or yts:
        est.log(f"{len(buscas)} busca(s) no TikTok · {len(yts)} pedido(s) ao YouTube")
        with ThreadPoolExecutor(4) as ex: list(ex.map(lambda f: f[0](f[1]), [(uma_busca, b) for b in buscas] + [(um_yt, y) for y in yts]))
    faltam = [k for k in todos if k not in escolhas]
    if faltam and novos:
        c2 = candidatos(pl, faltam, filtro(ctx), extra=novos, maximo=110)
        c2 = [i for i in c2 if i not in {e["broll"] for e in escolhas.values()}]
        _, ok2 = escolher(faltam, c2, False); est.log(f"{ok2} momento(s) resolvidos com o que veio das buscas")
    faltam = [k + 1 for k in todos if k not in escolhas]
    if faltam: est.log(f"momento(s) sem B-roll (saem da edição ou mudam na montagem): {', '.join(map(str, faltam))}")
    ctx.aux("escolhas", {str(k): v for k, v in escolhas.items()})
    reservar(ctx, [e.get("broll") for e in escolhas.values()], "escolha do B-roll")
    reserva = candidatos(pl, todos, filtro(ctx), n_por=3, maximo=30)
    P = ctx.P(); P["biblioteca"] = sorted({e["broll"] for e in escolhas.values()} | set(reserva)); ctx.grava_P(P)
    rodar([PY, ESTUDIO, "acervo", d], est, "acervo")
    return f"{len(escolhas)}/{len(todos)} momentos com B-roll · {len(set(novos))} clipe(s) novo(s) na biblioteca"

# ================================================================ 6. montagem
def ctx_edicao(ctx):
    P = ctx.P(); E = comum.estilo(P["estilo"]); ac = ctx.acervo()
    return E, ac, sch_edicao(list(E["letreiro"]["estilos"]), [x["id"] for x in ac])

def aplicar(ctx):
    rel = os.path.join(ctx.d, "auto", "relatorio_edicao.json")
    rodar([PY, ESTUDIO, "plano", ctx.d, "--forcar", "--relatorio", rel], ctx.est, "plano")
    return json.load(open(rel))

def total(O): return len(O.get("letreiros", [])) + len(O.get("brolls", [])) + len(O.get("efeitos", []))

def aceitar(ctx, O, r):
    novo = dict(letreiros=r["letreiros"], brolls=r["brolls"], efeitos=r["efeitos"])
    if O and total(novo) < 0.6 * total(O):
        ctx.est.log(f"a correção veio incompleta ({total(novo)} itens contra {total(O)}); mantive a edição anterior"); return None
    json.dump(novo, open(os.path.join(ctx.d, "edicao_ia.json"), "w"), ensure_ascii=False, indent=1)
    return novo

def conferir_automatico(ctx, conv, schema, O, rodadas=3, avisos=True):
    for rodada in range(rodadas):
        rels = aplicar(ctx); er = sum(len(r["erros"]) for r in rels.values()); av = sum(len(r["avisos"]) for r in rels.values())
        ctx.est.log(f"conferência automática: {er} erro(s), {av} aviso(s)")
        if (er == 0 and (av == 0 or rodada > 0 or not avisos)) or rodada == rodadas - 1: return O, rels
        r = ctx.pedir(conv, [texto("Conferência automática da edição (B-roll N = o N-ésimo da sua lista de brolls; letreiro N idem; 'regra do "
                                   "modelo' é obrigatória).\n\n" + ops.relatorio_texto(rels) + "\n\nCorrija. Devolva a edição COMPLETA.")],
                      schema, "Correção da edição", max_tokens=96000)
        for m in r["mudancas"][:6]: ctx.est.log("  · " + m)
        if r["ok"] and not (r["letreiros"] or r["brolls"]): return O, rels
        O = aceitar(ctx, O, r) or O
    return O, rels

def et_montagem(ctx):
    P = ctx.P(); E, ac, schema = ctx_edicao(ctx); pl = ctx.aux("plano"); esc = {int(k): v for k, v in (ctx.aux("escolhas") or {}).items()}
    brolls = []
    for k, m in enumerate(pl["momentos"]):
        if k in esc: brolls.append(dict({x: m.get(x) for x in ("formato", "em", "ate", "trans", "saida", "lado", "pb", "celeb", "fila")}, **esc[k]))
    O = dict(letreiros=pl["letreiros"], brolls=brolls, efeitos=pl["efeitos"])
    json.dump(O, open(os.path.join(ctx.d, "edicao_ia.json"), "w"), ensure_ascii=False, indent=1)
    rels = aplicar(ctx)
    conv = ctx.conv_edicao
    pre = []
    if conv is None:                                      # retomada: conversa nova, com o plano como ponto de partida
        conv = ctx.conv_edicao = ctx.cl.conversa(sistema_edicao(E, P["estilo"], ctx.usa_exemplo()), esforco="high")
        pre = [texto(f"# Transcrição (número:palavra)\n\n{ctx.montagem()}"), texto("Este foi o seu plano:\n" + json.dumps(pl, ensure_ascii=False))]
    B = biblioteca.ler()["itens"]; usados = {e["broll"] for e in esc.values()}
    info = "\n".join(f"momento {k + 1} → {e['broll']} a partir de {e['ini']:.1f}s · recorte {e['recorte']} · enquadra {e['enquadra']} · "
                     + (biblioteca.resumo_estudo(B[e["broll"]]) if e["broll"] in B else "") for k, e in sorted(esc.items()))
    sobra = "\n".join(biblioteca.resumo_estudo(B[x["id"]]) for x in ac if x["id"] in B and x["id"] not in usados)
    r = ctx.pedir(conv, pre + [texto("PASSO 2 — MONTAGEM. B-roll escolhido para cada momento (com a ficha do clipe):\n" + info),
                               texto("Outros clipes separados para este vídeo (pode trocar):\n" + (sobra or "(nenhum)")),
                               texto("Edição já montada com isso:\n" + json.dumps(O, ensure_ascii=False)),
                               texto("Conferência automática dela:\n" + ops.relatorio_texto(rels)),
                               texto("Revise e devolva a edição FINAL completa: ajuste o que a troca de clipe pede (momento sem B-roll: "
                                     "outro formato, outro clipe, ou tire e deixe um letreiro), mantenha ini dentro dos trechos limpos, "
                                     "use recorte para tirar texto queimado no topo/base e enquadra para a tela dividida.")],
                  schema, "Montagem", max_tokens=96000)
    O = aceitar(ctx, None, r) or O
    O, rels = conferir_automatico(ctx, conv, schema, O)
    return " · ".join(f"{v}: {r['numeros']['letreiros']} letreiros, {r['numeros']['brolls']} B-rolls" for v, r in rels.items())

# ================================================================ modo "B-roll primeiro" (Ultradinâmico Criativo)
def sch_broll(ids):
    return obj(formato=enum(FORMATOS), em=I(), ate=nulo(I()), broll=enum(ids or ["-"]), ini=N(), trans=enum(TRANS), saida=enum(SAIDA),
               lado=nulo(enum(["esq", "dir"])), pb=B(), celeb=B(), fila=nulo(arr(I())), recorte=enum(RECORTES),
               enquadra=enum(["alto", "meio", "baixo"]), motivo=S())
FALTA = obj(em=I(), ate=I(), imagem=S(), termos=arr(S()), formato=enum(FORMATOS), porque=S())
def sch_encaixe(ids): return obj(ok=B(), mudancas=arr(S()), brolls=arr(sch_broll(ids)), faltas=arr(FALTA))
def sch_revisao(ids): return obj(adicionar=arr(sch_broll(ids)), buscas=arr(obj(nome=S(), categoria=S(), termos=arr(S()), lacunas=arr(I()))),
                                 deixar=arr(obj(lacuna=I(), porque=S())))
def sch_adicionar(ids): return obj(adicionar=arr(sch_broll(ids)))
def sch_letreiros(estilos): return obj(ok=B(), mudancas=arr(S()), letreiros=arr(sch_letreiro(estilos)), efeitos=arr(EFEITO))

def fichas_para(ctx, n=110):
    """Os clipes com ficha que mais combinam com ESTE anúncio (a biblioteca inteira não cabe no pedido): procura por pedaços
    da fala e completa com os de nota maior do nicho."""
    marc = marcados_do_anuncio(ctx); W = ctx.W(); pont = {}
    for i in range(0, len(W), 20):
        for k, x in enumerate(biblioteca.procurar(" ".join(w["w"] for w in W[i:i + 25]), n=15, **filtro(ctx))):
            pont[x["id"]] = pont.get(x["id"], 0) + (15 - k)
    ids = [i for i in sorted(pont, key=lambda i: -pont[i]) if i not in marc][:max(0, n - len(marc))]
    if len(marc) + len(ids) < n:
        resto = sorted([x for x in disponiveis(ctx) if x.get("estudo") and x["id"] not in pont and x["id"] not in marc
                        and x.get("serve") is not False], key=lambda x: -(x.get("nota") or 0))
        ids += [x["id"] for x in resto[:n - len(marc) - len(ids)]]
    return marc + ids

def conferir_brolls(ctx, brolls):
    """Regras do modelo só com os B-rolls (antes dos letreiros). Devolve (erros, avisos, lacunas); lacuna = buraco longo sem
    B-roll, com as palavras da fonte que caem nele (vai para a revisão, não é erro aqui)."""
    E = comum.estilo(ctx.P()["estilo"]); ac = {x["id"]: x for x in biblioteca.acervo_de(sorted({b["broll"] for b in brolls}))}
    ac.update({x["id"]: x for x in ctx.acervo()}); W = ctx.W(); erros, avisos, lacunas = [], [], []
    for v, segs in ctx.versoes_segs():
        _, rel = ops.construir(dict(letreiros=[], brolls=brolls, efeitos=[]), W, segs, ops.ler_mapa(ctx.vd(v))[1], ac, E)
        for m in rel["erros"]:
            g = re.search(r"([\d.]+)s\s+[\d.]+s sem B-roll até ([\d.]+)s", m)
            if g:
                a, b = float(g.group(1)), float(g.group(2))
                ws = [k for k in range(len(W)) if (t := ops._t(W, segs, k)) is not None and a <= t < b]
                if ws and not any(set(ws) & set(l["palavras"]) for l in lacunas):
                    lacunas.append(dict(palavras=ws, texto=f"palavras {ws[0]} a {ws[-1]} ({b - a:.0f} s): \"" + " ".join(W[k]["w"] for k in ws)[:260] + "\""))
            else: erros.append(f"versão {v}: {m}")
        avisos += [f"versão {v}: {m}" for m in rel["avisos"] if "sem B-roll nem efeito" not in m and "sem letreiro" not in m]
    return list(dict.fromkeys(erros)), list(dict.fromkeys(avisos)), lacunas

def tira_pre(ctx): pre, ctx.pre_bp = ctx.pre_bp, []; return pre

def conv_bp(ctx):
    """A conversa do modo B-roll primeiro. Numa retomada, nasce de novo com a fala, as fichas e o que já foi decidido."""
    if ctx.conv_bp: return ctx.conv_bp
    P = ctx.P(); E = comum.estilo(P["estilo"]); enc = ctx.aux("encaixe") or {}; B = biblioteca.ler()["itens"]
    ctx.conv_bp = ctx.cl.conversa(sistema_edicao(E, P["estilo"], ctx.usa_exemplo(), PROC_BP), esforco="high")
    ctx.pre_bp = [texto(f"# Transcrição (número:palavra · ✂ = corte entre as palavras)\n\n{ctx.montagem()}"),
                  texto("# Biblioteca (fichas)\n\n" + "\n".join(biblioteca.resumo_estudo(B[i]) for i in enc.get("ids", []) if i in B)),
                  texto("B-rolls já decididos (continue daqui):\n" + json.dumps(enc.get("brolls", []), ensure_ascii=False))]
    return ctx.conv_bp

def corrigir_brolls(ctx, conv, brolls, ids, rotulo):
    for rodada in range(2):
        erros, avisos, _ = conferir_brolls(ctx, brolls)
        ctx.est.log(f"conferência dos B-rolls: {len(erros)} erro(s), {len(avisos)} aviso(s)")
        if not erros and (not avisos or rodada > 0): break
        r = ctx.pedir(conv, tira_pre(ctx) + [texto("Conferência automática dos B-rolls (B-roll N = o N-ésimo da sua lista; 'regra do modelo' é "
                                                   "obrigatória; trecho longo sem B-roll fica para a revisão, não precisa resolver agora):\n"
                                                   + "\n".join(f"- {x}" for x in erros + avisos) + "\n\nCorrija e devolva a lista COMPLETA de brolls.")],
                      sch_encaixe(ids), rotulo + " (correção)", max_tokens=64000)
        for m in r["mudancas"][:6]: ctx.est.log("  · " + m)
        if r["brolls"] and len(r["brolls"]) >= 0.6 * len(brolls): brolls = r["brolls"]
        else: break
    return brolls

def et_encaixe(ctx):
    d, est, P = ctx.d, ctx.est, ctx.P(); E = comum.estilo(P["estilo"]); nicho = P.get("nicho") or P.get("oferta") or ""
    pend = [x["id"] for x in disponiveis(ctx) if not x.get("estudo")]
    est.log(f"B-roll só da pasta {rotulo_pasta(ctx)}: 1º os clipes com a etiqueta do anúncio, 2º o resto da pasta"
            + ("; somente arquivos já salvos, sem novas buscas" if not buscas_permitidas(ctx.ped) else ", 3º busca no TikTok se faltar"))
    if pend:                                              # clipes da pasta ainda sem ficha: o Gemini estuda uma vez
        est.log(f"estudando {len(pend)} clipe(s) da biblioteca que ainda não tinham ficha ({biblioteca.motor_estudo()}; fica guardado)…")
        erros = []
        biblioteca.estudar_pendentes(pend, contexto=nicho, ao_cobrar=ctx.cobrou_estudo, log=lambda m: (erros.append(m), est.log(m)))
        if any("o estudo parou" in m for m in erros): ESTUDA["ok"] = False
    ids = fichas_para(ctx); B = biblioteca.ler()["itens"]; marc = set(marcados_do_anuncio(ctx))
    est.log(f"biblioteca: {len(ids)} clipe(s) com ficha separados para este anúncio" + (f" ({len(marc)} com a etiqueta dele)" if marc else ""))
    plano = plano_da_leva(ctx)
    if plano:
        casados = sum(1 for i in plano if i.get("clipe"))
        est.log(f"a busca já planejou {len(plano)} imagem(ns) para este anúncio ({casados} com clipe casado): a edição parte daí")
    ctx.conv_bp = None; ctx.pre_bp = []
    conv = ctx.conv_bp = ctx.cl.conversa(sistema_edicao(E, P["estilo"], ctx.usa_exemplo(), PROC_BP), esforco="high")
    durs = " · ".join(f"{v['nome']}: {fmt(ops.ler_mapa(ctx.vd(v['id']))[1])}" for v in P["versoes"])
    r = ctx.pedir(conv, [texto(f"# Vídeo: {P['nome']}\nVersões: {durs}\nPasta de B-roll: {rotulo_pasta(ctx)}\n\n"
                               f"# Transcrição (número:palavra · ✂ = corte entre as palavras)\n\n{ctx.montagem()}"),
                         texto("# Biblioteca (fichas dos clipes disponíveis que combinam com este anúncio"
                               + ("; ★ = buscado para ESTE anúncio na busca de B-roll: olhe estes primeiro" if marc else "") + ")\n\n"
                               + ("\n".join(("★ " if i in marc else "") + biblioteca.resumo_estudo(B[i]) for i in ids) or "(nenhum clipe com ficha: tudo vira falta)")),
                         *([texto("# Plano da busca (já feito para este anúncio, com a diretriz deste modelo)\n\n"
                                  + "\n".join(f"- {i['o_que']}" + (f" → {i['formato']}" if i.get("formato") else "")
                                               + (f" → clipe {i['clipe']}" if i.get("clipe") else " → (a busca não achou clipe)")
                                               for i in plano)
                                  + "\n\nUse isto como ponto de partida: o trabalho de ler a fala e decidir que imagem cabe em cada "
                                    "momento já foi feito. Confira contra as fichas e mude só o que estiver errado — clipe que não "
                                    "ilustra, formato que a ficha não permite, ou trecho limpo curto demais.")] if plano else []),
                         texto("PASSO 1 — B-ROLL PRIMEIRO. Coloque os B-rolls onde um clipe da biblioteca encaixa melhor na fala, seguindo "
                               "o ritmo e as regras do modelo e a lógica da edição aprovada (exemplo). Deixe espaço para os letreiros. "
                               "Onde a fala pede imagem e nenhum clipe está à altura, anote em `faltas`.")],
                  sch_encaixe(ids), "B-roll: encaixe", max_tokens=96000)
    brolls = corrigir_brolls(ctx, conv, r["brolls"], ids, "B-roll: encaixe")
    ctx.aux("encaixe", dict(ids=ids, brolls=brolls, faltas=r["faltas"]))
    reservar(ctx, [b.get("broll") for b in brolls], "encaixe")
    return f"{len(brolls)} B-roll(s) encaixados da biblioteca · {len(r['faltas'])} falta(s) anotada(s)"

def et_revisao(ctx):
    d, est, ped, P = ctx.d, ctx.est, ctx.ped, ctx.P(); nicho = P.get("nicho") or P.get("oferta") or ""
    enc = ctx.aux("encaixe"); conv = conv_bp(ctx); ids = list(enc["ids"]); brolls = list(enc["brolls"])
    _, _, lac = conferir_brolls(ctx, brolls)
    itens = [f"lacuna {k + 1}: {l['texto']} — trecho longo sem B-roll" for k, l in enumerate(lac)]
    itens += [f"lacuna {len(lac) + k + 1}: palavras {f['em']} a {f['ate']} — falta que você anotou: {f['imagem']} ({f['porque']})"
              for k, f in enumerate(enc.get("faltas", []))]
    novos, feitos = [], 0
    if itens:
        pode_tt = bool(buscas_permitidas(ped) and ped.get("buscar_tiktok", True) and custos.token_apify())
        r = ctx.pedir(conv, tira_pre(ctx) + [texto("PASSO 2 — REVISÃO (duplo check). Ficaram sem B-roll:\n" + "\n".join(itens) + "\n\n"
                                                   f"Para cada lacuna: (a) coloque um clipe da biblioteca que você ainda não usou (`adicionar`); "
                                                   f"(b) peça uma busca específica no TikTok (`buscas`: nome curto, a `categoria` (pasta) da oferta "
                                                   f"onde os clipes vão ficar — uma destas se servir: {', '.join(pasta_da_oferta(ctx)[2]) or 'nenhuma ainda'}; "
                                                   f"senão um nome novo curto —, 2 ou 3 termos EM INGLÊS que descrevem a CENA, e os números das lacunas; "
                                                   f"no máximo {MAX_BUSCAS_REVISAO if pode_tt else 0}, só "
                                                   "para fala forte e literal); ou (c) deixe sem (`deixar`: o letreiro ou um efeito segura). "
                                                   "Ao adicionar, respeite o respiro e os formatos do modelo.")],
                      sch_revisao(ids), "Revisão dos B-rolls", max_tokens=64000)
        brolls += r["adicionar"]; feitos = len(r["adicionar"])
        buscas = [b for b in r["buscas"] if b["termos"]][:MAX_BUSCAS_REVISAO] if pode_tt else []
        if buscas:
            est.log(f"{len(buscas)} busca(s) específica(s) no TikTok: " + ", ".join(b["nome"] for b in buscas))
            lock = threading.Lock()
            def uma(b):
                try:
                    if not buscas_permitidas(ped): raise Falha("Novas buscas desativadas para este anúncio.")
                    ex, of, _ = pasta_da_oferta(ctx)
                    res = tiktok.buscar(d, b["nome"], b["termos"][:3], n=15, estudar=ESTUDA["ok"], log=est.log, ao_cobrar_estudo=ctx.cobrou_estudo,
                                        campos=dict(expert=ex, oferta=of, categoria=b.get("categoria") or b["nome"]) if ex else None)
                    guardar_na_oferta(ctx, res["novos"], b.get("categoria") or b["nome"])
                    with lock: novos.extend(res["ids"])
                except Exception as ex: est.log(f"busca '{b['nome']}' não deu certo: {str(ex)[:200]}")
                finally: est.custo(custos.resumo(d))
            with ThreadPoolExecutor(3) as ex: list(ex.map(uma, buscas))
            B = biblioteca.ler()["itens"]; usados = {b["broll"] for b in brolls}
            novos = [i for i in dict.fromkeys(novos) if i in B and B[i]["estado"] == "disponivel" and B[i].get("estudo") and i not in usados]
            if novos:
                pedidas = {n for b in buscas for n in b["lacunas"]}
                r2 = ctx.pedir(conv, [texto("Clipes novos das buscas (já estudados):\n" + "\n".join(biblioteca.resumo_estudo(B[i]) for i in novos)
                                            + f"\n\nColoque nas lacunas {sorted(pedidas)} os que encaixarem BEM (`adicionar`); o que não "
                                              "encaixar, deixe de fora.")], sch_adicionar(ids + novos), "Revisão: clipes novos", max_tokens=64000)
                brolls += r2["adicionar"]; feitos += len(r2["adicionar"]); ids += novos
        brolls.sort(key=lambda b: b["em"])
        brolls = corrigir_brolls(ctx, conv, brolls, ids, "Revisão")
    enc.update(brolls=brolls, ids=ids); ctx.aux("encaixe", enc)
    reservar(ctx, [b.get("broll") for b in brolls], "revisão")      # a revisão pode ter trazido clipe novo
    P = ctx.P(); P["biblioteca"] = sorted({b["broll"] for b in brolls} | set(ids[:40])); ctx.grava_P(P)
    rodar([PY, ESTUDIO, "acervo", d], est, "acervo")
    return (f"{len(lac)} trecho(s) longo(s) e {len(enc.get('faltas', []))} falta(s) revisados · {feitos} B-roll(s) acrescentados · "
            f"{len(novos)} clipe(s) novo(s) das buscas" if itens else "nenhum buraco: nada a revisar")

def et_letreiros(ctx):
    P = ctx.P(); E, ac, schema = ctx_edicao(ctx); est = list(E["letreiro"]["estilos"]); enc = ctx.aux("encaixe"); conv = conv_bp(ctx)
    W = ctx.W()
    lista = "\n".join(f"B-roll {k + 1}: {b['formato']} das palavras {b['em']} a {b['ate']} ({b['broll']}) — \""
                      + " ".join(w["w"] for w in W[b["em"]:(b["ate"] if b.get("ate") is not None else b["em"] + 6) + 1])[:120] + "\""
                      for k, b in enumerate(enc["brolls"]))
    r = ctx.pedir(conv, tira_pre(ctx) + [texto("PASSO 3 — LETREIROS E EFEITOS. Os B-rolls estão fixados:\n" + (lista or "(nenhum)") + "\n\n"
                                               "Planeje os letreiros (palavra-chave, destaques, número, checks) e os efeitos (modo escuro, P&B) "
                                               "nos intervalos com a pessoa, na densidade do modelo e como no exemplo aprovado. Letreiro não entra "
                                               "em cima de B-roll (a lista de checks pode) e espera o B-roll sair; no máximo 5 palavras e 4 s.")],
                  sch_letreiros(est), "Letreiros nos intervalos", max_tokens=96000)
    O = dict(letreiros=r["letreiros"], brolls=[{k: v for k, v in b.items() if k != "motivo"} for b in enc["brolls"]], efeitos=r["efeitos"])
    json.dump(O, open(os.path.join(ctx.d, "edicao_ia.json"), "w"), ensure_ascii=False, indent=1)
    ctx.conv_edicao = conv
    O, rels = conferir_automatico(ctx, conv, schema, O)
    return " · ".join(f"{v}: {r['numeros']['letreiros']} letreiros, {r['numeros']['brolls']} B-rolls" for v, r in rels.items())

# ================================================================ 7. conferência do vídeo
INSTR_VISUAL = ("Estes são quadros REAIS do vídeo renderizado com a sua edição (a lista diz, para cada quadro, a versão, o tempo, o que está "
                "na tela e o que está sendo dito)" + "{gem}" + ". Confira: texto do B-roll aparecendo (legenda queimada, marca d'água); "
                "letreiro em cima do rosto ou da boca; letreiro cortado ou ilegível; cabeça cortada na tela dividida; B-roll que não combina "
                "com a fala; B-roll escuro ou com gente falando para a câmera; elementos empilhados ou trocando rápido demais. Para cada "
                "problema, mude o que resolve: ini (outro trecho limpo), recorte, enquadra, outro clipe da lista, formato, texto. Se estiver "
                "bom, ok = true. Se não, ok = false, `mudancas` e a edição COMPLETA corrigida.")

def rotulo(Pl, Wv, t):
    it = [f"B-roll {c.get('ia_op')} ({c['tipo']}, {c['id']} a partir de {c.get('src_ini', 0) + t - c['ini']:.1f}s)" for c in Pl["cenas"] if c["ini"] <= t < c["fim"]]
    it += [f"letreiro {b.get('ia_op')} \"{' / '.join(l[1] for l in b['linhas'] if l[2] <= t)}\"" for b in Pl["blocos"] if b["linhas"][0][2] <= t < b["fim"]]
    it += ["modo escuro" for a, b in Pl.get("escuro", []) if a <= t < b] + ["P&B" for a, b in Pl.get("pb", []) if a <= t < b]
    fala = " ".join(w["w"] for w in Wv if t - 1.2 <= w["t"] <= t + 1.0)
    return (", ".join(it) or "só a pessoa") + f" · fala: \"{fala}\""

def quadros(ctx, rodada):
    """Os momentos que mais dão problema (cada B-roll no começo e no meio, texto atrás da cabeça, dividida, modo escuro e
    letreiros), renderizados pelo motor de verdade, em folhas de 8."""
    P = ctx.P(); lista, vistos = [], set()
    for k, v in enumerate(P["versoes"]):
        vd = ctx.vd(v["id"]); Pl = json.load(open(os.path.join(vd, "plano.json")))
        Wv = ops.palavras(json.load(open(os.path.join(vd, "whisper.json")))); pts = []
        for c in Pl["cenas"]:
            ch = ("b", c.get("ia_op"))
            if ch in vistos: continue
            vistos.add(ch); pts += [c["ini"] + 0.35, (c["ini"] + c["fim"]) / 2]
        for b in Pl["blocos"]:
            ch = ("l", b.get("ia_op"))
            if ch in vistos: continue
            vistos.add(ch); pts.append(b["linhas"][-1][2] + 0.4)
        if k == 0: pts += [(a + b) / 2 for a, b in Pl.get("escuro", [])]
        pts = sorted({round(t, 2) for t in pts if 0 < t < Pl["dur"] - 0.1})
        if len(pts) > 36: pts = [pts[int(i * len(pts) / 36)] for i in range(36)]
        if not pts: continue
        ctx.est.log(f"renderizando {len(pts)} quadros da versão {v['id']} para conferir…")
        for arq in [os.path.join(vd, "prev", f"quadro_{t:07.2f}.jpg") for t in pts]:
            if os.path.exists(arq): os.remove(arq)
        rodar([PY, os.path.join(LIB, "motor.py"), vd, "preview", *[f"{t:.2f}" for t in pts]], rotulo="quadros")
        lista += [(os.path.join(vd, "prev", f"quadro_{t:07.2f}.jpg"), v["id"], t, rotulo(Pl, Wv, t)) for t in pts]
    folhas, desc = [], []
    for j in range(0, len(lista), 8):
        grupo = lista[j:j + 8]; dest = os.path.join(ctx.d, "auto", f"conferencia_{rodada + 1}_{j // 8 + 1}.jpg")
        rodar([PY, os.path.join(LIB, "folhas.py"), dest, "4", "270", "480", *[f"{a}::#{j + i + 1} {v} {t:.1f}s" for i, (a, v, t, _) in enumerate(grupo)]],
              rotulo="folha de quadros")
        folhas.append(dest); desc += [f"#{j + i + 1} · versão {v} · {t:.1f}s · {r}" for i, (a, v, t, r) in enumerate(grupo)]
    return folhas, desc

def hash_planos(ctx):
    h = hashlib.md5()
    for v in ctx.P()["versoes"]: h.update(open(os.path.join(ctx.vd(v["id"]), "plano.json"), "rb").read())
    return h.hexdigest()

def rascunho(ctx, v):
    dest = os.path.join(ctx.d, "auto", f"rascunho_{v}.mp4")
    rodar([PY, os.path.join(LIB, "render.py"), ctx.vd(v), "--saida", dest, "--rascunho"], rotulo="rascunho")
    return dest

def roteiro_do_plano(ctx, v):
    Pl = json.load(open(os.path.join(ctx.vd(v), "plano.json"))); L = []
    for c in Pl["cenas"]: L.append(f"{c['ini']:.1f}–{c['fim']:.1f}s: B-roll {c['tipo']} ({(biblioteca.item(c['id']) or {}).get('descricao', c['id'])})")
    for b in Pl["blocos"]: L.append(f"{b['linhas'][0][2]:.1f}–{b['fim']:.1f}s: letreiro \"{' / '.join(l[1] for l in b['linhas'])}\"")
    for a, b in Pl.get("escuro", []): L.append(f"{a:.1f}–{b:.1f}s: modo escuro")
    return "\n".join(sorted(L, key=lambda s: float(s.split("–")[0])))

def et_conferencia(ctx):
    if not ctx.ped.get("conferir"): return "pulada (o que não ficar bom, você troca no editor)"
    ctx.esperar_preparar()
    P = ctx.P(); E, ac, schema = ctx_edicao(ctx); O = json.load(open(os.path.join(ctx.d, "edicao_ia.json"))); conv = ctx.conv_edicao; pre = []
    if conv is None:
        conv = ctx.conv_edicao = ctx.cl.conversa(sistema_edicao(E, P["estilo"], ctx.usa_exemplo()), esforco="high")
        pre = [texto(f"# Transcrição (número:palavra)\n\n{ctx.montagem()}"), texto("Esta é a edição atual:\n" + json.dumps(O, ensure_ascii=False))]
    B = biblioteca.ler()["itens"]
    opcoes = "\n".join(biblioteca.resumo_estudo(B[x["id"]]) for x in ac if x["id"] in B)
    n = 0
    for rodada in range(2):
        relato = ""
        if rodada == 0 and OLHO["ok"]:
            partes = []
            for v in P["versoes"]:
                ctx.est.log(f"renderizando o rascunho da versão {v['id']} para o Gemini assistir…")
                try:
                    g = gemini.assistir_montagem(rascunho(ctx, v["id"]), roteiro_do_plano(ctx, v["id"]), ao_cobrar=ctx.cobrou_gemini, log=ctx.est.log)
                    probs = g.get("problemas", [])
                    partes.append(f"Versão {v['id']}: " + ("nenhum problema" if not probs else "\n" + "\n".join(
                        f"- {p['segundo']:.1f}s [{p['gravidade']}] {p['o_que']} → {p['sugestao']}" for p in probs)))
                    ctx.est.log(f"Gemini assistiu a versão {v['id']}: {len(probs)} ponto(s)")
                except Exception as ex: ctx.est.log(f"o Gemini não conseguiu assistir a versão {v['id']} ({str(ex)[:160]})")
            if partes: relato = "\n\nO Gemini assistiu o vídeo inteiro, com o áudio, e apontou:\n" + "\n".join(partes)
            ctx.aux("rascunho", dict(hash=hash_planos(ctx)))
        folhas, desc = quadros(ctx, rodada); n += len(desc)
        if not folhas: break
        r = ctx.pedir(conv, pre + [texto(INSTR_VISUAL.replace("{gem}", " e o relato do Gemini, que assistiu o vídeo" if relato else "")
                                         + relato + "\n\nClipes que você pode usar:\n" + opcoes + "\n\n" + "\n".join(desc))]
                      + [ia.imagem(f) for f in folhas], schema, "Conferência visual", max_tokens=96000)
        pre = []
        if r["ok"] or not (r["letreiros"] or r["brolls"]): ctx.est.log("conferência visual: ok"); break
        for m in r["mudancas"][:8]: ctx.est.log("  · " + m)
        O2 = aceitar(ctx, O, r)
        if O2 is None: break
        O, rels = conferir_automatico(ctx, conv, schema, O2, rodadas=2, avisos=False)
    return f"{n} quadros conferidos" + (" · Gemini assistiu o rascunho" if OLHO["ok"] else "")

# ================================================================ 8. render
def et_render(ctx):
    if not ctx.ped.get("renderizar", True): return "pulado: renderize pelo editor quando quiser"
    P = ctx.P(); feitos = []; ras = ctx.aux("rascunho") or {}
    prontos = []                                          # já renderizado depois da última mudança do plano? não refaz
    for v in P["versoes"]:
        vd = ctx.vd(v["id"]); rs = glob.glob(os.path.join(vd, "renders", "*.mp4"))
        pl = os.path.join(vd, "plano.json")
        if rs and os.path.exists(pl) and max(os.path.getmtime(r) for r in rs) >= os.path.getmtime(pl): prontos.append(v["id"])
    if len(prontos) == len(P["versoes"]) and prontos:
        return f"{len(prontos)} vídeo(s) já renderizados depois da última mudança"
    reaproveita = ras.get("hash") == hash_planos(ctx) and all(os.path.exists(os.path.join(ctx.d, "auto", f"rascunho_{v['id']}.mp4")) for v in P["versoes"])
    for v in P["versoes"]:
        vd = ctx.vd(v["id"])
        if reaproveita:                                   # nada mudou depois do rascunho: ele é o vídeo final
            os.makedirs(os.path.join(vd, "renders"), exist_ok=True); rev = 1
            while os.path.exists(os.path.join(vd, "renders", f"{P['nome']} - {v['nome']} rev{rev}.mp4")): rev += 1
            saida = os.path.join(vd, "renders", f"{P['nome']} - {v['nome']} rev{rev}.mp4")
            shutil.move(os.path.join(ctx.d, "auto", f"rascunho_{v['id']}.mp4"), saida)
            Pl = json.load(open(os.path.join(vd, "plano.json")))
            ids = sorted({i for i in (biblioteca.de_src(c["src"]) for c in Pl["cenas"] if c.get("src")) if i})
            if ids: biblioteca.marcar_usado(ids, P["nome"], v["id"])
        else:
            ctx.est.marca("render", "rodando", f"{v['nome']}: começando"); ult = -5
            p = subprocess.Popen([PY, os.path.join(LIB, "render.py"), vd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True); saida = None
            for l in p.stdout:
                if l.startswith("progresso"):
                    pct = int(l.split()[1])
                    if pct >= ult + 5: ult = pct; ctx.est.marca("render", "rodando", f"{v['nome']}: {pct}%")
                elif l.startswith("saida"): saida = l[6:].strip()
            err = p.stderr.read(); p.wait()
            if p.returncode or not saida: raise Falha("render falhou:\n" + err[-600:])
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "3", "-i", saida, "-frames:v", "1", "-vf", "scale=360:-2", os.path.join(vd, "capa.jpg")])
        feitos.append(v["nome"]); ctx.est.log(f"pronto: {saida}")
    return f"{len(feitos)} vídeo(s) prontos" + (" (o rascunho conferido virou o final)" if reaproveita else "")

# ================================================================
def etapas_pedido(ped, bp):
    etapas = ETAPAS_BP if bp else ETAPAS
    return [(i, "Vídeo já cortado" if i == "cortes" and ped.get("video_ja_cortado") else nome) for i, nome in etapas]


def executar_etapas(ctx, ate=None):
    """Ao devolver a edição para revisão, a prévia e as máscaras já estão prontas."""
    est, d, ped = ctx.est, ctx.d, ctx.ped
    if ped.get("video_ja_cortado") and os.path.exists(os.path.join(d, "projeto.json")):
        from video_integral import validar_fonte, versao_pronta
        identidade = validar_fonte(ctx.P(), ped)
        if est.et("cortes")["estado"] == "ok" and not versao_pronta(d, identidade):
            est.marca("cortes", "pendente", "Validando a versão integral deste upload")
    fns = dict(transcricao=et_transcricao, cortes=et_video_integral if ped.get("video_ja_cortado") else et_cortes,
               encaixe=et_encaixe, revisao=et_revisao, letreiros=et_letreiros,
               plano=et_plano, broll=et_broll, montagem=et_montagem, conferencia=et_conferencia, render=et_render)
    ordem = [i for i, _ in ctx.etapas]
    alvo = ordem.index(ate) + 1 if ate in ordem else len(ordem)
    for nome in ordem[:alvo]:
        if nome == "preparar":
            velha = est.et("preparar")["estado"] == "ok" and not all(
                comum.mascaras_ok(os.path.join(d, "versoes", v["id"])) for v in ctx.P()["versoes"])
            if velha: est.log("o vídeo mudou depois do recorte: refazendo as máscaras")
            if est.et("preparar")["estado"] != "ok" or velha: ctx.iniciar_preparar()
            if nome == ate: ctx.esperar_preparar()
            continue
        if nome == "render": ctx.esperar_preparar()
        roda(ctx, nome, fns[nome])
    # Também no --ate letreiros/montagem: a thread não pode morrer ao sair do processo.
    ctx.esperar_preparar()
    return alvo, ordem


def main():
    args = sys.argv[1:]; ate = None
    if "--ate" in args: i = args.index("--ate"); ate = args[i + 1]; del args[i:i + 2]      # roda só até esta etapa (Kanban)
    d = os.path.abspath(args[0]); ped = json.load(open(os.path.join(d, "auto", "pedido.json")))
    bp = comum.estilo(ped.get("estilo") or "ultradinamico").get("modo") == "broll_primeiro"
    etapas = etapas_pedido(ped, bp)
    est = Estado(d, etapas); ctx = Ctx(d, ped, est); ctx.etapas = etapas
    def parar(*_): est.log("cancelado"); est.fim("cancelado", "cancelado por você"); os._exit(1)
    signal.signal(signal.SIGTERM, parar)
    try:
        ordem = [i for i, _ in etapas]; alvo = ordem.index(ate) + 1 if ate in ordem else len(ordem)
        precisa_ia = any(i not in ("transcricao", "preparar") and (i != "cortes" or not ped.get("video_ja_cortado")) for i in ordem[:alvo])
        if precisa_ia:
            ctx.cl = ia.Claude(log=est.log, ao_cobrar=ctx.cobrou)
            quer_gemini = biblioteca.motor_estudo() == "gemini" or ped.get("conferir")
            ok, motivo = gemini.funciona(ao_cobrar=ctx.cobrou_gemini) if (quer_gemini and gemini.chave()) else (False, "não configurado")
            OLHO["ok"] = ok
            if biblioteca.motor_estudo() == "gemini" and not ok:
                biblioteca.MOTOR_FORCADO = "claude-opus-5"; est.log(f"o Gemini não respondeu ({motivo}); o Claude estuda os clipes")
            est.log(f"modelos: {ctx.cl.modelo} (decide) · {biblioteca.motor_estudo()} (estuda cada B-roll novo, uma vez)"
                    + (f" · {gemini.modelo_padrao()} (confere)" if ok and ped.get("conferir") else ""))
        est.custo(custos.resumo(d))
        alvo, ordem = executar_etapas(ctx, ate)
        c = custos.resumo(d)
        if alvo < len(ordem):
            est.fim("pausado", f"parou em '{ate}' como você pediu · Claude US$ {c['claude']:.2f} · Gemini US$ {c['gemini']:.2f} · Apify US$ {c['apify']:.2f}")
            est.log(f"etapa '{ate}' concluída (o resto fica para a próxima fase)")
        else:
            est.fim("pronto", f"pronto em {fmt(time.time() - est.s['inicio'])} · Claude US$ {c['claude']:.2f} · Gemini US$ {c['gemini']:.2f} · Apify US$ {c['apify']:.2f}")
            est.log("edição automática concluída")
    except Exception as e:
        est.log("ERRO: " + str(e)); open(os.path.join(d, "auto", "erro.txt"), "w").write(traceback.format_exc())
        est.fim("erro", str(e)[:1500]); sys.exit(1)

if __name__ == "__main__":
    main()
