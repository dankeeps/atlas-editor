"""Busca de B-roll por leva (aba "Busca de B-roll" do Estúdio).

Um ou vários anúncios -> só transcreve todos -> o Claude lê as falas juntas e define as buscas e as categorias da oferta ->
TikTok (Apify) -> o Gemini estuda cada clipe (ficha) -> os clipes vão para ~/B-rolls/<Expert>/<Oferta>/<Categoria>/ (os
descartados para .descartados) e ganham a etiqueta do anúncio ("Anúncio 01"). Quando esse anúncio for editado, a edição
olha primeiro os clipes com a etiqueta dele e depois o resto da oferta.

  .venv/bin/python lib/leva.py <pasta da leva>
A pasta fica em ~/B-rolls/<Expert>/<Oferta>/.levas/<data-hora>/: pedido.json (expert, oferta, anuncios [{nome, arquivo}],
por_anuncio), transcricoes/<n>.json, estado.json (a aba acompanha), custos.json."""
import os, re, sys, json, time, signal, threading, traceback, subprocess, unicodedata, glob
from concurrent.futures import ThreadPoolExecutor

LIB = os.path.dirname(os.path.abspath(__file__)); SKILL = os.path.dirname(LIB); sys.path.insert(0, LIB)
import biblioteca, custos, tiktok, youtube, gemini, ia, comum, regras
from ia import texto

PY = sys.executable
ETAPAS = [("transcricao", "Transcrição dos anúncios"), ("termos", "Plano de B-roll no template e termos de busca"), ("busca", "Busca dos B-rolls"),
          ("estudo", "Estudo dos B-rolls (Gemini)"), ("organizacao", "Pastas de categoria e etiquetas")]
# Busca agendada no YouTube (sem anúncio — ver main_youtube_agendada): não tem transcrição, e "busca" já
# inclui o planejamento de cada rodada (não é uma etapa fixa de "termos", porque se repete até a duração acabar).
ETAPAS_AGENDADA = [("busca", "Busca no YouTube (por rodadas, até a duração agendada acabar)"),
                    ("estudo", "Estudo dos B-rolls (Gemini)"), ("organizacao", "Pastas de categoria")]
MAX_TOTAL = 18                                             # buscas por leva, no máximo
MAX_TOTAL_AGENDADA = 8                                     # buscas por RODADA da busca agendada (ela repete rodadas)
YT_CANDS, YT_ABRIR, YT_TRECHOS = 10, 3, 3                  # por busca: vídeos listados, vídeos abertos, trechos por vídeo

def S(): return {"type": "string"}
def I(): return {"type": "integer"}
def B(): return {"type": "boolean"}
def arr(x): return {"type": "array", "items": x}
def enum(v): return {"type": "string", "enum": list(v)}
def obj(**p): return {"type": "object", "additionalProperties": False, "required": list(p), "properties": p}

INSTR = """Você prepara a biblioteca de B-roll de uma leva de anúncios verticais em português (o B-roll é a imagem de apoio que
entra por cima da fala). Você recebe a fala e a duração de cada anúncio, o TEMPLATE em que eles vão ser editados (ritmo,
regras e uma edição aprovada) e as categorias (pastas) que a oferta já tem, com quantos clipes bons cada uma tem.

0. Para cada anúncio, faça o que o editor faria no plano desse template: quantos B-rolls ele vai ter (pela duração e pelo
   ritmo do template) e, momento a momento, que IMAGEM LITERAL a fala pede ali, em que `formato` ela entraria. Em `por_anuncio`.
0b. AGORA CASE CLIPE A CLIPE, que é o mais importante desta etapa. Você recebe o CATÁLOGO da pasta: uma linha por clipe
   disponível, com os formatos em que ele funciona, o trecho limpo mais longo, a nota e o que ele ilustra. Para cada imagem:
   - Existe um clipe que mostra LITERALMENTE aquilo, funciona no formato pedido e tem trecho limpo com a duração mínima
     que a diretriz exige? Então escreva o id dele em `clipe` e marque pasta_ja_tem = true. Essa imagem NÃO vai para busca.
   - Não existe? Deixe `clipe` vazio e pasta_ja_tem = false. Só essas viram busca.
   Não force: clipe que "mais ou menos" serve é pior que buscar um certo. E o mesmo clipe não pode ser casado com dois
   anúncios da leva nem com duas imagens do mesmo anúncio — cada clipe entra uma vez só.
1. As imagens são o que dá para mostrar: "mulher agachando com barra pesada", "selfie no espelho mostrando o resultado",
   "ativação com elástico antes do treino". Nada abstrato.
2. Agrupe essas imagens em CATEGORIAS. Use uma categoria que a oferta já tem sempre que ela servir (escreva o nome
   exatamente igual, nova = false). Só crie categoria nova (nova = true) quando nenhuma serve; nome curto em português,
   no mesmo jeito das que existem ("Treino pesado na academia", "Resultado de glúteo"), e `ideia` com o que ela deve mostrar.
3. Crie as BUSCAS: cada busca = um tipo de imagem, numa categoria, com 2 ou 3 `termos` EM INGLÊS que descrevem a
   CENA filmada por criadores dos EUA ("woman heavy barbell squat gym", "glute bridge resistance band living room"). Os
   mesmos termos são usados no TikTok e no YouTube, então descreva a cena, não a rede. Evite
   termos que trazem gente explicando para a câmera ("how to", "tips", "mistakes", "why") e meme ("pov", "funny").
   `anuncios` = TODOS os anúncios em que essa imagem entra (uma busca serve a vários). `o_que` = o que precisa aparecer.
   Só busque as imagens que ficaram sem `clipe` no passo 0b. Cada busca traz ~15 a 25 clipes aproveitáveis: dimensione pelo número de
   momentos que precisam daquela imagem somando todos os anúncios. Respeite o limite de buscas."""

def linha_catalogo(x, categoria):
    """Uma linha por clipe, com o que decide se ele serve: formato, trecho limpo mais longo e o que mostra.
    Curto de propósito — a pasta tem centenas de clipes e a ficha inteira não caberia no pedido."""
    e = x.get("estudo") or {}
    maior = max((float(t.get("fim", 0)) - float(t.get("ini", 0)) for t in (e.get("trechos_limpos") or [])), default=0.0)
    fmts = ",".join(k for k, v in (e.get("formatos") or {}).items() if isinstance(v, dict) and v.get("serve")) or "nenhum"
    desc = (e.get("descricao") or x.get("descricao") or "")[:90]
    sp = e.get("serve_para") or []
    serve = ", ".join(sp[:2])[:60] if isinstance(sp, list) else str(sp)[:60]
    return (f"{x['id']} [{categoria}] {fmts} · limpo {maior:.1f}s · nota {x.get('nota') or '-'} · {desc}"
            + (f" — ilustra: {serve}" if serve else ""))

class ContextoBusca:
    """Estado de uma rodada de busca (TikTok e/ou YouTube): pasta da leva, quem cobrar, e o que já foi achado.
    Compartilhado entre tt_uma()/yt_uma() e usado tanto pela leva normal (a partir de anúncios) quanto pela
    busca agendada no YouTube (sem anúncio — ver main_youtube_agendada)."""
    def __init__(self, d, expert, oferta, nicho, leva_id, est, cobra_olho):
        self.d, self.expert, self.oferta, self.nicho, self.leva_id = d, expert, oferta, nicho, leva_id
        self.est, self.cobra_olho = est, cobra_olho
        self.achados = {}; self.lock = threading.Lock(); self.youtube_bloqueado = threading.Event()

    def falha(self, fonte, b, erro):
        if fonte == "YouTube" and isinstance(erro, getattr(youtube, "BloqueioYouTube", ())): self.youtube_bloqueado.set()
        motivo = str(erro)[:220]
        if fonte == "YouTube" and ("confirm" in motivo.lower() and "bot" in motivo.lower()):
            motivo = "YouTube bloqueou o download na VPS (verificação de robô)"
        self.est.log(f"{fonte} · '{b['nome']}': {motivo}")
        with self.est.lock:
            self.est.s.setdefault("falhas_busca", []).append(dict(fonte=fonte, busca=b["nome"], motivo=motivo))
            self.est.s["falhas_busca"] = self.est.s["falhas_busca"][-100:]
            self.est.salvar()

    def guardar(self, b, ids, novos):
        """Etiqueta os clipes com os anúncios (se houver — a busca agendada não tem nenhum) e a leva, e leva
        os novos para a pasta da categoria."""
        if not ids: return
        if b.get("anuncios"): biblioteca.marcar_anuncios(ids, b["anuncios"])
        with biblioteca.mexer() as Bx:
            for i in ids:
                it = Bx["itens"].get(i)
                if it: it["levas"] = sorted(set(it.get("levas", [])) | {self.leva_id})
        for i in novos: biblioteca.mover(i, self.expert, self.oferta, b["categoria"])
        with self.lock: self.achados[b["nome"]] = sorted(set(self.achados.get(b["nome"], [])) | set(ids))


def tt_uma(ctx, b):
    try:
        res = tiktok.buscar(ctx.d, b["nome"], b["termos"][:3], n=15, estudar=False, log=ctx.est.log, nicho=ctx.nicho,
                            campos=dict(expert=ctx.expert, oferta=ctx.oferta, categoria=b["categoria"], levas=[ctx.leva_id]))
        if not res["ids"]: raise RuntimeError("nenhum clipe foi baixado e salvo na biblioteca")
        ctx.guardar(b, res["ids"], res["novos"])
    except Exception as ex: ctx.falha("TikTok", b, ex)


def yt_uma(ctx, b):
    """Capas -> o Gemini escolhe os vídeos -> folha de quadros -> o Gemini acha os trechos -> baixa só eles."""
    if ctx.youtube_bloqueado.is_set(): return
    pasta = os.path.join(ctx.d, "youtube", tiktok.slug(b["nome"], 30)); cands, vistos = [], set()
    try:
        for termo in b["termos"][:2]:
            if ctx.youtube_bloqueado.is_set(): return
            for c in youtube.buscar(termo, YT_CANDS):
                if c["id"] in vistos: continue
                vistos.add(c["id"]); cands.append(dict(c, n=len(cands) + 1))
            if len(cands) >= YT_CANDS: break
        cands = cands[:YT_CANDS]
        if not cands: ctx.falha("YouTube", b, "nenhum vídeo encontrado"); return
        ctx.est.log(f"YouTube · {b['nome']}: {len(cands)} vídeo(s) na lista; escolhendo pelas capas…")
        folha = youtube.folha_capas(cands, os.path.join(pasta, "capas.jpg"))
        lista = "\n".join(f"{c['n']} · {c['dur'] // 60}:{c['dur'] % 60:02d} · {c['views']} views · {c['canal']} · {c['titulo'][:100]}" for c in cands)
        esc = gemini.escolher_videos(folha, lista, b["o_que"], YT_ABRIR, ao_cobrar=ctx.cobra_olho, log=ctx.est.log)
    except Exception as ex: ctx.falha("YouTube", b, ex); return
    por_n = {c["n"]: c for c in cands}; ids = []
    if not esc.get("escolhidos"): ctx.falha("YouTube", b, "nenhum vídeo adequado foi selecionado")
    for e in (esc.get("escolhidos") or [])[:YT_ABRIR]:
        if ctx.youtube_bloqueado.is_set(): break
        c = por_n.get(int(e.get("n") or 0))
        if not c: continue
        try:
            fq, ts, dur_q = youtube.quadros(c["id"], pasta)
            r = gemini.trechos_na_folha(fq, ts, dur_q, b["o_que"], YT_TRECHOS, ao_cobrar=ctx.cobra_olho, log=ctx.est.log)
            for t in (r.get("trechos") or [])[:YT_TRECHOS]:
                ini = max(0.0, float(t.get("ini") or 0)); dur = max(4.0, min(10.0, float(t.get("fim") or 0) - ini))
                if ini + dur > dur_q + 1: continue
                arq = youtube.baixar_para(c["id"], ini, dur, os.path.join(biblioteca.pasta("videos"), f"yt-{c['id']}-{int(ini)}.mp4"))
                i = biblioteca.adicionar(arquivo=arq, fonte="youtube", video_id=c["id"], yt_ini=ini, url=c["url"], autor=c["canal"],
                                         titulo=c["titulo"][:200], termo=", ".join(b["termos"][:2]), busca="youtube", nicho=ctx.nicho,
                                         views=c["views"], descricao=str(t.get("descricao") or "")[:300],
                                         expert=ctx.expert, oferta=ctx.oferta, categoria=b["categoria"], levas=[ctx.leva_id])
                ids.append(i); ctx.est.log(f"YouTube · {b['nome']}: {i} ({c['canal'][:22]}, {int(ini)}s) {str(t.get('descricao'))[:60]}")
        except Exception as ex: ctx.falha("YouTube", b, ex)
    for f in (os.listdir(pasta) if os.path.isdir(pasta) else []):
        if f.startswith("baixa_"): os.remove(os.path.join(pasta, f))
    ctx.guardar(b, ids, ids)


class Estado:
    def __init__(self, d, etapas=None):
        self.arq = os.path.join(d, "estado.json"); self.lock = threading.RLock()
        self.s = json.load(open(self.arq)) if os.path.exists(self.arq) else {}
        feitas = {e["id"]: e for e in self.s.get("etapas", [])}
        self.s["etapas"] = [feitas.get(i) or dict(id=i, nome=n, estado="pendente", detalhe="") for i, n in (etapas or ETAPAS)]
        for e in self.s["etapas"]:
            if e["estado"] != "ok": e["estado"] = "pendente"
        self.s.update(rodando=True, status="rodando", pid=os.getpid(), mensagem="", fim=None)
        self.s.setdefault("inicio", time.time()); self.s.setdefault("log", []); self.s.setdefault("resumo", {}); self.salvar()
    def et(self, i): return next(e for e in self.s["etapas"] if e["id"] == i)
    def marca(self, i, estado, detalhe=None):
        with self.lock:
            e = self.et(i); e["estado"] = estado
            if detalhe is not None: e["detalhe"] = detalhe
            self.salvar()
    def log(self, msg):
        with self.lock:
            linha = time.strftime("%H:%M:%S ") + str(msg); print(linha, flush=True)
            self.s["log"] = (self.s["log"] + [linha])[-400:]; self.salvar()
    def salvar(self):
        with self.lock:
            tmp = f"{self.arq}.{threading.get_ident()}.tmp"; json.dump(self.s, open(tmp, "w"), ensure_ascii=False, indent=1); os.replace(tmp, self.arq)

def roda(est, i, fn):
    if est.et(i)["estado"] == "ok": return
    est.marca(i, "rodando", ""); est.log(f"— {dict(ETAPAS)[i]}")
    try: det = fn()
    except Exception as e: est.marca(i, "erro", str(e)[-400:]); raise
    est.marca(i, "ok", det or "")

def resumo_resultado(T, achados, B, nomes):
    """Conta o que a edição pode usar, incluindo os clipes casados do catálogo."""
    planos = {a["anuncio"]: a for a in T.get("por_anuncio", [])}
    casados = {n: {im.get("clipe") for im in planos.get(n, {}).get("imagens", []) if im.get("clipe")} for n in nomes}
    encontrados = {i for lista in achados.values() for i in lista}
    reutilizados = set().union(*casados.values()) if casados else set()
    ids = encontrados | reutilizados
    def existe(i):
        arq = (B.get(i) or {}).get("arquivo")
        try: return bool(arq and os.path.isfile(arq) and os.path.getsize(arq))
        except OSError: return False
    presentes = {i for i in ids if existe(i)}
    bons = {i for i in presentes if B[i].get("estado") == "disponivel" and B[i].get("estudo") and B[i].get("serve") is not False}
    por_anuncio = {n: len(bons & (casados[n] | {i for i in encontrados if n in (B.get(i) or {}).get("anuncios", [])})) for n in nomes}
    # Um resultado parcial pode seguir. Um anúncio que precisa de B-roll e ficou
    # sem nenhum clipe utilizável precisa ser resolvido antes de indicar sucesso.
    sem_broll = [n for n in nomes if not por_anuncio[n] and
                 (n not in planos or planos[n].get("broll_estimados", 0) > 0 or planos[n].get("imagens"))]
    por_cat = {}
    for i in bons:
        categoria = B[i].get("categoria") or "sem categoria"
        por_cat[categoria] = por_cat.get(categoria, 0) + 1
    return dict(por_anuncio=por_anuncio, por_categoria=por_cat, bons=len(bons), total=len(ids),
                novos=len(bons & (encontrados - reutilizados)), reaproveitados=len(bons & reutilizados),
                descartados=sum(1 for i in ids if (B.get(i) or {}).get("estado") == "descartado"),
                sem_ficha=sum(1 for i in presentes if B[i].get("estado") == "disponivel" and not B[i].get("estudo")),
                sem_arquivo=len(ids - presentes), sem_broll=sem_broll,
                novas=[c["nome"] for c in T.get("categorias", []) if c.get("nova")],
                plano={n: dict(broll=a.get("broll_estimados", 0), imagens=len(a.get("imagens", [])),
                               pasta_tem=len(casados.get(n, []))) for n, a in planos.items()})

def validar_resultado(resumo, falhas=()):
    if not resumo["sem_broll"]: return
    causas, fontes = [], set()
    for falha in falhas:
        causa = f"{falha['fonte']}: {falha['motivo']}"
        if falha["fonte"] not in fontes: causas.append(causa); fontes.add(falha["fonte"])
    detalhe = (f"{resumo['descartados']} descartado(s), {resumo['sem_ficha']} sem estudo, "
               f"{resumo['sem_arquivo']} sem arquivo.") if resumo["total"] else "Nenhum clipe foi salvo."
    raise RuntimeError("Nenhum B-roll utilizável para " + ", ".join(resumo["sem_broll"]) + ". " + detalhe +
                       (" " + " · ".join(causas[:2]) if causas else " Confira o plano e tente a busca novamente."))

INSTR_AGENDADA = """Você abastece a biblioteca de B-roll de uma oferta com buscas novas no YouTube, sem ter
nenhum anúncio específico em mãos agora — é manutenção da pasta (rodando sozinho, agendado), não a preparação
de uma leva. Você recebe as categorias que a oferta já tem hoje e quantos clipes bons cada uma tem, e os
termos já buscados nesta sessão (não repita nenhum).

1. Priorize as categorias com menos clipes bons — é aí que a pasta está mais fraca.
2. Pode propor uma categoria nova (nova=true) se o nicho claramente pedir uma imagem que nenhuma categoria
   cobre — nome curto em português, no mesmo jeito das que já existem, com `ideia` do que ela deve mostrar.
3. Cada busca = um tipo de imagem, numa categoria, com 2 ou 3 termos EM INGLÊS que descrevem a CENA filmada
   por criadores dos EUA (ex.: "woman heavy barbell squat gym", "glute bridge resistance band living room").
   Evite termos que trazem gente explicando pra câmera ("how to", "tips", "why") ou meme ("pov", "funny").
   `o_que` = o que precisa aparecer na cena.
4. NUNCA repita um termo da lista de já buscados. Se uma categoria já foi bem coberta nesta sessão, mude de
   ângulo ou passe pra próxima categoria mais fraca."""

def termos_agendada(expert, oferta, ja_buscados, log=None, ao_cobrar=None):
    """Sem anúncio: olha as categorias que a oferta já tem (e quantos clipes bons cada uma tem) e propõe até
    MAX_TOTAL_AGENDADA buscas pra abastecer o que está mais fraco, sem repetir termo já usado nesta sessão
    (`ja_buscados`). Devolve [] quando não sobra nada óbvio pra buscar."""
    todos = [x for x in biblioteca.ler()["itens"].values() if x.get("expert") == expert and x.get("oferta") == oferta]
    cats = []
    for c in biblioteca._pastas(os.path.join(biblioteca.BROLLS, expert, oferta)):
        dela = [x for x in todos if x.get("categoria") == c and x["estado"] == "disponivel"]
        bons = sum(1 for x in dela if x.get("estudo"))
        cats.append(f"- {c}: {bons} com ficha")
    sch = obj(buscas=arr(obj(nome=S(), categoria=S(), nova=B(), ideia=S(), termos=arr(S()), o_que=S())))
    cl = ia.Claude(log=log, ao_cobrar=ao_cobrar)
    r = cl.conversa(INSTR_AGENDADA, esforco="medium").pedir(
        [texto(f"# Expert: {expert} · Oferta: {oferta}\n\n# Categorias hoje\n" + ("\n".join(cats) or "(nenhuma ainda — primeira busca desta oferta)")),
         texto("# Termos já buscados nesta sessão (não repita)\n" + ("\n".join(ja_buscados) or "(nada ainda)")),
         texto(f"Proponha até {MAX_TOTAL_AGENDADA} buscas novas pra abastecer o que está mais fraco.")],
        sch, max_tokens=16000, rotulo="Plano de busca agendada no YouTube")
    return (r.get("buscas") or [])[:MAX_TOTAL_AGENDADA]


def main_youtube_agendada(d, ped):
    """Busca agendada, sem anúncio: repete rodadas de (Claude planeja termos -> busca no YouTube) até a
    duração agendada acabar (ped['agendado_em'] + ped['duracao_horas']) ou o YouTube bloquear o acesso —
    o que vier primeiro. Cada rodada varia os termos (nunca repete um já buscado nesta sessão)."""
    est = Estado(d, etapas=ETAPAS_AGENDADA)
    def parar(*_): est.log("cancelado"); est.s.update(rodando=False, status="cancelado", fim=time.time()); est.salvar(); os._exit(1)
    signal.signal(signal.SIGTERM, parar)
    expert, oferta = ped["expert"], ped["oferta"]; nicho = biblioteca.nome_oferta(oferta); leva_id = os.path.basename(d)
    ate = float(ped["agendado_em"]) + float(ped.get("duracao_horas", 1)) * 3600
    def cobra_claude(info): custos.registrar(d, servico="claude", etapa="termos", fonte="tokens informados pela API × preço de tabela", **info)
    def cobra_olho(info):
        custos.registrar(d, servico="gemini", etapa="busca", fonte="tokens informados pela API × preço de tabela (escolha dos vídeos e dos trechos no YouTube)", **info)
    def cobra_estudo(info):
        custos.registrar(d, servico="gemini" if biblioteca.motor_estudo() == "gemini" else "claude", etapa="estudo",
                         fonte="tokens informados pela API × preço de tabela (estudo do clipe)", **info)
    try:
        if not youtube.disponivel(): raise RuntimeError("o YouTube precisa do yt-dlp e do ffmpeg instalados")
        ctx = ContextoBusca(d, expert, oferta, nicho, leva_id, est, cobra_olho)
        est.s["falhas_busca"] = []; est.salvar()
        ja_buscados, rodadas = [], 0
        while time.time() < ate and not ctx.youtube_bloqueado.is_set():
            rodadas += 1
            est.marca("busca", "rodando", f"rodada {rodadas} · planejando…")
            try:
                buscas = termos_agendada(expert, oferta, ja_buscados, log=est.log, ao_cobrar=cobra_claude)
            except Exception as e:
                est.log(f"plano da rodada {rodadas} falhou: {e}"); break
            if not buscas:
                est.log("nada novo pra buscar (categorias já cobertas) — parando"); break
            ja_buscados.extend(t for b in buscas for t in (b.get("termos") or []))
            est.marca("busca", "rodando", f"rodada {rodadas} · buscando {len(buscas)} termo(s)…")
            with ThreadPoolExecutor(2) as ex: list(ex.map(lambda b: yt_uma(ctx, b), buscas))
            json.dump(ctx.achados, open(os.path.join(d, "achados.json"), "w"), ensure_ascii=False, indent=1)
        # Por que parou, pra mostrar bem claro na tela (é o que o usuário mais precisa saber aqui).
        if ctx.youtube_bloqueado.is_set():
            motivo_parada = "YouTube bloqueou o acesso (verificação de robô) — parou antes da duração agendada acabar"
        elif time.time() >= ate:
            motivo_parada = f"completou a duração agendada ({ped.get('duracao_horas')}h)"
        else:
            motivo_parada = "nada novo pra buscar: as categorias já estavam cobertas"
        est.marca("busca", "ok", f"{rodadas} rodada(s) · {motivo_parada}")
        ids = list(dict.fromkeys(i for v in ctx.achados.values() for i in v))

        def estudo():
            pend = [i for i in ids if not (biblioteca.item(i) or {}).get("estudo")]
            if not pend: return "todos já tinham ficha"
            est.log(f"estudando {len(pend)} clipe(s) ({biblioteca.motor_estudo()}), uma vez só…")
            n = [0]
            def prog(f, t):
                if f - n[0] >= 5 or f == t: n[0] = f; est.marca("estudo", "rodando", f"{f}/{t} clipes")
            feitos = biblioteca.estudar_pendentes(pend, ao_cobrar=cobra_estudo, log=est.log, progresso=prog)
            return f"{len(feitos)}/{len(pend)} clipe(s) estudados"
        roda(est, "estudo", estudo)

        def organizacao():
            B = biblioteca.ler()["itens"]
            for i in ids:
                x = B.get(i)
                if x and x.get("levas") and leva_id in x["levas"] and x.get("categoria"): biblioteca.mover(i, expert, oferta, x["categoria"])
            B = biblioteca.ler()["itens"]
            por_cat = {}
            for i in ids:
                x = B.get(i)
                if x and x.get("estado") == "disponivel" and x.get("estudo") and x.get("serve") is not False:
                    cat = x.get("categoria") or "sem categoria"; por_cat[cat] = por_cat.get(cat, 0) + 1
            resumo = dict(bons=sum(por_cat.values()), total=len(ids), rodadas=rodadas, por_categoria=por_cat, motivo_parada=motivo_parada)
            est.s["resumo"] = resumo; est.salvar()
            return f"{resumo['bons']} clipe(s) bons em {len(por_cat)} categoria(s)"
        roda(est, "organizacao", organizacao)

        c = custos.resumo(d)
        est.s.update(rodando=False, status="pronto", fim=time.time(),
                     mensagem=f"{motivo_parada} · Claude US$ {c['claude']:.2f} · estudo US$ {c['gemini']:.2f}")
        est.salvar(); est.log("busca agendada concluída")
    except Exception as e:
        est.log("ERRO: " + str(e)); open(os.path.join(d, "erro.txt"), "w").write(traceback.format_exc())
        est.s.update(rodando=False, status="erro", mensagem=str(e)[:800], fim=time.time()); est.salvar(); sys.exit(1)


def listar_agendadas_pendentes():
    """Pedidos de busca agendada no YouTube cuja hora já chegou e que nunca foram lançados (sem estado.json
    ainda) — é o que o laço (laco_agendadas) usa pra saber o que lançar agora."""
    agora = time.time(); out = []
    for pj in glob.glob(os.path.join(biblioteca.BROLLS, "*", "*", ".levas", "*", "pedido.json")):
        d = os.path.dirname(pj)
        try: ped = json.load(open(pj))
        except (OSError, ValueError): continue
        if ped.get("modo") != "youtube_agendada": continue
        if os.path.exists(os.path.join(d, "estado.json")): continue
        if float(ped.get("agendado_em") or 0) > agora: continue
        out.append(d)
    return out


def lancar(dl):
    """Mesmo Popen de sempre (ver app/servidor.py:lancar_leva) — só que este arquivo já tem tudo que precisa
    (PY, LIB) pra se auto-lançar, sem depender do servidor: quem chama é o laço agendado, numa thread que
    não pode travar esperando o subprocesso terminar (pode rodar por horas)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))}
    subprocess.Popen([PY, os.path.join(LIB, "leva.py"), dl], stdout=open(os.path.join(dl, "saida.log"), "a"),
                     stderr=subprocess.STDOUT, start_new_session=True, env=env, cwd=dl)


def laco_agendadas(parar=None, espera=60.0, log=print):
    """Laço da fila de buscas agendadas: uma vez por minuto (espera), passa por TODOS os workspaces (cada um
    tem sua própria pasta de B-rolls) e lança quem já chegou a hora. O servidor chama isto numa thread, uma
    vez só pra vida inteira do processo — mesmo padrão de kanban.laco()."""
    import workspaces
    while not (parar and parar.is_set()):
        try:
            for w in workspaces.listar():
                tok = comum.definir_workspace(None if w["id"] == workspaces.PADRAO_ID else w)
                try:
                    for dl in listar_agendadas_pendentes():
                        log(f"busca agendada: lançando {dl}"); lancar(dl)
                except Exception as e:
                    log(f"buscas agendadas: tropeçou num workspace — {e}")
                finally:
                    comum.resetar_workspace(tok)
        except Exception as e:
            log(f"buscas agendadas: laço tropeçou — {e}")
        (parar.wait(espera) if parar else time.sleep(espera))


def main():
    d = os.path.abspath(sys.argv[1]); ped = json.load(open(os.path.join(d, "pedido.json")))
    if ped.get("modo") == "youtube_agendada": return main_youtube_agendada(d, ped)
    est = Estado(d)
    def parar(*_): est.log("cancelado"); est.s.update(rodando=False, status="cancelado", fim=time.time()); est.salvar(); os._exit(1)
    signal.signal(signal.SIGTERM, parar)
    expert, oferta = ped["expert"], ped["oferta"]; nicho = biblioteca.nome_oferta(oferta); leva = os.path.basename(d)
    anuncios = ped["anuncios"]; nomes = [a["nome"] for a in anuncios]
    fontes = [f for f in (ped.get("fontes") or ["tiktok"]) if f in ("tiktok", "youtube")] or ["tiktok"]
    def cobra_claude(info): custos.registrar(d, servico="claude", etapa="termos", fonte="tokens informados pela API × preço de tabela", **info)
    def cobra_olho(info):
        custos.registrar(d, servico="gemini", etapa="busca", fonte="tokens informados pela API × preço de tabela (escolha dos vídeos e dos trechos no YouTube)", **info)
    def cobra_estudo(info):
        custos.registrar(d, servico="gemini" if biblioteca.motor_estudo() == "gemini" else "claude", etapa="estudo",
                         fonte="tokens informados pela API × preço de tabela (estudo do clipe)", **info)
    try:
        # 1. transcrição (no Mac, um anúncio de cada vez)
        def transcricao():
            os.makedirs(os.path.join(d, "transcricoes"), exist_ok=True)
            for k, a in enumerate(anuncios, 1):
                saida = os.path.join(d, "transcricoes", f"{k:02d}.json")
                if os.path.exists(saida): continue
                est.log(f"transcrevendo {a['nome']}…")
                r = subprocess.run([PY, os.path.join(LIB, "transcrever.py"), a["arquivo"], saida], capture_output=True, text=True)
                if r.returncode or not os.path.exists(saida): raise RuntimeError(f"a transcrição de {a['nome']} falhou: {r.stderr[-300:]}")
            return f"{len(anuncios)} anúncio(s) transcritos"
        roda(est, "transcricao", transcricao)
        falas = {a["nome"]: json.load(open(os.path.join(d, "transcricoes", f"{k:02d}.json")))["texto"] for k, a in enumerate(anuncios, 1)}

        # 2. termos e categorias (o Claude lê todas as falas juntas)
        def termos():
            todos = [x for x in biblioteca.ler()["itens"].values() if x.get("expert") == expert and x.get("oferta") == oferta]
            cats, catalogo, ids_ok = [], [], set()
            for c in biblioteca._pastas(os.path.join(biblioteca.BROLLS, expert, oferta)):
                dela = [x for x in todos if x.get("categoria") == c and x["estado"] == "disponivel"]
                bons = [x for x in dela if x.get("estudo")]
                sem = len(dela) - len(bons)
                cats.append(f"- {c}: {len(bons)} com ficha" + (f" + {sem} ainda sem ficha (não dá para casar)" if sem else ""))
                for x in sorted(bons, key=lambda x: -(x.get("nota") or 0)):
                    catalogo.append(linha_catalogo(x, c)); ids_ok.add(x["id"])
            tpl = ped.get("template") or "ultradinamico-criativo"; E = comum.estilo(tpl); pasta_tpl = os.path.join(SKILL, "estilos", tpl)
            # BUSCA.md é a diretriz escrita PARA esta etapa (quantos clipes, que formato, onde buscar cada coisa).
            # O LEIA.md é o guia da edição e só entra se o estilo ainda não tiver o BUSCA.md.
            def ler_tpl(nome):
                a = os.path.join(pasta_tpl, nome)
                return open(a, encoding="utf-8").read() if os.path.exists(a) else ""
            leia = ler_tpl("BUSCA.md") or ler_tpl("LEIA.md")
            exemplo = ler_tpl("exemplo.md")
            def duracao(k):
                try: segs = json.load(open(os.path.join(d, "transcricoes", f"{k:02d}.json")))["whisper"]["segments"]; return segs[-1]["end"] if segs else 0
                except (OSError, ValueError, KeyError, IndexError): return 0
            durs = {a["nome"]: duracao(k) for k, a in enumerate(anuncios, 1)}
            limite = MAX_TOTAL
            fmts = (E.get("usa") or {}).get("formatos") or ["cheia", "canto", "dividida", "card"]
            sch = obj(por_anuncio=arr(obj(anuncio=enum(nomes), broll_estimados=I(),
                      imagens=arr(obj(o_que=S(), categoria=S(), formato=enum(fmts), clipe=S(), pasta_ja_tem=B())))),
                      categorias=arr(obj(nome=S(), nova=B(), ideia=S())),
                      buscas=arr(obj(nome=S(), categoria=S(), termos=arr(S()), anuncios=arr(enum(nomes)), o_que=S())), observacoes=arr(S()))
            cl = ia.Claude(log=est.log, ao_cobrar=cobra_claude)
            r = cl.conversa(INSTR, esforco="medium").pedir(
                [texto(f"# Template: {E.get('nome', tpl)}\n\n{leia}\n\n## Regras conferidas na edição\n" + ("\n".join("- " + l for l in regras.texto(E.get("regras"))) or "(sem regras extras)")
                       + ("\n\n## Edição aprovada neste template (o ritmo de referência)\n" + exemplo if exemplo else "")),
                 texto(f"# Expert: {expert} · Oferta: {oferta}\n\n# Categorias da pasta\n" + ("\n".join(cats) or "(nenhuma ainda)")
                       + "\n\n# Catálogo: os clipes que a pasta tem disponíveis agora\n"
                       + ("\n".join(catalogo) if catalogo else "(a pasta está vazia: tudo vai para busca)")),
                 texto("# Anúncios da leva\n\n" + "\n\n".join(f"## {n} ({int(durs[n]) // 60}:{int(durs[n]) % 60:02d})\n{f}" for n, f in falas.items())),
                 texto(f"Faça o plano de B-roll de cada anúncio neste template, confira o que a pasta já tem, e defina as categorias e as buscas "
                       f"só para as imagens que nenhum clipe do catálogo cobre (no máximo {limite}).")],
                sch, max_tokens=48000, rotulo="Plano de B-roll e termos de busca")
            # O casamento vem do Claude, mas quem garante que ele é válido é o código: id que existe, clipe que
            # serve mesmo, e UMA vez só na leva inteira. B-roll repetido entre anúncios queima o criativo.
            tomados, perdidos = {}, 0
            for a in r["por_anuncio"]:
                for im in a["imagens"]:
                    cid = (im.get("clipe") or "").strip().upper()
                    if cid and cid in ids_ok and cid not in tomados:
                        tomados[cid] = a["anuncio"]; im["clipe"] = cid; im["pasta_ja_tem"] = True
                    else:
                        if cid: perdidos += 1                      # inventado, já tomado por outro anúncio, ou sem ficha
                        im["clipe"] = ""; im["pasta_ja_tem"] = False
            if perdidos: est.log(f"  {perdidos} casamento(s) recusado(s) (clipe repetido ou inexistente): viram busca")
            for cid, anuncio in tomados.items(): biblioteca.marcar_anuncios([cid], [anuncio])
            est.log(f"  a pasta cobriu {len(tomados)} imagem(ns) sem precisar buscar")
            nomes_cat = {c["nome"] for c in r["categorias"]}
            faltam = {a["anuncio"] for a in r["por_anuncio"] for i in a["imagens"] if not i["clipe"]}
            buscas = [b for b in r["buscas"] if b["termos"] and [x for x in b["anuncios"] if x in faltam]][:limite]
            for b in buscas: b["anuncios"] = [x for x in b["anuncios"] if x in faltam]
            for b in buscas:
                if b["categoria"] not in nomes_cat: r["categorias"].append(dict(nome=b["categoria"], nova=True, ideia=b["o_que"])); nomes_cat.add(b["categoria"])
            json.dump(dict(template=tpl, por_anuncio=r["por_anuncio"], categorias=r["categorias"], buscas=buscas, observacoes=r["observacoes"]),
                      open(os.path.join(d, "termos.json"), "w"), ensure_ascii=False, indent=1)
            for a in r["por_anuncio"]:
                tem = sum(1 for i in a["imagens"] if i["pasta_ja_tem"])
                est.log(f"  {a['anuncio']}: ~{a['broll_estimados']} B-roll(s) no {E.get('nome', tpl)} · {len(a['imagens'])} imagem(ns), {tem} a pasta já tem")
            for b in buscas: est.log(f"  · busca {b['categoria']} › {b['nome']} ({', '.join(b['termos'])}) → {', '.join(b['anuncios'])}")
            est.s["plano"] = {a["anuncio"]: dict(broll=a["broll_estimados"], imagens=len(a["imagens"]), pasta_tem=sum(1 for i in a["imagens"] if i["pasta_ja_tem"]))
                              for a in r["por_anuncio"]}; est.salvar()
            novas = [c["nome"] for c in r["categorias"] if c.get("nova")]
            return (f"{len(buscas)} busca(s) para o que falta · {len(r['categorias'])} categoria(s)" + (f" ({len(novas)} nova(s): {', '.join(novas)})" if novas else "")
                    + (" · a pasta já cobre tudo" if not buscas else ""))
        roda(est, "termos", termos)
        T = json.load(open(os.path.join(d, "termos.json")))

        # 3. busca (TikTok pela Apify e/ou YouTube pelo yt-dlp; tudo já entra na categoria e com a etiqueta dos anúncios)
        ctx = ContextoBusca(d, expert, oferta, nicho, leva, est, cobra_olho)
        def busca():
            est.s["falhas_busca"] = []; est.salvar()
            if not T["buscas"]: json.dump({}, open(os.path.join(d, "achados.json"), "w")); return "nada a buscar: a pasta já cobre as imagens"
            if "tiktok" in fontes:
                if not custos.token_apify(): raise RuntimeError("falta o token da Apify (⚙)")
                with ThreadPoolExecutor(3) as ex: list(ex.map(lambda b: tt_uma(ctx, b), T["buscas"]))
            if "youtube" in fontes:
                if not youtube.disponivel(): raise RuntimeError("o YouTube precisa do yt-dlp e do ffmpeg instalados")
                with ThreadPoolExecutor(2) as ex: list(ex.map(lambda b: yt_uma(ctx, b), T["buscas"]))
            json.dump(ctx.achados, open(os.path.join(d, "achados.json"), "w"), ensure_ascii=False, indent=1)
            onde = " e ".join("TikTok" if f == "tiktok" else "YouTube" for f in fontes)
            return f"{len({i for lista in ctx.achados.values() for i in lista})} clipe(s) salvo(s) em {len(T['buscas'])} busca(s) no {onde}"
        roda(est, "busca", busca)
        achados = json.load(open(os.path.join(d, "achados.json")))
        ids = list(dict.fromkeys(i for v in achados.values() for i in v))

        # 4. estudo (uma vez por clipe)
        def estudo():
            pend = [i for i in ids if not (biblioteca.item(i) or {}).get("estudo")]
            if not pend: return "todos já tinham ficha"
            est.log(f"estudando {len(pend)} clipe(s) ({biblioteca.motor_estudo()}), uma vez só…")
            n = [0]
            def prog(f, t):
                if f - n[0] >= 5 or f == t: n[0] = f; est.marca("estudo", "rodando", f"{f}/{t} clipes")
            feitos = biblioteca.estudar_pendentes(pend, ao_cobrar=cobra_estudo, log=est.log, progresso=prog)
            return f"{len(feitos)}/{len(pend)} clipe(s) estudados"
        roda(est, "estudo", estudo)

        # 5. pastas e resumo (descartados vão para .descartados)
        def organizacao():
            B = biblioteca.ler()["itens"]
            for i in ids:
                x = B.get(i)
                if x and x.get("levas") and leva in x["levas"] and x.get("categoria"): biblioteca.mover(i, expert, oferta, x["categoria"])
            resumo = resumo_resultado(T, achados, biblioteca.ler()["itens"], nomes)
            est.s["resumo"] = resumo
            est.salvar()
            validar_resultado(resumo, est.s.get("falhas_busca", []))
            return f"{resumo['bons']} clipe(s) bons · {resumo['descartados']} descartado(s) · " + " · ".join(f"{n}: {q}" for n, q in resumo["por_anuncio"].items())
        roda(est, "organizacao", organizacao)
        # Confere também ao retomar estados antigos que registraram sucesso vazio.
        est.s["resumo"] = resumo_resultado(T, achados, biblioteca.ler()["itens"], nomes); est.salvar()
        validar_resultado(est.s["resumo"], est.s.get("falhas_busca", []))
        c = custos.resumo(d)
        est.s.update(rodando=False, status="pronto", fim=time.time(),
                     mensagem=f"pronto · Claude US$ {c['claude']:.2f} · Apify US$ {c['apify']:.2f} · estudo US$ {c['gemini']:.2f}")
        est.salvar(); est.log("busca da leva concluída")
    except Exception as e:
        est.log("ERRO: " + str(e)); open(os.path.join(d, "erro.txt"), "w").write(traceback.format_exc())
        est.s.update(rodando=False, status="erro", mensagem=str(e)[:800], fim=time.time()); est.salvar(); sys.exit(1)

if __name__ == "__main__":
    main()
