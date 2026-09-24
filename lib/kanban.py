"""Kanban do Estúdio: um quadro com os anúncios andando pelas fases, um trabalho pesado de cada vez.

Colunas: broll → edicao (revisão sua) → render → pronto.
Os vídeos do Kanban chegam já cortados; a limpeza automática está suspensa.
Uma demanda nova já entra direto em 'broll' (roda sozinha) e, quando a busca termina bem, segue sozinha para
'edicao' (roda sozinha de novo). A única parada manual da esteira é você revisar no editor e arrastar para
'render' — e depois de pronto, arrastar para 'pronto'. Quando um card entra numa coluna que roda ele entra na
FILA e roda sozinho; ao terminar fica ali marcado. O que roda em cada coluna:

  broll    por LOTE: uma leva (lib/leva.py) com todos os anúncios daquele lote — transcreve todos juntos, o Claude
           lê as falas e define as buscas, e os clipes entram na pasta da oferta com a etiqueta de cada anúncio.
  edicao   por card: preserva o vídeo enviado inteiro, prepara máscaras, B-roll e letreiros.
           Reaproveita a busca anterior e para para revisão no editor.
  render   por card: `auto.py --ate render`.

Regras de desempenho (o editor tem que continuar liso): até LIMITE_CONCORRENCIA trabalhos pesados ao mesmo tempo
por workspace (padrão 2; ESTUDIO_KANBAN_CONCORRENCIA muda isso), cada um em prioridade baixa (`nice`), e a fila
pode ser pausada. Render nunca roda mais de um por vez — sozinho já satura a máquina, rodar dois ao mesmo tempo só
deixaria os dois mais lentos. O estado mora em ~/Edições/.kanban/quadro.json.

  python3 kanban.py estado            imprime o quadro
  python3 kanban.py passo             executa o próximo da fila, esperando terminar (útil para testar)"""
import os, re, sys, json, time, shutil, fcntl, subprocess, threading, contextvars, contextlib, datetime, unicodedata
LIB = os.path.dirname(os.path.abspath(__file__)); SKILL = os.path.dirname(LIB); sys.path.insert(0, LIB)
import comum, biblioteca

def raiz():
    """Dinâmico por workspace (comum.RAIZ já é por requisição); um teste que faça patch.object(kanban, "RAIZ", ...)
    tem prioridade máxima — globals() enxerga o patch direto no __dict__ do módulo."""
    patch = globals().get("RAIZ")
    return patch if patch is not None else os.path.join(comum.RAIZ, ".kanban")

def _arq_kanban():
    patch = globals().get("ARQ")
    return patch if patch is not None else os.path.join(raiz(), "quadro.json")
VENV = sys.executable
PY = sys.executable
COLUNAS = [("broll", "B-roll"), ("edicao", "Edição (revisar)"), ("render", "Render"), ("pronto", "Pronto")]
RODA = {"broll", "edicao", "render"}            # colunas que executam algo
PRECISA_REVISAO = {"edicao"}                    # colunas que pedem o seu ok antes de seguir
LIMITE_CONCORRENCIA = max(1, int(os.environ.get("ESTUDIO_KANBAN_CONCORRENCIA") or 2))  # trabalhos rodando ao mesmo tempo, por workspace

def __getattr__(nome):
    if nome == "RAIZ": return raiz()
    if nome == "ARQ": return _arq_kanban()
    raise AttributeError(f"module {__name__!r} has no attribute {nome!r}")

def agora(): return time.strftime("%Y-%m-%dT%H:%M:%S")

@contextlib.contextmanager
def mexer():
    """Abre o quadro com trava, devolve o dict e grava no fim."""
    os.makedirs(raiz(), exist_ok=True)
    with open(os.path.join(raiz(), ".trava"), "w") as t:
        fcntl.flock(t, fcntl.LOCK_EX)
        Q = ler()
        yield Q
        tmp = _arq_kanban() + f".{os.getpid()}.tmp"; json.dump(Q, open(tmp, "w"), ensure_ascii=False, indent=1); os.replace(tmp, _arq_kanban())

def ler():
    try: Q = json.load(open(_arq_kanban()))
    except (OSError, ValueError): Q = {}
    Q.setdefault("lotes", {}); Q.setdefault("cards", {}); Q.setdefault("proximo", 1); Q.setdefault("pausada", False)
    return Q

def novo_id(Q, letra):
    i = Q["proximo"]; Q["proximo"] = i + 1
    return f"{letra}{i:03d}"

def identidade_reenvio(nome, expert, oferta):
    return tuple(" ".join(unicodedata.normalize("NFC", str(x)).split()).casefold()
                 for x in (nome, expert, oferta))

def reenvio_pendente(Q, nome, expert, oferta):
    identidade = identidade_reenvio(nome, expert, oferta)
    regras = [r for r in Q.get("reenvios", {}).values() if r.get("estado") == "pendente"
              and identidade_reenvio(r.get("nome"), r.get("expert"), r.get("oferta")) == identidade]
    if len(regras) > 1: raise ValueError("há mais de um reenvio reservado para este anúncio")
    return regras[0] if regras else None

def vincular_reenvio(Q, card, lote):
    """Consome só a exceção reservada; nunca reaproveita vídeo ou timestamps antigos."""
    r = reenvio_pendente(Q, card["nome"], lote["expert"], lote["oferta"])
    if not r or card["id"] == r.get("card_origem"): return False
    if card.get("projeto") or card.get("estado") not in ("parado", "espera"): return False
    if os.path.realpath(card["video"]) == os.path.realpath(r.get("video_origem") or ""):
        raise ValueError("o reenvio precisa usar o novo vídeo já cortado")
    leva = os.path.realpath(r["leva"])
    base = os.path.realpath(os.path.join(biblioteca.BROLLS, lote["expert"], lote["oferta"], ".levas"))
    if not leva.startswith(base + os.sep) or not os.path.isdir(leva):
        raise ValueError("a busca anterior deste anúncio não está disponível; os arquivos foram preservados")
    card.update(coluna="edicao", estado="espera", revisado=False, leva=leva, reenvio=r["id"],
                sem_novas_buscas=True, video_ja_cortado=True, transcricao=None, video_leva=None,
                msg="Vídeo já cortado; reaproveitando os B-rolls existentes", atualizado=agora())
    r.update(estado="consumido", card_destino=card["id"], consumido=agora())
    return True

def migrar_fluxo_sem_cortes():
    """Tira cards antigos da fase suspensa sem iniciar a edição do bruto, e tira cards presos na extinta coluna
    'novas' (toda demanda nova já entra direto em 'broll'; só ficam presos aqui cards criados antes desta versão)."""
    with mexer() as Q:
        mudou = []
        for c in Q["cards"].values():
            if c["coluna"] != "cortes": continue
            if c["estado"] == "rodando":
                raise ValueError("espere o corte em execução terminar antes de mudar o fluxo")
            c.update(coluna="edicao", estado="parado", revisado=False, atualizado=agora(),
                     msg="Fluxo de cortes suspenso. Aguardando o reenvio do vídeo já cortado.")
            mudou.append(c["id"])
        # Um envio que terminou durante a publicação também recebe a exceção.
        for c in Q["cards"].values():
            if c.get("coluna") == "novas" and c.get("estado") == "parado" and not c.get("projeto"):
                vincular_reenvio(Q, c, Q["lotes"][c["lote"]])
        # Quem sobrou em 'novas' (sem reenvio) segue para 'broll', como toda demanda nova já faz.
        for c in Q["cards"].values():
            if c.get("coluna") != "novas": continue
            if c["estado"] == "rodando": raise ValueError("espere a demanda em execução terminar antes de mudar o fluxo")
            c.update(coluna="broll", estado="espera", revisado=False, atualizado=agora(), msg="")
            mudou.append(c["id"])
        return mudou

def recuperar_travados():
    """Ao subir o servidor: nada tira sozinho um card 'rodando' dali — se o processo caiu no meio de um trabalho
    (mais fácil agora, com vários rodando ao mesmo tempo por workspace), esse card fica preso pra sempre: mover()
    pula quem está 'rodando', e render trava a coluna inteira (só roda um por vez). Manda pra 'erro' com um
    aviso — você decide se tenta de novo. Passa por TODOS os workspaces, cada um com sua própria fila."""
    import workspaces
    recuperados = []
    for w in workspaces.listar():
        tok = comum.definir_workspace(None if w["id"] == workspaces.PADRAO_ID else w)
        try:
            with mexer() as Q:
                for c in Q["cards"].values():
                    if c["estado"] != "rodando": continue
                    c.update(estado="erro", msg="Interrompido por um reinício do servidor; tente de novo.", atualizado=agora())
                    recuperados.append(c["id"])
        finally:
            comum.resetar_workspace(tok)
    return recuperados

# ---------------------------------------------------------------- criar
def criar_lote(nome, expert, oferta, estilo, anuncios, fontes=None):
    """anuncios: [(nome do anúncio, caminho do vídeo já salvo)]. Cria o lote e um card por anúncio, já na coluna
    'broll' — a busca começa sozinha, sem precisar arrastar (a não ser que seja um reenvio, que já pula pra edição
    reaproveitando a busca anterior)."""
    with mexer() as Q:
        lid = novo_id(Q, "L")
        Q["lotes"][lid] = dict(id=lid, nome=nome, expert=expert, oferta=oferta, estilo=estilo,
                               fontes=fontes or ["tiktok", "youtube"], criado=agora())
        for n, arq in anuncios:
            cid = novo_id(Q, "C")
            Q["cards"][cid] = dict(id=cid, lote=lid, nome=n, video=arq, projeto=None, coluna="broll", estado="espera",
                                   revisado=False, msg="", leva=None, video_ja_cortado=True, atualizado=agora())
            vincular_reenvio(Q, Q["cards"][cid], Q["lotes"][lid])
        return lid

def mover(ids, coluna):
    """Arrasta cards para uma coluna. Entrar numa coluna que roda = entrar na fila."""
    if coluna not in dict(COLUNAS): raise ValueError("coluna inválida")
    with mexer() as Q:
        for i in ids:
            c = Q["cards"].get(i)
            if not c or c["estado"] == "rodando": continue
            destino = "edicao" if coluna == "broll" and c.get("sem_novas_buscas") else coluna
            c.update(coluna=destino, atualizado=agora(), msg="")
            c["estado"] = "espera" if destino in RODA else "parado"
            if destino in RODA: c["revisado"] = False
    return True

def marcar_revisado(cid, valor=True):
    with mexer() as Q:
        c = Q["cards"].get(cid)
        if c: c.update(revisado=bool(valor), atualizado=agora())

def apagar(ids):
    with mexer() as Q:
        for i in ids: Q["cards"].pop(i, None)
        vivos = {c["lote"] for c in Q["cards"].values()}
        for l in [l for l in Q["lotes"] if l not in vivos]: Q["lotes"].pop(l, None)

def pausar(v):
    with mexer() as Q: Q["pausada"] = bool(v)

# ---------------------------------------------------------------- o que rodar agora
def proximo(Q, vagas=1):
    """Até `vagas` próximos trabalhos prontos para rodar agora: (tipo, chave) cada um. Lote de broll na frente,
    depois cards de edição, depois render — mas render nunca ganha mais de UM ao mesmo tempo (sozinho já satura
    a máquina; rodar dois só deixaria os dois mais lentos, sem ganhar nada)."""
    if Q.get("pausada") or vagas <= 0: return []
    render_ocupado = any(c["estado"] == "rodando" and c["coluna"] == "render" for c in Q["cards"].values())
    escolhidos = []
    pastas_escolhidas = set()  # dois cards com o mesmo nome (ainda sem projeto atribuído) usam a MESMA pasta —
                                # nunca escolher os dois juntos, senão rodam fase_projeto() um em cima do outro
    for col in ("broll", "edicao", "render"):
        if len(escolhidos) >= vagas: break
        if col == "render" and render_ocupado: continue
        prontos = [c for c in Q["cards"].values() if c["coluna"] == col and c["estado"] == "espera"]
        if not prontos: continue
        if col == "broll":
            por_lote = {}
            for c in prontos: por_lote.setdefault(c["lote"], []).append(c)
            for lid in sorted(por_lote, key=lambda lid: min(cc["atualizado"] for cc in por_lote[lid])):
                if len(escolhidos) >= vagas: break
                escolhidos.append(("lote", lid))
        else:
            for c in sorted(prontos, key=lambda c: c["atualizado"]):
                if len(escolhidos) >= vagas: break
                pasta = c.get("projeto") or pasta_projeto(c["nome"])
                if pasta in pastas_escolhidas: continue
                pastas_escolhidas.add(pasta)
                escolhidos.append(("card", c["id"]))
                if col == "render": render_ocupado = True; break
    return escolhidos

def _reservar(vagas_max):
    """Sob a trava do quadro: escolhe até `vagas_max` trabalhos prontos, sem passar do limite de concorrência do
    workspace, marca os cards 'rodando' (reserva atômica — ninguém mais pode pegar o mesmo trabalho) e devolve o
    necessário para rodá-los de verdade FORA da trava."""
    with mexer() as Q:
        rodando = sum(1 for c in Q["cards"].values() if c["estado"] == "rodando")
        vagas = min(vagas_max, LIMITE_CONCORRENCIA - rodando)
        unidades = []
        for tipo, chave in proximo(Q, vagas):
            coluna_alvo = "broll" if tipo == "lote" else Q["cards"][chave]["coluna"]
            alvos = [c for c in Q["cards"].values() if (c["lote"] == chave if tipo == "lote" else c["id"] == chave)
                     and c["estado"] == "espera" and c["coluna"] == coluna_alvo]
            if not alvos: continue
            for c in alvos: c.update(estado="rodando", atualizado=agora(), msg="")
            col = alvos[0]["coluna"]; lote = dict(Q["lotes"][alvos[0]["lote"]]); cards = [dict(c) for c in alvos]
            unidades.append((tipo, chave, col, lote, cards))
        return unidades

def _executar(tipo, chave, col, lote, cards, log):
    """Roda de fato um trabalho (bloqueia até terminar) e grava o resultado. Chamada tanto de passo() (síncrono,
    na própria thread de quem chamou) quanto de lancar_prontos() (numa thread própria, uma por trabalho) — por
    isso TODO o corpo fica dentro de um try/except: numa thread solta (daemon, sem quem chamou esperando), uma
    exceção que escapasse não iria para lugar nenhum — nem para o log, nem para o laço, só sumiria."""
    try:
        try:
            if col == "broll": fase_broll(lote, cards, log)
            else:
                for c in cards: fase_projeto(col, lote, c, log)
            fim, msg = "ok", ""
        except Exception as e:
            fim, msg = "erro", str(e)[:400]
            log(f"kanban: {col} falhou — {msg}")
        with mexer() as Q:
            for c in cards:
                x = Q["cards"].get(c["id"])
                if not x or x["estado"] != "rodando": continue
                if col == "broll" and fim == "ok":
                    # Busca deu certo: segue sozinho para a edição, sem esperar você arrastar.
                    x.update(coluna="edicao", estado="espera", revisado=False, msg="", atualizado=agora())
                else:
                    x.update(estado=fim, msg=msg, atualizado=agora())
        return dict(coluna=col, tipo=tipo, chave=chave, estado=fim)
    except Exception as e:
        log(f"kanban: não consegui terminar de processar {col} ({chave}) — {str(e)[:300]}")
        return dict(coluna=col, tipo=tipo, chave=chave, estado="erro")

def _nova_thread(alvo, args):
    """threading.Thread comum não herda o workspace ativo (contextvar) — copia o contexto de agora (de quem
    chama, já com o workspace certo) e roda a thread nova dentro dele."""
    ctx = contextvars.copy_context()
    t = threading.Thread(target=lambda: ctx.run(alvo, *args), daemon=True)
    t.start()
    return t

def passo(log=print):
    """Executa UM trabalho da fila, esperando terminar (bloqueia). Devolve o que rodou, ou None se não havia
    nada. Usado pela CLI e pelos testes; laco() usa lancar_prontos() para não bloquear a fila inteira num
    trabalho só."""
    unidades = _reservar(1)
    if not unidades: return None
    return _executar(*unidades[0], log)

def lancar_prontos(log=print):
    """Preenche as vagas livres da fila agora, cada trabalho na sua própria thread — não espera nenhum terminar.
    Devolve quantos trabalhos novos começaram a rodar nesta chamada."""
    unidades = _reservar(LIMITE_CONCORRENCIA)
    for unidade in unidades:
        _nova_thread(_executar, (*unidade, log))
    return len(unidades)

def laco(parar=None, espera=3.0, log=print):
    """Laço da fila: preenche as vagas livres de cada workspace, sem bloquear em nenhum trabalho — cada um roda
    na sua própria thread (lancar_prontos()). O servidor chama isto numa thread, uma vez só pra vida inteira do
    processo — não dá pra confiar no workspace de uma requisição específica; em cada volta, passa por TODOS os
    workspaces (cada um tem sua própria fila) e lança o que couber de cada um."""
    import workspaces
    while not (parar and parar.is_set()):
        try:
            algum = False
            for w in workspaces.listar():
                tok = comum.definir_workspace(None if w["id"] == workspaces.PADRAO_ID else w)
                try:
                    if lancar_prontos(log) > 0: algum = True
                finally:
                    comum.resetar_workspace(tok)
            if not algum: time.sleep(espera)
        except Exception as e:
            log(f"kanban: laço tropeçou — {e}"); time.sleep(espera)

# ---------------------------------------------------------------- fases
def roda_cmd(cmd, log, cwd=None):
    """Roda com prioridade baixa (nice), para não atrapalhar a prévia do editor. Herda o workspace ativo agora
    (definido por laco()/_nova_thread() antes de chamar, ou pela requisição que chamou isto direto). Com mais de
    um trabalho rodando ao mesmo tempo (LIMITE_CONCORRENCIA > 1), reparte as threads de matting/transcrição entre
    eles — cada um sozinho tenta usar a máquina inteira, então sem isso dois ao mesmo tempo só atrapalhariam um
    ao outro sem ganhar nada; não mexe se o ambiente já trouxer um valor explícito."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))}
    env["ESTUDIO_RAIZ"] = comum.RAIZ; env["ESTUDIO_BROLLS"] = comum.BROLLS
    por_trabalho = str(max(1, (os.cpu_count() or 4) // LIMITE_CONCORRENCIA))
    env.setdefault("ESTUDIO_ONNX_THREADS", por_trabalho); env.setdefault("ESTUDIO_WHISPER_THREADS", por_trabalho)
    p = subprocess.Popen(["nice", "-n", "10", *cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=cwd, env=env)
    ult = ""
    for linha in p.stdout:
        ult = linha.rstrip()
        if ult: log(ult[:300])
    p.wait()
    if p.returncode: raise RuntimeError(ult[-300:] or f"saiu com código {p.returncode}")

def fase_broll(lote, cards, log):
    """Uma leva com os anúncios deste lote: transcreve todos, define as buscas e enche a pasta da oferta."""
    if any(c.get("sem_novas_buscas") for c in cards):
        raise ValueError("este reenvio usa os B-rolls existentes e não pode iniciar outra busca")
    # O id do lote entra no nome da pasta: só o timestamp (segundo a segundo) colidiria se dois lotes da mesma
    # oferta começarem no mesmo segundo — coisa que só pode acontecer agora que broll roda mais de um por vez.
    dl = biblioteca.pasta_oferta(lote["expert"], lote["oferta"], ".levas",
                                  f"{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{lote['id']}")
    os.makedirs(os.path.join(dl, "anuncios"), exist_ok=True)
    anuncios = []
    for k, c in enumerate(cards, 1):
        dest = os.path.join(dl, "anuncios", f"{k:02d}-{re.sub(r'[/:]', '-', c['nome'])}{os.path.splitext(c['video'])[1].lower()}")
        shutil.copy2(c["video"], dest); anuncios.append(dict(nome=c["nome"], arquivo=dest))
    pedido_arq = os.path.join(dl, "pedido.json"); tmp = pedido_arq + ".tmp"
    with open(tmp, "w") as f:
        json.dump(dict(expert=lote["expert"], oferta=lote["oferta"], anuncios=anuncios, template=lote["estilo"],
                       fontes=lote.get("fontes") or ["tiktok", "youtube"], criado=time.strftime("%Y-%m-%d %H:%M:%S"),
                       kanban=lote["id"]), f, ensure_ascii=False, indent=1)
    os.replace(tmp, pedido_arq)
    log(f"kanban: leva de {len(cards)} anúncio(s) em {dl}")
    # A tela acompanha a leva desde o início e conserva seu diagnóstico em caso
    # de erro. Os caminhos existem antes de iniciar a transcrição/busca.
    with mexer() as Q:
        for k, c in enumerate(cards, 1):
            x = Q["cards"].get(c["id"])
            if x: x.update(leva=dl, transcricao=os.path.join(dl, "transcricoes", f"{k:02d}.json"), video_leva=anuncios[k - 1]["arquivo"])
    roda_cmd([VENV, os.path.join(LIB, "leva.py"), dl], log, cwd=dl)
    try: resultado = json.load(open(os.path.join(dl, "estado.json")))
    except (OSError, ValueError): raise RuntimeError("a busca de B-roll terminou sem registrar seu resultado")
    if resultado.get("status") != "pronto" or (resultado.get("resumo") or {}).get("sem_broll"):
        raise RuntimeError(resultado.get("mensagem") or "a busca de B-roll não concluiu com clipes utilizáveis")

def pasta_projeto(nome): return os.path.join(comum.RAIZ, re.sub(r"[/:]", "-", nome).strip())

def fase_projeto(col, lote, card, log):
    """Edição de um vídeo já cortado; render mantém os projetos existentes."""
    if col not in ("edicao", "render"): raise ValueError("fase de projeto inválida")
    d = card.get("projeto") and os.path.join(comum.RAIZ, card["projeto"]) or pasta_projeto(card["nome"])
    pedido_arq = os.path.join(d, "auto", "pedido.json")
    video = card["video"] if card.get("sem_novas_buscas") else (card.get("video_leva") or card["video"])
    if os.path.exists(pedido_arq):
        ped = json.load(open(pedido_arq))
        if ped.get("kanban") != card["id"] or os.path.realpath(ped.get("video") or "") != os.path.realpath(video):
            raise ValueError("este nome ainda pertence ao projeto anterior; exclua a edição antiga antes do reenvio")
        if col == "edicao" and not ped.get("video_ja_cortado"):
            raise ValueError("este projeto usa o fluxo antigo; reenvie o vídeo já cortado como nova demanda")
    else:
        if os.path.exists(os.path.join(d, "projeto.json")):
            raise ValueError("já existe uma edição com este nome; os arquivos anteriores foram preservados")
        os.makedirs(os.path.join(d, "auto"), exist_ok=True)
        leva = card.get("leva")
        origem = dict(nome=card["nome"], leva=os.path.basename(leva)) if leva else None
        ped = dict(nome=card["nome"], estilo=lote["estilo"], video=video, roteiro=None, expert=lote["expert"],
                   oferta=lote["oferta"], sigla="", nicho=biblioteca.nome_oferta(lote["oferta"]),
                   buscar_tiktok=False, buscar_youtube=False, renderizar=True, conferir=False,
                   video_ja_cortado=True, sem_novas_buscas=bool(card.get("sem_novas_buscas")),
                   anuncio=card["nome"], anuncio_origem=origem,
                   whisper=None if card.get("sem_novas_buscas") else card.get("transcricao"),
                   pedido=time.strftime("%Y-%m-%d %H:%M"), kanban=card["id"],
                   leva_termos=os.path.join(leva, "termos.json") if leva else None)
        tmp = pedido_arq + ".tmp"
        with open(tmp, "w") as f: json.dump(ped, f, ensure_ascii=False, indent=1)
        os.replace(tmp, pedido_arq)
    with mexer() as Q:
        x = Q["cards"].get(card["id"])
        if x: x["projeto"] = os.path.basename(d)
    bp = comum.estilo(lote["estilo"]).get("modo") == "broll_primeiro"
    ate = ("letreiros" if bp else "montagem") if col == "edicao" else "render"
    log(f"kanban: {card['nome']} → {col} (até '{ate}', vídeo enviado já cortado)")
    roda_cmd([VENV, os.path.join(LIB, "auto.py"), d, "--ate", ate], log, cwd=d)

# ---------------------------------------------------------------- leitura para a tela
def quadro():
    """O quadro pronto para a tela: lotes, cards com o estado do projeto e o que cada um está fazendo."""
    Q = ler(); out = []
    for c in sorted(Q["cards"].values(), key=lambda c: (c["lote"], c["nome"])):
        d = os.path.join(comum.RAIZ, c["projeto"]) if c.get("projeto") else None
        est = None
        if d and os.path.exists(os.path.join(d, "auto", "estado.json")):
            try: est = json.load(open(os.path.join(d, "auto", "estado.json")))
            except (OSError, ValueError): est = None
        broll = None
        if c.get("leva"):
            try:
                busca = json.load(open(os.path.join(c["leva"], "estado.json")))
                broll = dict(status=busca.get("status"), mensagem=busca.get("mensagem", ""),
                             etapa=next((e["nome"] for e in reversed(busca.get("etapas", [])) if e["estado"] == "rodando"), None),
                             resumo=busca.get("resumo", {}), log=busca.get("log", [])[-12:],
                             falhas_busca=busca.get("falhas_busca", []))
                if c["coluna"] == "broll": est = busca
            except (OSError, ValueError): pass
        capa = None
        if d:
            for v in ("A", "B", "C"):
                p = os.path.join(d, "versoes", v, "capa.jpg")
                if os.path.exists(p): capa = p; break
        tela = dict(c)
        if c["coluna"] == "cortes":
            tela.update(coluna="edicao", estado="parado", revisado=False, msg="Aguardando vídeo já cortado")
        pode_abrir = bool(d and tela["coluna"] == "edicao" and tela["estado"] == "ok"
                          and all(os.path.isfile(os.path.join(d, "versoes", "A", n)) for n in ("plano.json", "pm.mp4", "whisper.json", "mapa.json")))
        out.append(dict(tela, capa=capa, broll=broll, pode_abrir_editor=pode_abrir,
                        etapa=next((e["nome"] for e in reversed((est or {}).get("etapas", [])) if e["estado"] == "rodando"), None),
                        auto_status=(est or {}).get("status"), log=(est or {}).get("log", [])[-12:]))
    return dict(colunas=[dict(id=i, nome=n) for i, n in COLUNAS], lotes=list(Q["lotes"].values()), cards=out,
                pausada=Q.get("pausada", False),
                reenvios_pendentes=[{k: r.get(k) for k in ("nome", "expert", "oferta")}
                                    for r in Q.get("reenvios", {}).values() if r.get("estado") == "pendente"])

if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["estado"]:
        q = quadro()
        for col in q["colunas"]:
            cs = [c for c in q["cards"] if c["coluna"] == col["id"]]
            print(f"{col['nome']:22} {len(cs):2}  " + ", ".join(f"{c['nome']}[{c['estado']}]" for c in cs))
    elif a[:1] == ["passo"]: print(passo())
    elif a[:1] == ["laco"]: laco()
    else: print(__doc__)
