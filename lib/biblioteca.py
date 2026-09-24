"""Biblioteca de B-rolls: tudo que as buscas trazem fica guardado em ~/B-rolls/biblioteca/ com o que o Gemini viu (descrição,
tags, trechos limpos, onde aparece texto), para a próxima edição olhar aqui antes de buscar de novo.

Cada B-roll tem um estado: "disponivel" ou "usado" (entrou num vídeo renderizado; o render marca sozinho). Os usados não
voltam para edições novas, a não ser que alguém devolva (aba B-rolls do Estúdio, ou `liberar`).

  python3 biblioteca.py listar [--estado disponivel|usado] [--nicho Glúteo]
  python3 biblioteca.py procurar "mulher agachando com elástico" [--nicho Glúteo]
  python3 biblioteca.py estudar [ID ...]          estuda com o Gemini os que ainda não têm descrição (ou os IDs dados)
  python3 biblioteca.py importar-obsidian         traz o acervo antigo (fichas do Obsidian), com o estado de cada ficha
  python3 biblioteca.py liberar ID                volta um usado para disponível
  python3 biblioteca.py reaplicar [ID ...]        passa a regra de descarte de novo (de graça): clipe com texto sai
Só usa a biblioteca padrão."""
import os, re, sys, json, time, fcntl, unicodedata, subprocess, contextlib, glob, datetime
LIB = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, LIB)
import comum

PATH = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")

def brolls():
    """~/B-rolls por padrão; um teste que faça patch.object(biblioteca, "BROLLS", ...) tem prioridade máxima
    (globals() enxerga o patch, já que ele grava direto no __dict__ do módulo); senão segue comum.brolls() — o
    workspace ativo da requisição (dinâmico por thread — ver comum.definir_workspace), ou, sem workspace ativo,
    o padrão calculado de ESTUDIO_BROLLS. ESTUDIO_BROLLS nunca sobrepõe um workspace ativo: comum.brolls() já
    trata isso (senão todo workspace cairia na mesma pasta fixa, vazando B-roll entre eles)."""
    patch = globals().get("BROLLS")
    return patch if patch is not None else comum.brolls()

def raiz():
    """Igual ideia: com workspace ativo, é sempre <brolls do workspace>/biblioteca. Sem workspace ativo (CLI,
    testes, instalação avulsa), ESTUDIO_BIBLIOTECA pode forçar outro caminho."""
    patch = globals().get("RAIZ")
    if patch is not None: return patch
    if comum.workspace_ativo() is None and os.environ.get("ESTUDIO_BIBLIOTECA"):
        return os.path.abspath(os.path.expanduser(os.environ["ESTUDIO_BIBLIOTECA"]))
    return os.path.join(brolls(), "biblioteca")

def idx():
    patch = globals().get("IDX")
    return patch if patch is not None else os.path.join(raiz(), "biblioteca.json")

def __getattr__(nome):
    if nome == "RAIZ": return raiz()
    if nome == "BROLLS": return brolls()
    if nome == "IDX": return idx()
    raise AttributeError(f"module {__name__!r} has no attribute {nome!r}")

def pasta(*p):
    d = os.path.join(raiz(), *p); os.makedirs(d, exist_ok=True); return d

# ---------------------------------------------------------------- experts, ofertas e categorias (pastas em ~/B-rolls)
def nome_oferta(oferta):
    """"Oferta Glúteo (definir)" -> "Glúteo": é o "nicho" que a edição e a aba B-rolls usam para filtrar."""
    return re.sub(r"^Oferta\s+|\s*\(definir\)$", "", str(oferta or "")).strip() or str(oferta or "")

def pasta_oferta(expert, oferta, *sub):
    d = os.path.join(brolls(), expert, oferta, *sub); os.makedirs(d, exist_ok=True); return d

def _pastas(d):
    try: return sorted(e.name for e in os.scandir(d) if e.is_dir() and not e.name.startswith(".") and e.name != os.path.basename(raiz()))
    except OSError: return []

def arvore():
    """Pastas para a aba B-rolls: [{expert, disponivel, usado, ofertas: [{oferta, nicho, disponivel, usado, descartado, sem_ficha,
    categorias: [{nome, disponivel, usado, capas}]}]}] — as pastas de ~/B-rolls com as contas da biblioteca."""
    B = list(ler()["itens"].values()); out = []
    def conta(xs, est): return sum(1 for x in xs if x["estado"] == est)
    for ex in _pastas(brolls()):
        ofs = []
        for of in _pastas(os.path.join(brolls(), ex)):
            dela = [x for x in B if x.get("expert") == ex and x.get("oferta") == of]
            cats = []
            for c in _pastas(os.path.join(brolls(), ex, of)):
                dc = [x for x in dela if x.get("categoria") == c and x["estado"] != "descartado"]
                cats.append(dict(nome=c, disponivel=conta(dc, "disponivel"), usado=conta(dc, "usado"), clipes=conta(dc, "disponivel"),
                                 capas=[x["capa"] for x in sorted(dc, key=lambda x: -(x.get("nota") or 0)) if x.get("capa")][:3]))
            ofs.append(dict(oferta=of, nicho=nome_oferta(of), categorias=cats, disponivel=conta(dela, "disponivel"), usado=conta(dela, "usado"),
                            descartado=conta(dela, "descartado"), clipes=conta(dela, "disponivel"),
                            sem_ficha=sum(1 for x in dela if x["estado"] == "disponivel" and not x.get("estudo"))))
        out.append(dict(expert=ex, ofertas=ofs, disponivel=sum(o["disponivel"] for o in ofs), usado=sum(o["usado"] for o in ofs)))
    return out

def mover(i, expert, oferta, categoria):
    """Leva o arquivo do clipe para ~/B-rolls/<expert>/<oferta>/<categoria>/ e anota no índice (descartado vai para .descartados)."""
    with mexer() as B:
        it = B["itens"].get(i)
        if not it: return None
        destino = pasta_oferta(expert, oferta, ".descartados" if it.get("estado") == "descartado" else categoria)
        base = os.path.basename(it["arquivo"]); novo = os.path.join(destino, base if base.startswith(f"{i}-") else f"{i}-{base}")
        if os.path.realpath(it["arquivo"]) != os.path.realpath(novo) and os.path.exists(it["arquivo"]):
            os.replace(it["arquivo"], novo); it["arquivo"] = novo
        it.update(expert=expert, oferta=oferta, categoria=categoria, nicho=it.get("nicho") or nome_oferta(oferta))
        return it["arquivo"]

def marcar_anuncios(ids, nomes):
    """Etiqueta "Anúncio 01"… nos clipes que a busca trouxe para esses anúncios (a edição deles olha estes primeiro)."""
    with mexer() as B:
        for i in ids:
            it = B["itens"].get(i)
            if it: it["anuncios"] = sorted(set(it.get("anuncios", [])) | set(nomes))

def migrar():
    """Clipes antigos (só com "nicho"): preenche expert/oferta pela pasta que tem esse nicho e a categoria pela pasta do arquivo."""
    mapa = {nome_oferta(of): (ex, of) for ex in _pastas(brolls()) for of in _pastas(os.path.join(brolls(), ex))}
    n = 0
    with mexer() as B:
        for it in B["itens"].values():
            if it.get("oferta") or it.get("nicho") not in mapa: continue
            ex, of = mapa[it["nicho"]]; it.update(expert=ex, oferta=of); n += 1
            rel = os.path.relpath(os.path.realpath(it["arquivo"]), os.path.realpath(os.path.join(brolls(), ex, of)))
            if not rel.startswith("..") and os.sep in rel: it["categoria"] = rel.split(os.sep)[0]
    return n

@contextlib.contextmanager
def mexer():
    """Lê, deixa mexer e grava de volta, com trava (o servidor, a edição automática e o chat podem mexer ao mesmo tempo)."""
    os.makedirs(raiz(), exist_ok=True)
    with open(os.path.join(raiz(), ".trava"), "w") as tv:
        fcntl.flock(tv, fcntl.LOCK_EX)
        B = ler(); yield B
        tmp = idx() + ".tmp"; json.dump(B, open(tmp, "w"), ensure_ascii=False, indent=1); os.replace(tmp, idx())

def ler():
    try: return json.load(open(idx()))
    except (OSError, ValueError): return dict(itens={}, proximo=1)

def normal(t):
    t = unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", t)

def sonda(arq):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                        "-of", "json", arq], capture_output=True, text=True, env=dict(os.environ, PATH=PATH))
    try:
        j = json.loads(r.stdout); s = (j.get("streams") or [{}])[0]
        return dict(dur=round(float(j["format"].get("duration") or 0), 2), w=s.get("width") or 0, h=s.get("height") or 0)
    except (ValueError, KeyError): return dict(dur=0, w=0, h=0)

def capa(arq, destino, dur):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{min(1.0, max(dur / 3, 0)):.2f}", "-i", arq, "-frames:v", "1", "-vf", "scale=240:-2", destino],
                   capture_output=True, env=dict(os.environ, PATH=PATH))
    return destino if os.path.exists(destino) else ""

def chave_de(d):
    if d.get("fonte") == "tiktok" and d.get("tiktok_id"): return "tiktok:" + str(d["tiktok_id"])
    if d.get("fonte") == "youtube": return f"youtube:{d.get('video_id')}:{int(d.get('yt_ini') or 0)}"
    return "arquivo:" + os.path.realpath(d["arquivo"])

def achar(B, chave):
    return next((i for i, it in B["itens"].items() if it.get("chave") == chave), None)

def adicionar(**d):
    """Guarda um B-roll (arquivo já baixado). Se já existe, devolve o ID que ele tem. Campos: arquivo, fonte, url, autor,
    views, termo, busca, nicho, tiktok_id | video_id + yt_ini, titulo."""
    d["chave"] = chave_de(d)
    with mexer() as B:
        ja = achar(B, d["chave"])
        if ja: return ja
        i = f"B{B['proximo']:04d}"; B["proximo"] += 1
        info = sonda(d["arquivo"]); th = capa(d["arquivo"], os.path.join(pasta("capas"), i + ".jpg"), info["dur"])
        B["itens"][i] = dict(d, id=i, estado="disponivel", usos=[], baixado=datetime.date.today().isoformat(), capa=th, **info)
        return i

def motivo_descarte(e):
    """Clipe que não entra na biblioteca de disponíveis. B-roll é imagem limpa: QUALQUER texto queimado (legenda, título,
    contador, seta, @, marca d'água) derruba o clipe — não importa onde esteja nem se dá para recortar em card."""
    txt = e.get("texto_na_tela") or []
    if txt:
        t = max(txt, key=lambda x: (x.get("tamanho") == "grande", float(x.get("fim", 0)) - float(x.get("ini", 0))))
        return f"texto na tela ({t.get('onde', '?')}, {t.get('tamanho', '?')})"
    if not e.get("serve_como_broll", True): return "não serve como B-roll"
    f = e.get("formatos") or {}
    if f and not any((f.get(k) or {}).get("serve") for k in ("cheia", "canto", "dividida", "card")):
        return "não funciona em nenhum formato"
    if "trechos_limpos" in e and not e["trechos_limpos"]:
        return "nenhum trecho aproveitável (gente falando para a câmera ou corte o tempo todo)"
    return None

def reaplicar(ids=None):
    """Passa a regra de descarte de novo em disponíveis e descartados (de graça, sem reestudar). Usado nunca muda.
    Devolve (saíram, voltaram)."""
    saiu, voltou = [], []
    with mexer() as B:
        for i, it in B["itens"].items():
            if (ids and i not in ids) or not it.get("estudo") or it.get("estado") not in ("disponivel", "descartado"): continue
            m = motivo_descarte(it["estudo"])
            if m and it["estado"] == "disponivel": it.update(estado="descartado", descarte=m); saiu.append(i)
            elif not m and it["estado"] == "descartado": it["estado"] = "disponivel"; it.pop("descarte", None); voltou.append(i)
            elif m and it["estado"] == "descartado": it["descarte"] = m
    return saiu, voltou

def anuncios_usando(it):
    """Anúncios ainda existentes que usaram este clipe (usos.projeto apontando pra pasta já apagada não conta:
    quem excluiu o anúncio já aceitou perder aquele histórico, só o B-roll continua protegido)."""
    return sorted({u["projeto"] for u in (it.get("usos") or [])
                   if u.get("projeto") and os.path.isdir(os.path.join(comum.raiz(), u["projeto"]))})

def apagar(ids):
    """Tira os clipes do índice E do disco (o arquivo, a capa e a miniatura). Não volta atrás.
    Recusa apagar um clipe que algum anúncio ainda existente usou — apagar o arquivo quebraria um render
    futuro desse anúncio. Devolve quem foi apagado, quem não achou e quem ficou em uso (com os anúncios)."""
    sumidos, faltando, em_uso = [], [], {}
    with mexer() as B:
        for i in ids:
            it = B["itens"].get(i)
            if not it: faltando.append(i); continue
            usado_por = anuncios_usando(it)
            if usado_por: em_uso[i] = usado_por; continue
            del B["itens"][i]
            for caminho in (it.get("arquivo"), it.get("capa"), it.get("thumb")):
                try:
                    if caminho and os.path.exists(caminho) and os.path.realpath(caminho).startswith(os.path.realpath(brolls()) + "/"):
                        os.remove(caminho)
                except OSError: pass
            sumidos.append(i)
    return dict(apagados=sumidos, nao_achei=faltando, em_uso=em_uso)

def revisar_descartados(ids=None):
    """Só o lado bom da regra: descartado que passa volta para disponível."""
    return reaplicar([i for i, x in ler()["itens"].items()
                      if x.get("estado") == "descartado" and (not ids or i in ids)])[1]

def gravar_estudo(i, e, modelo):
    with mexer() as B:
        it = B["itens"][i]
        it.update(descricao=e.get("descricao", ""), tags=e.get("tags", []), estudo=e, estudado_por=modelo,
                  estudado_em=time.strftime("%Y-%m-%dT%H:%M:%S"), nota=e.get("nota"), serve=e.get("serve_como_broll", True))
        m = motivo_descarte(e)
        if m and it.get("estado") == "disponivel": it.update(estado="descartado", descarte=m)
        if not m and it.get("estado") == "descartado": it["estado"] = "disponivel"; it.pop("descarte", None)   # reestudo salvou o clipe

def item(i): return ler()["itens"].get(i)

def itens(estado=None, nicho=None, q=None, expert=None, oferta=None, categoria=None):
    L = list(ler()["itens"].values())
    if estado: L = [x for x in L if x["estado"] == estado]
    if nicho: L = [x for x in L if normal(x.get("nicho") or "") == normal(nicho)]
    if expert: L = [x for x in L if x.get("expert") == expert]
    if oferta: L = [x for x in L if x.get("oferta") == oferta]
    if categoria: L = [x for x in L if x.get("categoria") == categoria]
    if q:
        ts = set(normal(q).split())
        L = [x for x in L if ts & set(normal(" ".join([x.get("descricao", ""), " ".join(x.get("tags", [])), x.get("termo", ""), x.get("categoria") or "",
                                                       " ".join(x.get("anuncios", []))])).split())]
    return sorted(L, key=lambda x: x["id"], reverse=True)

VAZIAS = set("a o e de do da dos das em no na nos nas com para por um uma the of and in on with at to".split())

def procurar(texto, nicho=None, n=12, excluir=(), so_estudados=True, expert=None, oferta=None):
    """Disponíveis (e já estudados, a não ser so_estudados=False) que mais combinam com o texto, os de nota maior primeiro.
    expert/oferta: só a pasta ~/B-rolls/<expert>/<oferta> (a edição nunca sai da pasta escolhida)."""
    q = [t for t in normal(texto).split() if t not in VAZIAS and len(t) > 2]
    if not q: return []
    res = []
    for x in itens("disponivel", nicho, None, expert, oferta):
        if x["id"] in excluir or (so_estudados and not x.get("estudo")) or x.get("serve") is False: continue
        tags = set(normal(" ".join(x.get("tags", []))).split()); desc = set(normal(x.get("descricao", "") + " " + x.get("titulo", "")).split())
        termo = set(normal(x.get("termo", "")).split())
        s = sum(2.0 if t in tags else 1.0 if t in desc else 0.5 if t in termo else 0 for t in q)
        if s > 0: res.append((s + (x.get("nota") or 5) / 10, x))
    return [x for _, x in sorted(res, key=lambda r: -r[0])[:n]]

def marcar_usado(ids, projeto, versao=""):
    hoje = time.strftime("%Y-%m-%d")
    with mexer() as B:
        for i in ids:
            it = B["itens"].get(i)
            if not it: continue
            it["estado"] = "usado"
            if not any(u.get("projeto") == projeto and u.get("versao") == versao for u in it["usos"]):
                it["usos"].append(dict(projeto=projeto, versao=versao, quando=hoje))

def mudar_estado(i, estado):
    with mexer() as B:
        if i in B["itens"] and estado in ("disponivel", "usado", "descartado"):
            B["itens"][i]["estado"] = estado
            if estado == "disponivel": B["itens"][i].pop("descarte", None)
            return True
    return False

def mudar_estados(ids, estado):
    """Igual mudar_estado, mas para vários clipes numa trava só (seleção em massa na aba Arquivos)."""
    if estado not in ("disponivel", "usado", "descartado"): raise ValueError("estado inválido")
    mudados, nao_achei = [], []
    with mexer() as B:
        for i in ids:
            it = B["itens"].get(i)
            if not it: nao_achei.append(i); continue
            it["estado"] = estado
            if estado == "disponivel": it.pop("descarte", None)
            mudados.append(i)
    return dict(mudados=mudados, nao_achei=nao_achei)

def de_src(src):
    """ID do B-roll da biblioteca que tem este arquivo (ou None)."""
    rp = os.path.realpath(src)
    return next((i for i, it in ler()["itens"].items() if os.path.realpath(it["arquivo"]) == rp), None)

def resumo_estudo(x):
    """A ficha do clipe numa linha (lib/ficha.py), para escolher sem abrir o vídeo."""
    import ficha
    return ficha.resumo(x)

def acervo_de(ids, categoria="Biblioteca"):
    """Itens no formato do acervo do editor (o próprio arquivo serve de prévia)."""
    B = ler()["itens"]; out = []
    for i in ids:
        x = B.get(i)
        if not x or not os.path.exists(x["arquivo"]): continue
        out.append(dict(id=x["id"], categoria=categoria, nome=(x.get("descricao") or x["id"])[:60], descricao=x.get("descricao", ""),
                        src=x["arquivo"], proxy=x["arquivo"], thumb=x.get("capa", ""), dur=x.get("dur", 0), w=x.get("w", 0), h=x.get("h", 0),
                        origem=x.get("url", ""), estado=x["estado"]))
    return out

MOTOR_FORCADO = None                                  # a edição automática troca para o Claude se o Gemini não responder

def motor_estudo():
    """Quem estuda os clipes (⚙): gemini (padrão: pela OpenRouter ou pela chave do Google), claude-opus-5 ou claude-sonnet-5.
    Gemini escolhido sem nenhuma chave dele -> Claude Opus."""
    import chaves, gemini
    m = MOTOR_FORCADO or chaves.ler().get("modelo_estudo") or "gemini"
    return "claude-opus-5" if m == "gemini" and not gemini.chave() else m

def estudar_pendentes(ids=None, contexto="", ao_cobrar=None, log=print, paralelo=6, progresso=None, refazer=False):
    """Estuda (uma vez) os clipes sem ficha. Claude: folha de contato por clipe (lib/estudo.py, precisa do Python da .venv);
    Gemini: o vídeo inteiro. progresso(feitos, total) é chamado a cada clipe. refazer=True estuda de novo quem já tem ficha
    (usado no "reestudar os descartados", para a regra nova valer para eles)."""
    from concurrent.futures import ThreadPoolExecutor
    B = ler()["itens"]; alvo = [B[i] for i in (ids or [i for i, x in B.items() if not x.get("estudo")]) if i in B and (refazer or not B[i].get("estudo"))]
    motor = motor_estudo(); feitos = []; parou = []; n = [0]
    if motor == "gemini":
        import gemini
        modelo = gemini.modelo_padrao()
        def estuda(x): return gemini.estudar(x["arquivo"], contexto or x.get("termo", ""), modelo=modelo, ao_cobrar=ao_cobrar, log=log)
    else:
        import ia, estudo
        modelo = motor; cl = ia.Claude(modelo=modelo, log=lambda *_: None, ao_cobrar=ao_cobrar)
        def estuda(x): return estudo.estudar(x["arquivo"], contexto or (x.get("termo") or "")[:200], cl=cl, rotulo=f"estudo {x['id']}")
    def um(x):
        if parou: return None
        try:
            e = estuda(x); gravar_estudo(x["id"], e, modelo); return x["id"]
        except Exception as ex:
            if getattr(ex, "fatal", False) or "authentication" in str(ex).lower() or "credit" in str(ex).lower():
                if not parou: parou.append(1); log(f"o estudo parou: {str(ex)[:220]}")
                return None
            log(f"{x['id']}: não consegui estudar ({str(ex)[:160]})"); return None
        finally:
            n[0] += 1
            if progresso:
                try: progresso(n[0], len(alvo))
                except Exception: pass                   # o aviso de progresso nunca derruba o estudo
    with ThreadPoolExecutor(paralelo) as ex: feitos = [r for r in ex.map(um, alvo) if r]
    return feitos

def importar_obsidian(vault=os.path.expanduser("~/Keeps/03 Edição")):
    n = 0
    for p in sorted(glob.glob(os.path.join(vault, "**", "*-BR*.md"), recursive=True)):
        t = open(p, encoding="utf-8", errors="ignore").read(); fm = re.match(r"---\n(.*?)\n---", t, re.S)
        if not fm: continue
        f = {}
        for l in fm.group(1).splitlines():
            if ":" in l: k, v = l.split(":", 1); f[k.strip()] = v.strip().strip('"')
        if f.get("tipo") != "broll" or not os.path.exists(f.get("caminho", "")): continue
        cat = os.path.basename(os.path.dirname(p)); oferta = os.path.basename(os.path.dirname(os.path.dirname(p)))
        oferta = re.sub(r"^Oferta\s+|\s*\(definir\)$", "", oferta).strip() or oferta
        i = adicionar(arquivo=f["caminho"], fonte="tiktok", tiktok_id=f.get("tiktok_id"), url=f.get("origem_url", ""), autor=f.get("autor", ""),
                      termo=f.get("termo", ""), busca=f.get("busca", ""), nicho=oferta, titulo=cat, obsidian=f.get("id"))
        with mexer() as B:
            x = B["itens"][i]
            if not x.get("descricao"): x["descricao"] = f.get("descricao", "")
            if not x.get("tags"): x["tags"] = sorted({w for w in normal(cat).split() if len(w) > 2})
            if f.get("usado") == "true" and x["estado"] != "usado":
                x["estado"] = "usado"; x["usos"].append(dict(projeto=f.get("usado_em", "")[:120], versao="", quando=f.get("data_uso", "")))
        n += 1
    return n

if __name__ == "__main__":
    a = sys.argv[1:]
    def opt(k):
        if k in a: i = a.index(k); v = a[i + 1]; del a[i:i + 2]; return v
    est, nicho = opt("--estado"), opt("--nicho")
    if a[:1] == ["listar"]:
        for x in itens(est, nicho): print(f"{x['id']} {x['estado']:10s} {x.get('dur', 0):5.1f}s {x.get('nicho', '')[:18]:18s} {x.get('descricao', '')[:80]}")
    elif a[:1] == ["procurar"]:
        for x in procurar(a[1], nicho, 20): print(resumo_estudo(x))
    elif a[:1] == ["estudar"]:
        # --progresso arq.json: a aba B-rolls acompanha (feitos, total, usd, erro); custo vai para a biblioteca/custos.json
        import custos
        import threading
        prog = opt("--progresso"); refazer = "--refazer" in a
        if refazer: a.remove("--refazer")
        st = dict(rodando=True, feitos=0, total=0, usd=0.0, erro="", pid=os.getpid(), motor=motor_estudo())
        trava = threading.Lock()
        def grava():                                     # 6 clipes estudando ao mesmo tempo: um de cada vez no arquivo
            if not prog: return
            with trava:
                tmp = f"{prog}.{threading.get_ident()}.tmp"; json.dump(st, open(tmp, "w")); os.replace(tmp, prog)
        def cobra(info):
            st["usd"] += info["usd"]; grava()
            custos.registrar(raiz(), servico="gemini" if st["motor"] == "gemini" else "claude", etapa="biblioteca",
                             fonte="tokens informados pela API × preço de tabela", **info)
        def log(m):
            if "parou" in m: st["erro"] = m[:220]; grava()
        ids = a[1:] or [x["id"] for x in itens("descartado" if refazer else "disponivel", nicho) if refazer or not x.get("estudo")]
        st["total"] = len(ids); grava()
        try: feitos = estudar_pendentes(ids, ao_cobrar=cobra, log=log, refazer=refazer, progresso=lambda f, t: (st.update(feitos=f), grava()))
        except Exception as e: st["erro"] = str(e)[:220]; feitos = []
        st["rodando"] = False; grava(); print(len(feitos), "estudado(s)")
    elif a[:1] == ["importar-obsidian"]:
        print(importar_obsidian(), "ficha(s) importada(s)")
    elif a[:1] == ["revisar-descartados"]:
        print(len(revisar_descartados()), "clipe(s) voltaram para disponível")
    elif a[:1] == ["reaplicar"]:
        s, v = reaplicar(a[1:] or None)
        print(len(s), "clipe(s) descartados ·", len(v), "voltaram para disponível")
    elif a[:1] == ["migrar"]:
        print(migrar(), "clipe(s) com expert/oferta preenchidos")
    elif a[:1] == ["liberar"]:
        print("ok" if mudar_estado(a[1], "disponivel") else "não achei")
    else: print(__doc__)
