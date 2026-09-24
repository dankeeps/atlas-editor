"""Estúdio de Edição — servidor local (tela inicial + editor). Abra http://127.0.0.1:8790
Projetos ficam em ~/Edições/<projeto>/ (projeto.json, versoes/<V>/plano.json ...). Só lê arquivos das pastas permitidas."""
import os, tempfile, re, sys, json, math, wave, time, glob, shutil, signal, threading, subprocess, mimetypes, datetime, urllib.parse, urllib.request, urllib.error, hashlib, base64, importlib, secrets, contextvars, zipfile, io
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie
import cloud

APP = os.path.dirname(os.path.abspath(__file__)); SKILL = os.path.dirname(APP); LIB = os.path.join(SKILL, "lib")
sys.path.insert(0, LIB)
import comum, chaves, custos, biblioteca, gemini, youtube, kanban, workspaces, leva
from estudio import zoom_auto      # a MESMA trilha de zoom do plano (não duplicar: o editor e o render têm que bater)
PY = sys.executable; PORTA = int(os.environ.get("ESTUDIO_PORTA") or os.environ.get("PORTA", "4123"))
HOST = os.environ.get("ESTUDIO_HOST", "127.0.0.1")
VENV_PY = PY

def matar_processo(pid):
    """Encerra um processo iniciado com start_new_session=True (subprocess.Popen), incluindo filhos.
    No POSIX isso é o grupo de processos inteiro (os.killpg). No Windows start_new_session não cria
    grupo nenhum — é ignorado pelo subprocess ali —, então a única forma de matar a árvore é taskkill /T."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    else:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
def arquivos():
    # ESTUDIO_ARQUIVOS nunca sobrepõe um workspace ativo — só vale sem workspace (mesma regra de biblioteca.raiz()).
    if comum.workspace_ativo() is None and os.environ.get("ESTUDIO_ARQUIVOS"): return os.path.expanduser(os.environ["ESTUDIO_ARQUIVOS"])
    return os.path.join(comum.RAIZ, ".arquivos")
def entrada(): return os.path.join(comum.RAIZ, ".entrada")   # vídeos/roteiros recém-enviados, antes de virar projeto
def raizes(): return [comum.RAIZ, biblioteca.BROLLS, biblioteca.RAIZ, arquivos()]   # dinâmico: segue o workspace ativo da requisição

def __getattr__(nome):
    """ARQUIVOS/ENTRADA/RAIZES/CHATS/ESTUDO_ARQ viraram funções (dinâmicas por workspace); isto é só para quem
    ainda lê servidor.ARQUIVOS etc. como atributo (testes, principalmente) continuar funcionando sem precisar
    saber disso — inclusive um teste que faça patch.object(servidor, "ENTRADA", ...)."""
    alvo = dict(ARQUIVOS=arquivos, ENTRADA=entrada, RAIZES=raizes, CHATS=chats, ESTUDO_ARQ=estudo_arq).get(nome)
    if alvo: return alvo()
    raise AttributeError(f"module {__name__!r} has no attribute {nome!r}")

def env_workspace(base=None):
    """Env pra um processo-filho nosso (auto.py, estudio.py, biblioteca.py, leva.py, motor.py, render.py, chat.py...):
    sem isso o processo novo abriria sozinho, sem saber qual era o workspace ativo desta requisição, e cairia
    sempre no padrão (~/Edições) — ESTUDIO_RAIZ/ESTUDIO_BROLLS é como comum.py decide isso ao importar."""
    env = dict(base if base is not None else os.environ)
    env["ESTUDIO_RAIZ"] = comum.RAIZ; env["ESTUDIO_BROLLS"] = biblioteca.BROLLS
    return env

def resolver_workspace(handler):
    """Qual workspace vale para esta requisição: o que está marcado no cookie, se a pessoa ainda tem acesso a
    ele — senão cai pro padrão (None), que é o ~/Edições de sempre e continua aberto pra quem já loga, a não
    ser que o admin master já tenha restringido (ver workspaces.pode_acessar)."""
    email = (handler.user or {}).get("email", "")
    cookie = SimpleCookie(handler.headers.get("Cookie", ""))
    wid = cookie["atlas_workspace"].value if "atlas_workspace" in cookie else None
    if wid and wid != workspaces.PADRAO_ID and workspaces.pode_acessar(email, wid):
        try: return workspaces.obter(wid)
        except ValueError: pass
    return None

def nova_thread(alvo, args=(), kwargs=None, **outros):
    """threading.Thread, mas o alvo herda o workspace ativo desta requisição: uma thread nova NÃO herda o
    contextvar sozinha (é o motivo de tanta gente se surpreender com isso em produção) — sem este empacotamento,
    todo trabalho em segundo plano (renderizar, aplicar corte…) cairia no workspace padrão
    não importa de qual workspace a pessoa pediu."""
    ctx = contextvars.copy_context(); kwargs = kwargs or {}
    return threading.Thread(target=lambda: ctx.run(alvo, *args, **kwargs), **outros)
EXT_VIDEO = (".mp4", ".mov", ".m4v", ".webm", ".mkv"); EXT_ROTEIRO = (".docx", ".txt", ".md")
EXT_ARQUIVOS = EXT_VIDEO + EXT_ROTEIRO + (".webm", ".mkv", ".avi", ".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".srt", ".vtt")
mimetypes.add_type("video/mp4", ".mp4"); mimetypes.add_type("font/ttf", ".ttf"); mimetypes.add_type("video/quicktime", ".mov")
CORTES_LOCK = threading.RLock()
JOBS = {}          # (projeto, versão) -> status do render

def dentro(p, raiz):
    return os.path.realpath(p).startswith(os.path.realpath(raiz).rstrip(os.sep) + os.sep)

def permitido(p):
    if not isinstance(p, str) or not p or "\x00" in p: return False
    p = os.path.realpath(p)
    privados = [chaves.ARQ, os.environ.get("ESTUDIO_USERS_FILE", "")]
    if any(x and p == os.path.realpath(x) for x in privados): return False
    ext = os.path.splitext(p)[1].lower()
    if ext in EXT_ARQUIVOS and any(dentro(p, r) for r in raizes()): return True
    if ext in (".ttf", ".ttc", ".otf", ".woff", ".woff2") and dentro(p, os.path.join(SKILL, "fontes")): return True
    if ext in (".mp3", ".wav", ".m4a", ".ogg") and dentro(p, os.path.join(SKILL, "sfx")): return True
    return os.path.basename(p) == "amostra.jpg" and dentro(p, os.path.join(SKILL, "estilos"))

def pasta_proj(slug):
    if not isinstance(slug, str) or not slug or slug.startswith(".") or any(c in slug for c in ("/", "\\", "\x00")): raise ValueError("projeto inválido")
    p = os.path.realpath(os.path.join(comum.RAIZ, slug))
    if not dentro(p, comum.RAIZ) or not os.path.exists(os.path.join(p, "projeto.json")): raise ValueError("projeto inválido")
    return p

def pasta_versao(slug, v):
    if not isinstance(v, str) or not re.fullmatch(r"[\w-]{1,80}", v): raise ValueError("versão inválida")
    raiz = os.path.join(pasta_proj(slug), "versoes"); p = os.path.realpath(os.path.join(raiz, v))
    if not dentro(p, raiz) or not os.path.exists(os.path.join(p, "plano.json")): raise ValueError("versão inválida")
    return p

def limpar(P):
    if P.get("estilo", "ultradinamico") not in [e["id"] for e in estilos()]: raise ValueError("template inválido")
    E = comum.estilo(P.get("estilo", "ultradinamico")); n = P.get("_prox_uid", 1)
    for b in P["blocos"]:
        b["linhas"] = [[l[0], str(l[1]), round(float(l[2]), 3), (l[3] or None)] for l in b["linhas"] if str(l[1]).strip()]
        b["fim"] = round(float(b["fim"]), 3)
        if not b.get("uid"): b["uid"] = f"b{n}"; n += 1
    P["blocos"] = sorted([b for b in P["blocos"] if b["linhas"]], key=lambda b: min(l[2] for l in b["linhas"]))
    for c in P["cenas"]:
        c["ini"], c["fim"] = round(float(c["ini"]), 3), round(float(c["fim"]), 3)
        for k in [k for k in c if k.startswith("_")]: c.pop(k)
        if not c.get("uid"): c["uid"] = f"c{n}"; n += 1
    P["cenas"] = sorted([c for c in P["cenas"] if c["fim"] - c["ini"] > 0.05], key=lambda c: c["ini"])
    P["legendas"].sort(key=lambda l: l["ini"]); P["_prox_uid"] = n
    P.setdefault("sfx_edicoes", {}); P.setdefault("sfx_extras", [])
    P["zoom"] = zoom_auto(P, E)
    return P

def salvar(pasta, P):
    os.makedirs(os.path.join(pasta, "historico"), exist_ok=True)
    shutil.copy(os.path.join(pasta, "plano.json"), os.path.join(pasta, "historico", f"plano_{time.strftime('%Y%m%d-%H%M%S')}.json"))
    P = limpar(P); json.dump(P, open(os.path.join(pasta, "plano.json"), "w"), ensure_ascii=False, indent=1)
    # mede de novo o que depende da pessoa no quadro: topo da cabeça (dividida) e a janela até a cintura (canto)
    if any(c["tipo"] in ("dividida", "canto") for c in P["cenas"]) and os.path.isdir(os.path.join(pasta, "masks")):
        subprocess.run([PY, os.path.join(LIB, "motor.py"), pasta, "cabecas"], capture_output=True, env=env_workspace())
        P = json.load(open(os.path.join(pasta, "plano.json")))
    return P

def eventos_sfx(P):
    return comum.regras_sfx(P.get("estilo", "ultradinamico")).eventos(P)

def acervo(proj):
    a = []
    for arq in ("acervo/acervo.json", "acervo/uploads.json"):
        p = os.path.join(proj, arq)
        if os.path.exists(p): a += json.load(open(p))
    return a

def estado_auto(pasta):
    p = os.path.join(pasta, "auto", "estado.json")
    if not os.path.exists(p): return None
    try: s = json.load(open(p))
    except ValueError: return None
    if s.get("rodando") and not vivo(s.get("pid")): s.update(rodando=False, status="erro", mensagem="o processo parou sem avisar (o Mac dormiu ou reiniciou?)")
    s["log"] = s.get("log", [])[-80:]; return s

def vivo(pid):
    try: os.kill(int(pid), 0); return True
    except (TypeError, ValueError, ProcessLookupError, PermissionError): return False

def auto_rodando():
    for p in glob.glob(os.path.join(comum.RAIZ, "*", "auto", "estado.json")):
        s = estado_auto(os.path.dirname(os.path.dirname(p)))
        if s and s.get("rodando"): return os.path.basename(os.path.dirname(os.path.dirname(p)))
    return None

def nome_estilo_exibicao(nome):
    """Nome do estilo só para mostrar na lista; projetos antigos podem apontar para um estilo já removido
    (ex.: os 3 que viraram Ultradinâmico DR) — não pode derrubar a aba Vídeos inteira por causa disso."""
    try: return comum.estilo(nome)["nome"]
    except (OSError, ValueError): return f"{nome} (removido)"

def info_projetos():
    out = []; com_proj = {os.path.realpath(d["pasta"]) for d in comum.projetos()}
    for p in sorted(glob.glob(os.path.join(comum.RAIZ, "*", "auto", "pedido.json"))):   # projetos ainda na transcrição
        pasta = os.path.dirname(os.path.dirname(p))
        if os.path.realpath(pasta) not in com_proj:
            ped = json.load(open(p))
            out.append(dict(slug=os.path.basename(pasta), nome=ped["nome"], estilo=nome_estilo_exibicao(ped.get("estilo", "ultradinamico")),
                            expert=ped.get("expert", ""), oferta=ped.get("oferta", ""), sigla=ped.get("sigla", ""), versoes=[],
                            criado=time.strftime("%Y-%m-%d"), auto=estado_auto(pasta), ordem=os.path.getmtime(p)))
    for d in comum.projetos():
        slug = os.path.basename(d["pasta"]); vs = []
        for v in d["versoes"]:
            pv = os.path.join(d["pasta"], "versoes", v["id"])
            if not os.path.exists(os.path.join(pv, "plano.json")): continue
            P = json.load(open(os.path.join(pv, "plano.json")))
            rs = sorted([f for f in os.listdir(os.path.join(pv, "renders"))] if os.path.isdir(os.path.join(pv, "renders")) else [],
                        key=lambda f: os.path.getmtime(os.path.join(pv, "renders", f)), reverse=True)
            vs.append(dict(id=v["id"], nome=v["nome"], dur=P["dur"], capa=os.path.join(pv, "capa.jpg") if os.path.exists(os.path.join(pv, "capa.jpg")) else None,
                           plano_mod=os.path.getmtime(os.path.join(pv, "plano.json")),
                           renders=[dict(nome=f, arquivo=os.path.join(pv, "renders", f), data=os.path.getmtime(os.path.join(pv, "renders", f)),
                                         mb=round(os.path.getsize(os.path.join(pv, "renders", f)) / 1e6)) for f in rs if f.endswith(".mp4")],
                           cenas=len(P["cenas"]), letreiros=len(P["blocos"]), job=JOBS.get((slug, v["id"]))))
        au = estado_auto(d["pasta"]); ts = [os.path.getmtime(os.path.join(d["pasta"], "projeto.json"))] + [x["plano_mod"] for x in vs]
        if au: ts.append(au.get("inicio") or 0)
        out.append(dict(slug=slug, nome=d["nome"], estilo=nome_estilo_exibicao(d.get("estilo", "ultradinamico")), expert=d.get("expert", ""),
                        oferta=d.get("oferta", ""), sigla=d.get("sigla", ""), nicho=d.get("nicho", ""), versoes=vs, criado=d.get("criado", ""),
                        auto=au, ordem=max(ts)))
    return sorted(out, key=lambda x: -x["ordem"])

def todos_custos():
    """Aba Custo: um bloco por projeto (vídeo) com os itens de custos.json e o total geral."""
    out = []; tot = dict(claude=0.0, gemini=0.0, apify=0.0, total=0.0)
    for p in info_projetos():
        pasta = os.path.join(comum.RAIZ, p["slug"]); C = custos.ler(pasta); r = custos.resumo(pasta)
        for k in tot: tot[k] += r[k]
        out.append(dict(slug=p["slug"], nome=p["nome"], criado=p.get("criado", ""), sigla=p.get("sigla", ""),
                        versoes=[dict(nome=v["nome"], dur=v["dur"]) for v in p["versoes"]], resumo=r,
                        itens=sorted(C["itens"], key=lambda x: x.get("quando", "")), notas=C.get("notas", []),
                        editando=bool(p.get("auto") and p["auto"].get("rodando"))))
    lv = levas(); itl = []
    for x in lv:
        dl = os.path.join(biblioteca.BROLLS, x["id"])
        for it in custos.ler(dl)["itens"]: itl.append(dict(it, rotulo=f"{' · '.join(x['anuncios'])[:60]} — {it.get('rotulo', '')}"))
    if itl:
        r = dict(claude=round(sum(i["usd"] for i in itl if i["servico"] == "claude"), 4), gemini=round(sum(i["usd"] for i in itl if i["servico"] == "gemini"), 4),
                 apify=round(sum(i["usd"] for i in itl if i["servico"] == "apify"), 4)); r["total"] = round(r["claude"] + r["gemini"] + r["apify"], 4)
        for k in ("claude", "gemini", "apify", "total"): tot[k] += r[k]
        out.append(dict(slug="__busca__", nome="Buscas de B-roll (levas)", chat=True, criado="", sigla="", versoes=[], resumo=r,
                        itens=sorted(itl, key=lambda i: i.get("quando", "")), notas=[], editando=any(x["estado"].get("rodando") for x in lv)))
    its = custos_chats()
    if its:
        r = dict(claude=round(sum(i["usd"] for i in its), 4), gemini=0.0, apify=0.0); r["total"] = r["claude"]
        tot["claude"] += r["claude"]; tot["total"] += r["total"]
        out.append(dict(slug="__chat__", nome="Conversas no chat", chat=True, criado="", sigla="", versoes=[], resumo=r,
                        itens=its, notas=[], editando=False))
    return dict(projetos=out, total={k: round(v, 4) for k, v in tot.items()},
                precos={k: dict(entrada=v[0], saida=v[1]) for k, v in custos.PRECOS.items()},
                precos_gemini={k: dict(entrada=v[0], saida=v[1]) for k, v in gemini.PRECOS.items()})

def zip_skill_criar_template():
    """Empacota a skill criar-template + o CLI que ela chama (scripts/criar_template.py), pronta pra soltar
    dentro de OUTRO clone deste repositório (.claude/skills/criar-template/SKILL.md e scripts/criar_template.py,
    nesses mesmos caminhos). O resto de que a skill precisa (lib/template_escrita.py, estilos/, fontes/) já
    vem com qualquer clone do GitHub — não tem por que empacotar de novo."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(SKILL, ".claude", "skills", "criar-template", "SKILL.md"),
                ".claude/skills/criar-template/SKILL.md")
        z.write(os.path.join(SKILL, "scripts", "criar_template.py"), "scripts/criar_template.py")
    return buf.getvalue()

def estilos(completo=False, incluir_rascunhos=False):
    """Modelos de edição. completo=True (aba Templates): sequência da edição, regras em frases, exemplo aprovado, guia e uso.
    incluir_rascunhos=True (só a aba Templates, pra dar pra achar e publicar um rascunho): também lista
    templates criados pela skill criar-template e ainda não publicados — eles ficam de fora de tudo que é
    uso real (Kanban, Novo Vídeo, que nunca passam incluir_rascunhos) até alguém publicar."""
    import regras
    out = []; usos = {}
    if completo:
        for d in comum.projetos(): usos[d.get("estilo", "ultradinamico")] = usos.get(d.get("estilo", "ultradinamico"), 0) + 1
    for p in sorted(glob.glob(os.path.join(SKILL, "estilos", "*", "estilo.json"))):
        E = json.load(open(p)); nome = os.path.basename(os.path.dirname(p)); pasta = os.path.dirname(p)
        publicado = E.get("publicado", True)
        if not publicado and not incluir_rascunhos: continue
        x = dict(id=nome, nome=E.get("nome", nome), descricao=E.get("descricao", ""), regras=bool(E.get("regras")),
                 publicado=publicado, origem=E.get("origem", "manual"))
        if completo:
            ex = os.path.join(pasta, "exemplo.md"); leia = os.path.join(pasta, "LEIA.md"); bu = os.path.join(pasta, "BUSCA.md")
            x.update(processo=E.get("processo", []), modo=E.get("modo", "plano"), regras_texto=regras.texto(E.get("regras")), usos=usos.get(nome, 0),
                     usa=E.get("usa", {}), letreiros=list(E.get("letreiro", {}).get("estilos", {})), amostra=bool(amostra_letreiro(nome)),
                     exemplo=(open(ex, encoding="utf-8").read().split("\n", 1)[0] if os.path.exists(ex) else ""),
                     guia=open(leia, encoding="utf-8").read() if os.path.exists(leia) else "",
                     busca=(open(bu, encoding="utf-8").read() if os.path.exists(bu) else ""))
        out.append(x)
    ordem = {"ultradinamico-criativo": 0, "ultradinamico-dr": 1, "ultradinamico": 2}
    return sorted(out, key=lambda x: ordem.get(x["id"], 9))

EX_LETREIRO = {"sans": ("o que ninguém te conta", None), "azul": ("SEM ACADEMIA", None),
               "num": ("+8", None), "branca": ("perigoso", None), "lista": ("Bruce Lee", None),
               "check": ("treino curto", "✅")}

def amostra_letreiro(eid):
    """Print dos letreiros do estilo, desenhado pelo PRÓPRIO motor — o que a aba mostra é o que sai no vídeo.
    Fica em cache ao lado do estilo.json e só é refeito quando o estilo muda."""
    if not re.fullmatch(r"[a-z0-9_-]+", eid or ""): raise ValueError("template inválido")
    pasta = os.path.join(SKILL, "estilos", eid)
    est, cache = os.path.join(pasta, "estilo.json"), os.path.join(pasta, "amostra.jpg")
    if not os.path.exists(est): return None
    if os.path.exists(cache) and os.path.getmtime(cache) >= os.path.getmtime(est): return cache
    E = json.load(open(est, encoding="utf-8"))
    if not (E.get("usa") or {}).get("letreiro", True): return None
    pares = [[k, *EX_LETREIRO.get(k, ("exemplo", None))] for k in E.get("letreiro", {}).get("estilos", {})]
    if not pares: return None
    with tempfile.TemporaryDirectory() as d:
        json.dump(dict(src="", masks="", whisper="", estilo=eid, dur=1, cortes=[], cenas=[], blocos=[],
                       legendas=[], zoom=[], glitch=[], flash=[]), open(os.path.join(d, "plano.json"), "w"))
        r = subprocess.run([PY, os.path.join(LIB, "motor.py"), d, "amostras", json.dumps(pares, ensure_ascii=False), cache],
                           capture_output=True, text=True, env=env_workspace())
    return cache if os.path.exists(cache) else None

def sugestao():
    """Expert/oferta/sigla/nicho do projeto mais recente, para já vir preenchido."""
    ps = sorted(comum.projetos(), key=lambda d: os.path.getmtime(os.path.join(d["pasta"], "projeto.json")), reverse=True)
    nichos = sorted({x.get("nicho") for x in biblioteca.itens() if x.get("nicho")} | {d.get("nicho") for d in ps if d.get("nicho")})
    u = ps[0] if ps else {}
    return dict(ultima=dict(expert=u.get("expert", ""), oferta=u.get("oferta", ""), sigla=u.get("sigla", ""), nicho=u.get("nicho", "")), nichos=nichos)

def testar_apify():
    k = custos.token_apify()
    if not k: return dict(ok=False, msg="nenhum token salvo")
    try:
        req = urllib.request.Request("https://api.apify.com/v2/users/me", headers={"Authorization": f"Bearer {k}"})
        d = json.loads(urllib.request.urlopen(req, timeout=15).read())["data"]
        return dict(ok=True, msg=f"token válido · conta {d.get('username', '?')}")
    except urllib.error.HTTPError as e:
        return dict(ok=False, msg="token inválido" if e.code in (401, 403) else f"a Apify respondeu {e.code}")
    except Exception:
        return dict(ok=False, msg="sem conexão com a Apify")

def testar_openai():
    k = chaves.ler().get("openai")
    if not k: return dict(ok=False, msg="nenhuma chave salva")
    try:
        req = urllib.request.Request("https://api.openai.com/v1/models", headers={"Authorization": "Bearer " + k})
        json.loads(urllib.request.urlopen(req, timeout=15).read())
        return dict(ok=True, msg="chave válida")
    except urllib.error.HTTPError as e:
        return dict(ok=False, msg="chave inválida" if e.code in (401, 403) else f"a OpenAI respondeu {e.code}")
    except Exception:
        return dict(ok=False, msg="sem conexão com a OpenAI")

def testar_heygen():
    k = chaves.ler().get("heygen")
    if not k: return dict(ok=False, msg="nenhuma chave salva")
    try:
        req = urllib.request.Request("https://api.heygen.com/v2/user/remaining_quota", headers={"x-api-key": k})
        json.loads(urllib.request.urlopen(req, timeout=15).read())
        return dict(ok=True, msg="chave válida")
    except urllib.error.HTTPError as e:
        return dict(ok=False, msg="chave inválida" if e.code in (401, 403) else f"o HeyGen respondeu {e.code}")
    except Exception:
        return dict(ok=False, msg="sem conexão com o HeyGen")

def testar_claude():
    if not os.path.exists(VENV_PY): return dict(ok=False, msg="falta instalar o SDK (.venv da skill)")
    try: r = subprocess.run([VENV_PY, os.path.join(LIB, "ia.py"), "testar"], capture_output=True, text=True, timeout=40)
    except subprocess.TimeoutExpired: return dict(ok=False, msg="a API demorou demais para responder")
    try: return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError): return dict(ok=False, msg=(r.stderr or "falhou")[-200:])

def slug_nome(nome):
    slug = re.sub(r"[/\\:\x00]", "-", nome).strip().strip(".")
    if not slug or slug.startswith("."): raise ValueError("nome do projeto inválido")
    return slug

def receber_arquivo(handler, token, tipo, nome):
    """Grava o corpo da requisição direto no disco, em pedaços (o vídeo bruto pode ter vários GB)."""
    if not re.fullmatch(r"[a-z0-9]{8,40}", token): raise ValueError("envio inválido")
    ext = os.path.splitext(nome)[1].lower()
    if tipo == "video" and ext not in EXT_VIDEO: raise ValueError("o vídeo precisa ser .mp4, .mov ou .m4v")
    if tipo == "roteiro" and ext not in EXT_ROTEIRO: raise ValueError("o roteiro precisa ser .docx, .txt ou .md")
    if tipo not in ("video", "roteiro"): raise ValueError("tipo inválido")
    pasta = os.path.join(entrada(), token); os.makedirs(pasta, exist_ok=True)
    dest = os.path.join(pasta, tipo + ext)
    cloud.receive(handler, dest)
    for velho in glob.glob(os.path.join(pasta, tipo + ".*")):
        if velho != dest and not velho.endswith(".part"): os.remove(velho)
    return dest

def iniciar_auto(d):
    token = d.get("id", ""); nome = str(d.get("nome", "")).strip()
    if not re.fullmatch(r"[a-z0-9]{8,40}", token): raise ValueError("envio inválido")
    if not nome: raise ValueError("dê um nome ao projeto")
    if not chaves.ler()["anthropic"]: raise ValueError("configure a chave da API do Claude primeiro (⚙)")
    if auto_rodando(): raise ValueError(f"já tem uma edição automática rodando ({auto_rodando()}); espere ela terminar")
    if d.get("estilo") not in [e["id"] for e in estilos()]: raise ValueError("modelo de edição inválido")
    ent = os.path.join(entrada(), token); video = (glob.glob(os.path.join(ent, "video.*")) or [None])[0]
    if not video: raise ValueError("o vídeo não chegou")
    pasta = os.path.join(comum.RAIZ, slug_nome(nome))
    if os.path.exists(pasta): raise ValueError(f"já existe um projeto chamado '{nome}'")
    os.makedirs(os.path.join(pasta, "fonte")); os.makedirs(os.path.join(pasta, "auto"))
    v_dest = os.path.join(pasta, "fonte", "original" + os.path.splitext(video)[1].lower()); shutil.move(video, v_dest)
    rot = (glob.glob(os.path.join(ent, "roteiro.*")) or [None])[0]; r_dest = None
    if rot: r_dest = os.path.join(pasta, "fonte", "roteiro_original" + os.path.splitext(rot)[1].lower()); shutil.move(rot, r_dest)
    shutil.rmtree(ent, ignore_errors=True)
    sig = re.sub(r"[^A-Z0-9]", "", str(d.get("sigla", "")).upper())[:6]
    ped = dict(nome=nome, estilo=d["estilo"], video=v_dest, roteiro=r_dest, expert=str(d.get("expert", "")).strip(),
               oferta=str(d.get("oferta", "")).strip(), sigla=sig,
               nicho=biblioteca.nome_oferta(d.get("oferta")) if d.get("oferta") else str(d.get("nicho", "")).strip(),
               buscar_tiktok=bool(d.get("buscar_tiktok", True)), buscar_youtube=bool(d.get("buscar_youtube", True)),
               renderizar=bool(d.get("renderizar", True)), conferir=bool(d.get("conferir", False)), anuncio=str(d.get("anuncio") or ""),
               pedido=time.strftime("%Y-%m-%d %H:%M"))
    json.dump(ped, open(os.path.join(pasta, "auto", "pedido.json"), "w"), ensure_ascii=False, indent=1)
    lancar_auto(pasta); return os.path.basename(pasta)

def lancar_auto(pasta, ate=None):
    log = open(os.path.join(pasta, "auto", "saida.log"), "a")
    script = os.environ.get("ESTUDIO_AUTO") or os.path.join(LIB, "auto.py")          # ESTUDIO_AUTO: testes (Claude simulado)
    cmd = [VENV_PY, script, pasta] + (["--ate", ate] if ate else [])
    p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, cwd=pasta, env=env_workspace())
    for _ in range(50):                                 # espera o processo novo se anunciar no estado, para a tela já mostrar
        s = estado_auto(pasta)
        if s and s.get("pid") == p.pid and s.get("rodando"): break
        time.sleep(0.1)
    return p.pid

def cancelar_auto(pasta):
    s = estado_auto(pasta)
    if not s or not s.get("rodando"): raise ValueError("não tem edição automática rodando neste projeto")
    try: matar_processo(int(s["pid"]))
    except (ProcessLookupError, PermissionError): pass

def retomar_auto(pasta):
    s = estado_auto(pasta)
    if s and s.get("rodando"): raise ValueError("já está rodando")
    if auto_rodando(): raise ValueError("já tem outra edição automática rodando")
    if not chaves.ler()["anthropic"]: raise ValueError("configure a chave da API do Claude primeiro (⚙)")
    lancar_auto(pasta)

def pasta_auto(slug):
    p = os.path.realpath(os.path.join(comum.RAIZ, slug))
    if not p.startswith(os.path.realpath(comum.RAIZ) + "/") or not os.path.exists(os.path.join(p, "auto", "pedido.json")): raise ValueError("projeto inválido")
    return p

def publicar_template(estilo_id):
    """Libera um rascunho (criado pela skill criar-template) para Kanban/Novo Vídeo. Sem trava de teste
    prévio — quem decide se o template está pronto é a skill/a pessoa que revisou o estilo.json, não um
    render automático (o Laboratório que fazia isso saiu: exigia vídeo e B-rolls preparados à parte, fora de
    qualquer fluxo normal, e não valia o atrito)."""
    p = os.path.join(SKILL, "estilos", str(estilo_id or ""), "estilo.json")
    if not os.path.exists(p): raise ValueError("template não encontrado")
    E = json.load(open(p))
    if E.get("publicado", True): raise ValueError("este template já está publicado")
    E["publicado"] = True
    tmp = p + ".tmp"; json.dump(E, open(tmp, "w"), ensure_ascii=False, indent=1); os.replace(tmp, p)

# ---------------------------------------------------------------- chat (lib/chat.py: o Claude Code pela API, um processo por conversa)
def chats(): return os.path.join(comum.RAIZ, ".chats")
LANCA = threading.Lock()
EXT_MENCAO = EXT_VIDEO + EXT_ROTEIRO + (".pdf", ".png", ".jpg", ".jpeg", ".webp")

def pasta_chat(cid):
    if not re.fullmatch(r"[a-f0-9]{12}", cid or ""): raise ValueError("conversa inválida")
    p = os.path.join(chats(), cid)
    if not os.path.exists(os.path.join(p, "meta.json")): raise ValueError("conversa não encontrada")
    return p

def ler_meta(p):
    try: return json.load(open(os.path.join(p, "meta.json")))
    except (OSError, ValueError): return {}

def grava_meta(p, **kw):
    d = ler_meta(p); d.update(kw); tmp = os.path.join(p, "meta.json.srv"); json.dump(d, open(tmp, "w"), ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(p, "meta.json"))

def estado_chat(p):
    try: s = json.load(open(os.path.join(p, "estado.json")))
    except (OSError, ValueError): s = {}
    s["vivo"] = bool(s.get("pid")) and vivo(s["pid"])
    if not s["vivo"]: s.update(ocupado=False, aguardando=0)
    return s

def lista_chats():
    out = []
    for m in glob.glob(os.path.join(chats(), "*", "meta.json")):
        d = ler_meta(os.path.dirname(m)); e = estado_chat(os.path.dirname(m))
        if not d.get("id"): continue
        out.append(dict(id=d["id"], titulo=d.get("titulo") or "Nova conversa", criado=d.get("criado"), atualizado=d.get("atualizado") or d.get("criado"),
                        custo=d.get("custo", 0), ocupado=e.get("ocupado"), aguardando=e.get("aguardando", 0), modo=d.get("modo", "auto")))
    return sorted(out, key=lambda x: -(x["atualizado"] or 0))

def novo_chat():
    cid = hashlib.sha1(f"{time.time_ns()}-{os.getpid()}".encode()).hexdigest()[:12]
    p = os.path.join(chats(), cid); os.makedirs(os.path.join(p, "entrada"))
    json.dump(dict(id=cid, criado=time.time(), atualizado=time.time(), modo="auto", custo=0, modelo=chaves.ler().get("modelo")),
              open(os.path.join(p, "meta.json"), "w"), ensure_ascii=False, indent=1)
    return cid

def comando_chat(p, **c):
    """O processo da conversa lê <pasta>/entrada/*.json a cada 0,25 s (grava .tmp e renomeia: ele nunca lê pela metade)."""
    d = os.path.join(p, "entrada"); os.makedirs(d, exist_ok=True); n = str(time.time_ns())
    json.dump(c, open(os.path.join(d, n + ".tmp"), "w"), ensure_ascii=False); os.replace(os.path.join(d, n + ".tmp"), os.path.join(d, n + ".json"))

def lancar_chat(p):
    with LANCA:
        if estado_chat(p)["vivo"]: return
        env = env_workspace({k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC")) and k not in ("AI_AGENT", "BAGGAGE")})
        pr = subprocess.Popen([VENV_PY, os.path.join(LIB, "chat.py"), p], stdout=open(os.path.join(p, "saida.log"), "a"), stderr=subprocess.STDOUT,
                              start_new_session=True, cwd=os.path.expanduser("~"), env=env)
        json.dump(dict(pid=pr.pid, ocupado=True, aguardando=0, vivo_em=time.time(), inicio_turno=time.time()), open(os.path.join(p, "estado.json"), "w"))

def enviar_chat(p, d):
    if not os.path.exists(VENV_PY): raise ValueError("falta instalar o SDK (.venv da skill)")
    if not chaves.ler()["anthropic"]: raise ValueError("configure a chave da API do Claude no ⚙ da tela inicial")
    texto = str(d.get("texto") or "").strip(); anexos = [a for a in d.get("anexos") or [] if isinstance(a, str) and permitido(a) and os.path.isfile(a)]
    if not texto and not anexos: raise ValueError("mensagem vazia")
    if not ler_meta(p).get("titulo"): grava_meta(p, titulo=(texto.splitlines() or [os.path.basename(anexos[0])])[0][:70])
    comando_chat(p, tipo="msg", texto=texto, anexos=anexos); lancar_chat(p)

def eventos_chat(p, pos):
    arq = os.path.join(p, "eventos.jsonl"); evs = []
    if os.path.exists(arq):
        with open(arq, "rb") as f: f.seek(pos); dados = f.read(32 << 20)
        corte = dados.rfind(b"\n") + 1; pos += corte                          # evento pela metade fica para a próxima leitura
        for l in dados[:corte].decode("utf-8", "replace").splitlines():
            try: evs.append(json.loads(l))
            except ValueError: pass
    return dict(eventos=evs, pos=pos, estado=estado_chat(p), meta=ler_meta(p))

def modo_chat(p, modo):
    if modo not in ("auto", "default", "acceptEdits", "bypassPermissions", "plan"): raise ValueError("modo inválido")
    if estado_chat(p)["vivo"]: comando_chat(p, tipo="modo", modo=modo)
    else: grava_meta(p, modo=modo)

def apagar_chat(p):
    s = estado_chat(p)
    if s["vivo"]:
        try: matar_processo(int(s["pid"]))
        except (ProcessLookupError, PermissionError): pass
    shutil.rmtree(p, ignore_errors=True)

def receber_anexo(handler, p, nome):
    nome = re.sub(r"[/\\:\x00]", "-", os.path.basename(nome or "arquivo")).strip() or "arquivo"
    d = os.path.join(p, "anexos"); os.makedirs(d, exist_ok=True); base, ext = os.path.splitext(nome); dest = os.path.join(d, nome); i = 2
    while os.path.exists(dest): dest = os.path.join(d, f"{base} ({i}){ext}"); i += 1
    if ext.lower() not in EXT_ARQUIVOS: raise ValueError("formato de anexo não aceito")
    cloud.receive(handler, dest)
    return dest

def mencoes():
    """O que dá para citar com @ no chat: projetos do Estúdio, renders e arquivos recentes de Downloads/Mesa."""
    out, rs = [], []
    for d in sorted(comum.projetos(), key=lambda d: os.path.getmtime(os.path.join(d["pasta"], "projeto.json")), reverse=True):
        out.append(dict(nome=d["nome"], caminho=d["pasta"], grupo="Projetos"))
        for f in glob.glob(os.path.join(d["pasta"], "versoes", "*", "renders", "*.mp4")): rs.append((os.path.getmtime(f), f))
    out += [dict(nome=os.path.basename(f), caminho=f, grupo="Renders") for _, f in sorted(rs, reverse=True)[:20]]
    out += [dict(nome=f["nome"], caminho=f["path"], grupo="Arquivos") for f in listar_arquivos()[:100]]
    return out

def custos_chats():
    its = []
    for c in lista_chats():
        m = ler_meta(os.path.join(chats(), c["id"]))
        if not m.get("custo"): continue
        its.append(dict(servico="claude", etapa="chat", rotulo=c["titulo"], usd=round(m["custo"], 6), quando=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(c["atualizado"] or 0)),
                        entrada=m.get("entrada", 0), saida=m.get("saida", 0), cache_leitura=m.get("cache_leitura", 0), cache_escrita=m.get("cache_escrita", 0),
                        modelo=m.get("modelo") or chaves.ler().get("modelo", "")))
    return its

# ---------------------------------------------------------------- aba Busca de B-roll (lib/leva.py)
def levas():
    """As buscas por leva de todas as ofertas, mais novas primeiro, com o estado de cada uma. Inclui as
    buscas agendadas no YouTube (modo="youtube_agendada", sem "anuncios") — antes da hora chegar, elas ainda
    não têm estado.json nenhum, e aparecem com status "agendada" em vez de cair no fallback de "rodando"."""
    out = []
    for e in glob.glob(os.path.join(biblioteca.BROLLS, "*", "*", ".levas", "*", "pedido.json")):
        d = os.path.dirname(e); ped = json.load(open(e))
        arq_estado = os.path.join(d, "estado.json")
        if os.path.exists(arq_estado):
            try: st = json.load(open(arq_estado))
            except (OSError, ValueError): st = dict(status="rodando", etapas=[], log=[])
            if st.get("rodando") and not vivo(st.get("pid")): st.update(rodando=False, status="erro", mensagem=st.get("mensagem") or "o processo parou no meio (dá para retomar)")
        elif ped.get("modo") == "youtube_agendada":
            # O horário some daqui de propósito: agendado_em é um timestamp (sem fuso), e quem formata pra
            # tela é o navegador — a VPS roda em UTC, então um texto pronto aqui mostraria 3h a mais do que a
            # pessoa digitou (Brasília). O cliente já recebe agendado_em/duracao_horas soltos, logo abaixo.
            st = dict(rodando=False, status="agendada", etapas=[], log=[], mensagem="")
        else:
            st = dict(status="rodando", etapas=[], log=[])
        st["log"] = st.get("log", [])[-60:]
        out.append(dict(id=os.path.relpath(d, biblioteca.BROLLS), expert=ped["expert"], oferta=ped["oferta"],
                        anuncios=[a["nome"] for a in ped.get("anuncios") or []], agendada=ped.get("modo") == "youtube_agendada",
                        agendado_em=ped.get("agendado_em"), duracao_horas=ped.get("duracao_horas"),
                        template=ped.get("template"), fontes=ped.get("fontes") or ["tiktok"],
                        criado=ped.get("criado", ""), estado=st, custo=custos.resumo(d)))
    return sorted(out, key=lambda x: x["criado"], reverse=True)

def pasta_leva(rel):
    p = os.path.realpath(os.path.join(biblioteca.BROLLS, rel or ""))
    if not p.startswith(os.path.realpath(biblioteca.BROLLS) + "/") or "/.levas/" not in p or not os.path.exists(os.path.join(p, "pedido.json")): raise ValueError("busca inválida")
    return p

def receber_anuncio(handler, token, n, nome):
    if not re.fullmatch(r"[a-z0-9]{8,40}", token) or not re.fullmatch(r"\d{1,2}", n or ""): raise ValueError("envio inválido")
    ext = os.path.splitext(nome)[1].lower()
    if ext not in EXT_VIDEO: raise ValueError("o anúncio precisa ser .mp4, .mov ou .m4v")
    pasta = os.path.join(entrada(), token); os.makedirs(pasta, exist_ok=True)
    dest = os.path.join(pasta, f"anuncio_{int(n):02d}{ext}")
    cloud.receive(handler, dest)
    for velho in glob.glob(os.path.join(pasta, f"anuncio_{int(n):02d}.*")):
        if velho != dest and not velho.endswith(".part"): os.remove(velho)
    return dest

def nome_pasta(n): return re.sub(r"[/:\\]", "-", str(n or "")).strip().strip(".")

def iniciar_leva(d):
    token = d.get("id", ""); expert, oferta = nome_pasta(d.get("expert")), nome_pasta(d.get("oferta"))
    if not re.fullmatch(r"[a-z0-9]{8,40}", token): raise ValueError("envio inválido")
    if not expert or not oferta: raise ValueError("escolha o expert e a oferta")
    if not chaves.ler()["anthropic"]: raise ValueError("configure a chave da API do Claude primeiro (⚙)")
    fontes = [f for f in (d.get("fontes") or ["tiktok"]) if f in ("tiktok", "youtube")]
    if not fontes: raise ValueError("escolha onde buscar: TikTok, YouTube ou os dois")
    if "tiktok" in fontes and not custos.token_apify(): raise ValueError("configure o token da Apify primeiro (⚙) ou busque só no YouTube")
    if "youtube" in fontes and not gemini.chave(): raise ValueError("o YouTube usa o Gemini para escolher os trechos: configure a chave (⚙)")
    if "youtube" in fontes and not youtube.disponivel(): raise ValueError("o YouTube precisa do yt-dlp e do ffmpeg instalados")
    ent = os.path.join(entrada(), token); ans = []
    for a in d.get("anuncios") or []:
        arq = (glob.glob(os.path.join(ent, f"anuncio_{int(a.get('n', 0)):02d}.*")) or [None])[0]
        if arq: ans.append((str(a.get("nome") or "").strip() or f"Anúncio {int(a['n']):02d}", arq))
    if not ans: raise ValueError("suba pelo menos um anúncio")
    if len({n for n, _ in ans}) < len(ans): raise ValueError("dois anúncios com o mesmo nome")
    dl = biblioteca.pasta_oferta(expert, oferta, ".levas", time.strftime("%Y%m%d-%H%M%S")); os.makedirs(os.path.join(dl, "anuncios"))
    anuncios = []
    for k, (nome, arq) in enumerate(ans, 1):
        dest = os.path.join(dl, "anuncios", f"{k:02d}-{nome_pasta(nome)}{os.path.splitext(arq)[1]}"); shutil.move(arq, dest)
        anuncios.append(dict(nome=nome, arquivo=dest))
    shutil.rmtree(ent, ignore_errors=True)
    tpl = d.get("template") if d.get("template") in [e["id"] for e in estilos()] else "ultradinamico-criativo"
    json.dump(dict(expert=expert, oferta=oferta, anuncios=anuncios, template=tpl, fontes=fontes,
                   criado=time.strftime("%Y-%m-%d %H:%M:%S")), open(os.path.join(dl, "pedido.json"), "w"), ensure_ascii=False, indent=1)
    lancar_leva(dl); return os.path.relpath(dl, biblioteca.BROLLS)

def agendar_busca_youtube(d):
    """Cria o pedido de uma busca agendada no YouTube (sem anúncio — o Claude planeja os termos pelas
    categorias que a oferta já tem) e devolve sem lançar nada: quem lança, na hora certa, é
    leva.laco_agendadas(), rodando numa thread do servidor desde o início do processo."""
    expert, oferta = nome_pasta(d.get("expert")), nome_pasta(d.get("oferta"))
    if not expert or not oferta: raise ValueError("escolha o expert e a oferta")
    if not chaves.ler()["anthropic"]: raise ValueError("configure a chave da API do Claude primeiro (⚙)")
    if not gemini.chave(): raise ValueError("o YouTube usa o Gemini para escolher os trechos: configure a chave (⚙)")
    if not youtube.disponivel(): raise ValueError("o YouTube precisa do yt-dlp e do ffmpeg instalados")
    try: agendado_em = float(d.get("agendado_em"))
    except (TypeError, ValueError): raise ValueError("escolha quando a busca deve começar")
    if agendado_em < time.time() - 60: raise ValueError("escolha um horário no futuro")
    try: duracao_horas = float(d.get("duracao_horas"))
    except (TypeError, ValueError): raise ValueError("escolha por quantas horas a busca deve rodar")
    if not (0.5 <= duracao_horas <= 24): raise ValueError("a duração precisa ser entre 30 minutos e 24 horas")
    # sufixo aleatório: o nome só com data-hora (segundo a segundo) colidiria num duplo-clique — um clique só,
    # sem upload de anúncio no meio pra segurar o ritmo, torna isso bem mais fácil de acontecer aqui do que na
    # leva normal (iniciar_leva). pasta_oferta() já cria o diretório (exist_ok=True) — nada de os.makedirs aqui.
    dl = biblioteca.pasta_oferta(expert, oferta, ".levas", f"{time.strftime('%Y%m%d-%H%M%S')}-agendada-{secrets.token_hex(3)}")
    json.dump(dict(expert=expert, oferta=oferta, anuncios=[], fontes=["youtube"], modo="youtube_agendada",
                   agendado_em=agendado_em, duracao_horas=duracao_horas, criado=time.strftime("%Y-%m-%d %H:%M:%S")),
              open(os.path.join(dl, "pedido.json"), "w"), ensure_ascii=False, indent=1)
    return os.path.relpath(dl, biblioteca.BROLLS)

def cancelar_agendada(id):
    """Só cancela o que ainda não começou (sem estado.json) — depois que leva.laco_agendadas() lançou, é
    /api/busca/cancelar (mata o processo) que serve, igual a qualquer outra leva."""
    dl = pasta_leva(id)
    ped = json.load(open(os.path.join(dl, "pedido.json")))
    if ped.get("modo") != "youtube_agendada": raise ValueError("essa busca não é agendada")
    if os.path.exists(os.path.join(dl, "estado.json")): raise ValueError("essa busca já começou — cancele pela busca em si")
    shutil.rmtree(dl)

def picos_audio_versao(vd, n_por_s=20):
    """Onda do áudio já na timeline editada (faixa Anúncio do editor, mesmo eixo de tempo do plano). Cache por versão."""
    import numpy as np, wave
    alvo = os.path.join(vd, "picos.json"); wav = os.path.join(vd, "voz.wav")
    if os.path.exists(alvo) and os.path.getmtime(alvo) >= os.path.getmtime(wav): return json.load(open(alvo))
    w = wave.open(wav); ch, sr = w.getnchannels(), w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    if ch > 1: x = x.reshape(-1, ch).mean(axis=1)
    hop = max(1, sr // n_por_s); x = x[:len(x) // hop * hop].reshape(-1, hop)
    pk = np.abs(x).max(1) if len(x) else np.array([0.0])
    pk = (pk / (pk.max() or 1)) ** 0.6                     # raiz: silêncio fica visível
    d = dict(dur=round(len(pk) / n_por_s, 3), n=n_por_s, picos=[round(float(v), 3) for v in pk])
    json.dump(d, open(alvo, "w")); return d

QUADROS_LOCK = threading.Lock()
QUADROS_GERANDO = set()   # pastas de versão com o filmstrip sendo gerado agora (evita rodar em dobro)

def quadros_prontos(vd):
    """Só olha o cache do filmstrip; nunca chama ffmpeg (isso pode ser lento demais para segurar uma resposta).
    None se ainda não tem (ou está desatualizado) — quem chamar decide se dispara gerar_quadros em background."""
    marca = os.path.join(vd, "quadros", ".pronto"); jc = os.path.join(vd, "jc.mov")
    if os.path.exists(marca) and os.path.exists(jc) and os.path.getmtime(marca) >= os.path.getmtime(jc):
        return json.load(open(marca))
    return None

def gerar_quadros(vd, dur, n=60):
    """Roda numa thread à parte (chamado a partir de /api/estado quando o cache está frio): a tela do editor
    não pode ficar 'carregando' presa atrás disso num vídeo longo. Cada quadro é um seek+grab independente
    (rápido, igual às outras miniaturas do Estúdio) rodando em paralelo, bem mais rápido que decodificar o
    vídeo inteiro numa passada só."""
    with QUADROS_LOCK:
        if vd in QUADROS_GERANDO: return
        QUADROS_GERANDO.add(vd)
    try:
        pasta_q = os.path.join(vd, "quadros"); jc = os.path.join(vd, "jc.mov")
        os.makedirs(pasta_q, exist_ok=True)
        for arq in glob.glob(os.path.join(pasta_q, "*.jpg")): os.remove(arq)
        passo = max(dur / n, 1 / 30)
        tempos = [round(i * passo, 3) for i in range(n) if i * passo < dur]
        def um_quadro(item):
            i, t = item; arq = os.path.join(pasta_q, f"q{i:04d}.jpg")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", jc, "-frames:v", "1",
                            "-vf", "scale=120:-2", "-q:v", "5", arq], check=True)
            return dict(t=t, arquivo=arq)
        with ThreadPoolExecutor(max_workers=6) as ex:
            lista = list(ex.map(um_quadro, enumerate(tempos)))
        json.dump(lista, open(os.path.join(pasta_q, ".pronto"), "w"))
    except Exception as e:
        print(f"filmstrip do editor falhou em {vd}: {e}", file=sys.stderr)
    finally:
        with QUADROS_LOCK: QUADROS_GERANDO.discard(vd)

def uso_disco():
    """Quanto espaço cada parte do Estúdio ocupa, para a aba Arquivos avisar antes de lotar a VPS. Cache de 5 min
    (percorrer tudo pode ser lento com muito projeto acumulado)."""
    cache = os.path.join(comum.RAIZ, ".cache_uso_disco.json")
    if os.path.exists(cache) and time.time() - os.path.getmtime(cache) < 300:
        try: return json.load(open(cache))
        except (OSError, ValueError): pass
    def tamanho(pasta):
        total = 0
        for raiz, _, arqs in os.walk(pasta):
            for a in arqs:
                try: total += os.path.getsize(os.path.join(raiz, a))
                except OSError: pass
        return total
    projetos_tam = lab_tam = outros_tam = 0; n_projetos = 0
    if os.path.isdir(comum.RAIZ):
        for nome in os.listdir(comum.RAIZ):
            p = os.path.join(comum.RAIZ, nome)
            if nome == ".laboratorio": lab_tam = tamanho(p)
            elif nome.startswith("."): outros_tam += tamanho(p) if os.path.isdir(p) else os.path.getsize(p)
            elif os.path.isdir(p): projetos_tam += tamanho(p); n_projetos += 1
            else: outros_tam += os.path.getsize(p)
    biblioteca_tam = tamanho(biblioteca.BROLLS) if os.path.isdir(biblioteca.BROLLS) else 0
    total = projetos_tam + lab_tam + outros_tam + biblioteca_tam
    livre = total_disco = None
    try:
        du = shutil.disk_usage(comum.RAIZ); livre, total_disco = du.free, du.total
    except OSError: pass
    d = dict(total=total, livre=livre, disco_total=total_disco, quando=time.strftime("%Y-%m-%d %H:%M"), itens=[
        dict(nome=f"Anúncios ({n_projetos})", bytes=projetos_tam, cor="#0a84ff"),
        dict(nome="Biblioteca de B-roll", bytes=biblioteca_tam, cor="#30d158"),
        dict(nome="Laboratório", bytes=lab_tam, cor="#ff9f0a"),
        dict(nome="Outros", bytes=outros_tam, cor="#8e8e93"),
    ])
    tmp = cache + ".tmp"; json.dump(d, open(tmp, "w")); os.replace(tmp, cache)
    return d

def visao_geral():
    """Resumo do workspace ativo agora, para a aba Visão Geral: quantos anúncios estão em produção, quantos
    B-rolls a busca trouxe essa semana, quantos anúncios já foram entregues."""
    Q = kanban.ler()
    em_producao = sum(1 for c in Q["cards"].values() if c["coluna"] != "pronto")
    editados = sum(1 for c in Q["cards"].values() if c["coluna"] == "pronto")
    limite = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    B = biblioteca.ler()["itens"]
    brolls_semana = sum(1 for it in B.values() if (it.get("baixado") or "") >= limite)
    return dict(em_producao=em_producao, editados=editados, brolls_semana=brolls_semana)

def stats_sistema():
    """CPU e memória da máquina agora, para a aba Processamento notar se algo travou consumindo tudo. Sem
    psutil (não é dependência do projeto): lê /proc no Linux (VPS); fora do Linux (Mac, no dev local), usa a
    carga média como aproximação — não é tão preciso, mas dá uma ideia."""
    nucleos = os.cpu_count() or 1
    cpu_pct = None
    if os.path.exists("/proc/stat"):
        try:
            def amostra():
                campos = [int(x) for x in open("/proc/stat").readline().split()[1:8]]
                return sum(campos), campos[3] + campos[4]  # total, ocioso (idle + iowait)
            t1, o1 = amostra(); time.sleep(0.15); t2, o2 = amostra()
            dt = t2 - t1
            if dt > 0: cpu_pct = round(100 * (1 - (o2 - o1) / dt), 1)
        except (OSError, ValueError, IndexError): pass
    if cpu_pct is None and hasattr(os, "getloadavg"):
        try: cpu_pct = round(min(100.0, os.getloadavg()[0] / nucleos * 100), 1)
        except OSError: pass
    mem_total = mem_usado = None
    if os.path.exists("/proc/meminfo"):
        try:
            info = {}
            for linha in open("/proc/meminfo"):
                k, v = linha.split(":", 1); info[k.strip()] = int(v.strip().split()[0]) * 1024
            mem_total = info.get("MemTotal")
            if mem_total: mem_usado = mem_total - info.get("MemAvailable", mem_total)
        except (OSError, ValueError): pass
    elif shutil.which("sysctl") and shutil.which("vm_stat"):
        try:
            mem_total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=2).stdout.strip())
            pagesize = 4096
            paginas = {}
            for linha in subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=2).stdout.splitlines():
                if ":" not in linha: continue
                k, v = linha.split(":", 1); v = v.strip().rstrip(".")
                if v.isdigit(): paginas[k.strip()] = int(v)
            livre = (paginas.get("Pages free", 0) + paginas.get("Pages inactive", 0)) * pagesize
            mem_usado = mem_total - livre
        except (OSError, ValueError, subprocess.SubprocessError): pass
    return dict(cpu_pct=cpu_pct, nucleos=nucleos, mem_total=mem_total, mem_usado=mem_usado,
                carga=list(os.getloadavg()) if hasattr(os, "getloadavg") else None)

def _ts(s):
    try: return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").timestamp()
    except (ValueError, TypeError): return None

def processamento():
    """O que está rodando agora, em qualquer sistema (Kanban, Editor: edição automática, render/recorte manual) — para achar
    uma demanda em loop infinito ou travada. Cada item mostra há quanto tempo está rodando: bem mais que o normal
    é sinal de bug. Sem hora de início registrada (não devia acontecer, mas é sinal de bug por si só) o tempo
    fica None — a tela mostra "desconhecido" e alerta, nunca finge que acabou de começar."""
    agora_ts = time.time(); itens = []
    def segundos_de(iso_ou_epoch, epoch=False):
        t0 = (iso_ou_epoch if epoch else _ts(iso_ou_epoch))
        return round(agora_ts - t0) if t0 else None
    for c in kanban.quadro()["cards"]:
        if c["estado"] != "rodando": continue
        itens.append(dict(sistema="Kanban", tipo=c["coluna"], nome=c["nome"], segundos=segundos_de(c.get("atualizado"))))
    for chave, j in list(JOBS.items()):
        if not j.get("rodando"): continue
        nome = " · ".join(str(x) for x in chave) if isinstance(chave, (tuple, list)) else str(chave)
        itens.append(dict(sistema="Editor", tipo=j.get("etapa") or "processando", nome=nome, segundos=segundos_de(j.get("inicio"), epoch=True)))
    for p in glob.glob(os.path.join(comum.RAIZ, "*", "auto", "estado.json")):     # edição automática: processo à parte, não mora em JOBS
        pasta = os.path.dirname(os.path.dirname(p)); s = estado_auto(pasta)
        if not s or not s.get("rodando"): continue
        etapa = next((e["nome"] for e in s.get("etapas", []) if e["estado"] == "rodando"), "processando")
        try: nome = json.load(open(os.path.join(pasta, "auto", "pedido.json"))).get("nome") or os.path.basename(pasta)
        except (OSError, ValueError): nome = os.path.basename(pasta)
        itens.append(dict(sistema="Editor", tipo=etapa, nome=nome, segundos=segundos_de(s.get("inicio"), epoch=True)))
    itens.sort(key=lambda x: x["segundos"] if x["segundos"] is not None else float("inf"), reverse=True)
    return dict(itens=itens, sistema=stats_sistema(), quando=time.strftime("%Y-%m-%d %H:%M:%S"))

def picos_audio(proj, n_por_s=20):
    """Onda do áudio da fonte, reduzida (para desenhar a timeline da revisão de cortes). Fica em cache."""
    import numpy as np, wave
    alvo = os.path.join(proj, "fonte", "picos.json")
    wav = os.path.join(proj, "fonte", "voz16k.wav")
    if os.path.exists(alvo) and os.path.getmtime(alvo) >= os.path.getmtime(wav): return json.load(open(alvo))
    w = wave.open(wav); x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    hop = max(1, 16000 // n_por_s); x = x[:len(x) // hop * hop].reshape(-1, hop)
    pk = np.abs(x).max(1); pk = (pk / (pk.max() or 1)) ** 0.6                     # raiz: silêncio fica visível
    d = dict(dur=round(len(pk) / n_por_s, 3), n=n_por_s, picos=[round(float(v), 3) for v in pk])
    json.dump(d, open(alvo, "w")); return d

def proxy_fonte(proj):
    """Cópia leve do vídeo ORIGINAL (com som) para a revisão de cortes."""
    alvo = os.path.join(proj, "fonte", "prev.mp4")
    if os.path.exists(alvo) and os.path.getsize(alvo): return alvo
    P = json.load(open(os.path.join(proj, "projeto.json")))
    src = P.get("fonte_video") or os.path.join(proj, "fonte", "original.mp4")
    subprocess.run(["nice", "-n", "10", "ffmpeg", "-v", "error", "-y", "-i", src, "-vf", "scale=-2:640,fps=30",
                    "-c:v", "libx264", "-crf", "30", "-preset", "veryfast", "-c:a", "aac", "-b:a", "96k",
                    "-movflags", "+faststart", alvo], check=True)
    return alvo

def dados_cortes(slug, v):
    """Tudo que a tela de revisão precisa: onda, trechos que ficam (segs) e o vídeo original leve."""
    d = pasta_proj(slug); vd = os.path.join(d, "versoes", v)
    prop = json.load(open(os.path.join(d, "fonte", "cortes.json")))
    ver = next((x for x in prop["versoes"] if x["id"] == v), None)
    if not ver: raise ValueError("versão inválida")
    segs = ver.get("segs")
    if not segs and os.path.exists(os.path.join(vd, "mapa.json")):
        segs = [[a, b] for a, b, _ in json.load(open(os.path.join(vd, "mapa.json")))["mapa"]]
    pic = picos_audio(d)
    jc = os.path.join(vd, "jc.mov")
    return dict(projeto=slug, versao=v, nome=json.load(open(os.path.join(d, "projeto.json")))["nome"],
                dur=pic["dur"], picos=pic["picos"], n=pic["n"], segs=[[round(float(a), 3), round(float(b), 3)] for a, b in (segs or [[0, pic["dur"]]])],
                faixas=ver.get("faixas") or [[0, pic["dur"]]], proxy=proxy_fonte(d), jc=jc if os.path.exists(jc) else None,
                revisao=hash_cortes(os.path.join(d, "fonte", "cortes.json")), job=JOBS.get(("cortes", slug, v)))

def hash_cortes(arq):
    with open(arq, "rb") as f: return hashlib.sha256(f.read()).hexdigest()


def segmentos_revisados(valor, dur):
    if not isinstance(valor, list) or not valor or len(valor) > 10000:
        raise ValueError("envie os trechos que ficam no vídeo")
    segs = []; anterior = 0.0
    for trecho in valor:
        if not isinstance(trecho, (list, tuple)) or len(trecho) != 2:
            raise ValueError("trecho de corte inválido")
        if any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) for t in trecho):
            raise ValueError("os tempos dos cortes devem ser números finitos")
        a, b = map(float, trecho)
        if a < 0 or b > dur + .001 or b - a < .05 or a < anterior:
            raise ValueError("os trechos devem estar em ordem, sem sobreposição e dentro do vídeo")
        a, b = round(a, 3), round(min(b, dur), 3)
        if b <= a: raise ValueError("trecho de corte curto demais")
        segs.append([a, b]); anterior = b
    return segs


def cortes_ocupados(slug):
    return any(j.get("rodando") and (k[0] == slug or (len(k) > 2 and k[0] == "cortes" and k[1] == slug))
               for k, j in JOBS.items())


def salvar_cortes(d):
    """Reserva o trabalho antes de gravar e só conclui a revisão depois de gerar os arquivos."""
    if not isinstance(d, dict): raise ValueError("pedido de cortes inválido")
    slug, v, cid = d.get("p"), d.get("v"), d.get("card")
    proj = pasta_proj(slug)
    if not isinstance(v, str) or not re.fullmatch(r"[\w-]{1,80}", v): raise ValueError("versão inválida")
    if cid is not None and not isinstance(cid, str): raise ValueError("card inválido")
    if d.get("revisado"): raise ValueError("confirme a revisão depois que o corte for aplicado")
    chave = ("cortes", slug, v); arq = os.path.join(proj, "fonte", "cortes.json")
    with CORTES_LOCK, kanban.mexer() as Q:
        if cortes_ocupados(slug) or (estado_auto(proj) or {}).get("rodando"):
            raise ValueError("o projeto ainda está sendo processado; aguarde")
        relacionados = [c for c in Q["cards"].values() if c.get("projeto") == slug]
        if any(c.get("estado") in ("rodando", "espera") for c in relacionados):
            raise ValueError("o anúncio está em processamento ou na fila")
        card = Q["cards"].get(cid) if cid else None
        if cid and (not card or card.get("projeto") != slug or card.get("coluna") != "cortes"):
            raise ValueError("o card não corresponde à revisão de cortes deste projeto")
        if relacionados and not cid: raise ValueError("abra esta revisão pelo card do Kanban")
        if d.get("revisao") != hash_cortes(arq):
            raise ValueError("os cortes mudaram; recarregue a página antes de salvar")
        with open(arq) as f: prop = json.load(f)
        versoes = [x for x in prop.get("versoes", []) if x.get("id") == v]
        if len(versoes) != 1: raise ValueError("versão inválida")
        with wave.open(os.path.join(proj, "fonte", "voz16k.wav"), "rb") as w:
            dur = w.getnframes() / w.getframerate()
        segs = segmentos_revisados(d.get("segs"), dur)
        versoes[0]["segs"] = segs
        tmp = arq + "." + secrets.token_hex(8) + ".tmp"
        try:
            with open(tmp, "x", encoding="utf-8") as f:
                json.dump(prop, f, ensure_ascii=False, indent=1, allow_nan=False)
            os.replace(tmp, arq)
        finally:
            if os.path.exists(tmp): os.remove(tmp)
        revisao = hash_cortes(arq)
        JOBS[chave] = dict(rodando=True, etapa="refazendo o corte", pct=0, erro=None, revisao=revisao, card=cid)
        if card: card.update(estado="rodando", revisado=False, msg="Aplicando a revisão dos cortes", atualizado=kanban.agora())

    def finalizar(erro=None):
        with CORTES_LOCK, kanban.mexer() as Q:
            JOBS[chave] = dict(rodando=False, etapa="erro" if erro else "pronto", pct=0 if erro else 100,
                               erro=erro, revisao=revisao, card=cid)
            card = Q["cards"].get(cid) if cid else None
            if card and card.get("projeto") == slug and card.get("coluna") == "cortes" and card.get("estado") == "rodando":
                card.update(estado="erro" if erro else "ok", revisado=False, msg=erro or "", atualizado=kanban.agora())

    def trabalho():
        try:
            subprocess.run(["nice", "-n", "10", PY, os.path.join(LIB, "estudio.py"), "cortes", slug,
                            "--aplicar", "--versoes", v, "--manter-copia"],
                           check=True, capture_output=True, text=True)
            if hash_cortes(arq) != revisao: raise ValueError("os cortes foram alterados durante o processamento; confira antes de revisar")
            finalizar()
        except Exception as e:
            if isinstance(e, subprocess.CalledProcessError):
                erro = ((e.stderr or "") + "\n" + (e.stdout or "")).strip()
                erro = ((e.stderr or "").strip() or erro)[-1000:] or "não foi possível aplicar o corte"
            else: erro = str(e) or "não foi possível aplicar o corte"
            finalizar(erro)
    try:
        nova_thread(trabalho, daemon=True).start()
    except Exception as e:
        finalizar(str(e)); raise
    return dict(ok=True, revisao=revisao)


def revisar_card(d):
    """O selo de revisão dos cortes requer a mesma revisão e processamento concluído."""
    if not isinstance(d, dict) or not isinstance(d.get("id"), str): raise ValueError("card inválido")
    valor = d.get("valor", True)
    if not isinstance(valor, bool): raise ValueError("revisão inválida")
    with CORTES_LOCK, kanban.mexer() as Q:
        card = Q["cards"].get(d["id"])
        if not card: raise ValueError("card não encontrado")
        if valor and card.get("coluna") == "cortes":
            slug = card.get("projeto"); proj = pasta_proj(slug); v = d.get("v") or "A"
            if d.get("p") != slug or not isinstance(v, str) or not re.fullmatch(r"[\w-]{1,80}", v):
                raise ValueError("projeto ou versão da revisão inválidos")
            arq = os.path.join(proj, "fonte", "cortes.json")
            revisao = hash_cortes(arq)
            if d.get("revisao") != revisao: raise ValueError("os cortes mudaram; recarregue e confira a revisão atual")
            if card.get("estado") != "ok" or cortes_ocupados(slug) or (estado_auto(proj) or {}).get("rodando"):
                raise ValueError("espere o corte terminar antes de marcar como revisado")
            job = JOBS.get(("cortes", slug, v))
            if job and (job.get("erro") or job.get("revisao") != revisao):
                raise ValueError("o corte atual ainda não foi aplicado com sucesso")
            with open(arq) as f: prop = json.load(f)
            if not any(x.get("id") == v for x in prop.get("versoes", [])): raise ValueError("versão inválida")
            modificado = os.stat(arq).st_mtime_ns
            for nome in ("jc.mov", "voz.wav", "voz16k.wav", "mapa.json", "whisper.json"):
                path = os.path.join(proj, "versoes", v, nome)
                if not os.path.isfile(path) or os.path.getsize(path) == 0 or os.stat(path).st_mtime_ns < modificado:
                    raise ValueError("os arquivos desta revisão ainda não estão prontos")
        card.update(revisado=valor, atualizado=kanban.agora())
    return dict(ok=True)


def lista_anuncios():
    """Aba Arquivos › Anúncios: os vídeos brutos que você subiu, com o que cada um já gerou."""
    Q = kanban.ler(); por_video = {}
    for c in Q["cards"].values(): por_video.setdefault(os.path.realpath(c.get("video") or ""), []).append(c)
    out = []
    for arq in sorted(glob.glob(os.path.join(kanban.RAIZ, "lotes", "*", "*"))):
        if not arq.lower().endswith(EXT_VIDEO): continue
        cs = por_video.get(os.path.realpath(arq), [])
        proj = next((c.get("projeto") for c in cs if c.get("projeto")), None)
        pasta = os.path.join(comum.RAIZ, proj) if proj else None
        out.append(dict(arquivo=arq, nome=os.path.splitext(os.path.basename(arq))[0], mb=round(os.path.getsize(arq) / 1e6),
                        quando=os.path.getmtime(arq), cards=[c["id"] for c in cs], coluna=(cs[0]["coluna"] if cs else None),
                        lote=(cs[0]["lote"] if cs else None), projeto=proj,
                        editado=bool(pasta and os.path.isdir(pasta)),
                        gerado_mb=(round(tamanho_pasta(pasta) / 1e6) if pasta and os.path.isdir(pasta) else 0)))
    return sorted(out, key=lambda x: -x["quando"])

def tamanho_pasta(d):
    t = 0
    for r, _, fs in os.walk(d):
        for f in fs:
            try: t += os.path.getsize(os.path.join(r, f))
            except OSError: pass
    return t

def ficha_arquivo(path, origem="Enviado"):
    path = os.path.realpath(path); st = os.stat(path)
    return dict(id=hashlib.sha256(path.encode()).hexdigest()[:24], nome=os.path.basename(path), path=path,
                arquivo=path, tamanho=st.st_size, mb=round(st.st_size / 1e6, 2),
                tipo=mimetypes.guess_type(path)[0] or "application/octet-stream", criado_em=st.st_mtime,
                quando=st.st_mtime, origem=origem, url="/f?" + urllib.parse.urlencode(dict(p=path)),
                pode_apagar=dentro(path, arquivos()) or dentro(path, entrada()))

def listar_arquivos():
    """Arquivos originais enviados pelo navegador, sem máscaras ou proxies derivados."""
    patterns = [(os.path.join(arquivos(), "*", "*"), "Enviado"),
                (os.path.join(entrada(), "*", "*"), "Aguardando projeto"),
                (os.path.join(kanban.RAIZ, "lotes", "*", "*"), "Anúncio"),
                (os.path.join(chats(), "*", "anexos", "*"), "Chat"),
                (os.path.join(comum.RAIZ, "*", "uploads", "*"), "Editor"),
                (os.path.join(comum.RAIZ, "*", "assets", "*"), "Projeto"),
                (os.path.join(comum.RAIZ, "*", "fonte", "original.*"), "Anúncio"),
                (os.path.join(comum.RAIZ, "*", "fonte", "roteiro_original.*"), "Roteiro"),
                (os.path.join(biblioteca.BROLLS, "*", "*", ".levas", "*", "anuncios", "*"), "Busca de B-roll")]
    seen, result = set(), []
    for pattern, origem in patterns:
        for path in glob.iglob(pattern):
            path = os.path.realpath(path)
            if path in seen or not permitido(path) or not os.path.isfile(path): continue
            seen.add(path)
            try: result.append(ficha_arquivo(path, origem))
            except OSError: continue
    return sorted(result, key=lambda f: -f["criado_em"])

def receber_generico(handler, nome):
    nome = re.sub(r"[/\\:\x00]", "-", os.path.basename(nome or "arquivo")).strip().strip(".")[:180]
    if not nome or os.path.splitext(nome)[1].lower() not in EXT_ARQUIVOS: raise ValueError("formato de arquivo não aceito")
    pasta = os.path.join(arquivos(), secrets.token_hex(12)); os.makedirs(pasta, exist_ok=True)
    dest = os.path.join(pasta, nome)
    try: cloud.receive(handler, dest)
    except Exception:
        shutil.rmtree(pasta, ignore_errors=True)
        raise
    return ficha_arquivo(dest)

def usar_arquivo(d):
    source = os.path.realpath(str(d.get("arquivo") or ""))
    if not permitido(source) or not os.path.isfile(source) or not any(f["path"] == source for f in listar_arquivos()):
        raise ValueError("arquivo não encontrado")
    token, destino = str(d.get("id") or ""), d.get("destino")
    if not re.fullmatch(r"[a-z0-9]{8,40}", token): raise ValueError("envio inválido")
    ext = os.path.splitext(source)[1].lower()
    if destino == "novo":
        tipo = d.get("tipo", "video")
        if tipo not in ("video", "roteiro") or ext not in (EXT_VIDEO if tipo == "video" else EXT_ROTEIRO): raise ValueError("formato de arquivo inválido")
        base = tipo
    elif destino in ("kanban", "busca"):
        n = str(d.get("n", ""))
        if ext not in EXT_VIDEO or not re.fullmatch(r"\d{1,2}", n): raise ValueError("anúncio inválido")
        base = f"anuncio_{int(n):02d}"
    else: raise ValueError("destino inválido")
    pasta = os.path.join(entrada(), token); os.makedirs(pasta, exist_ok=True)
    dest = os.path.join(pasta, base + ext)
    if os.path.realpath(dest) != source:
        tmp = dest + "." + secrets.token_hex(8) + ".part"
        try:
            shutil.copyfile(source, tmp); os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp): os.remove(tmp)
    for previous in glob.glob(os.path.join(pasta, base + ".*")):
        if previous != dest and not previous.endswith(".part"): os.remove(previous)
    return dict(ok=True, arquivo=dest)

def apagar_arquivo(d):
    """Aba Arquivos. broll: tira do índice E do disco. anuncio: apaga só o que a edição gerou (o vídeo bruto e os
    B-rolls ficam); com `tudo`, apaga também o vídeo bruto e o card do quadro."""
    tipo = d.get("tipo")
    if tipo == "arquivo":
        path = os.path.realpath(str(d.get("arquivo") or ""))
        item = next((f for f in listar_arquivos() if f["path"] == path), None)
        if not item: raise ValueError("arquivo não encontrado")
        if not item["pode_apagar"]: raise ValueError("Esse arquivo pertence a um projeto. Use as opções de limpeza do anúncio.")
        os.remove(path)
        try: os.rmdir(os.path.dirname(path))
        except OSError: pass
        return dict(ok=True)
    if tipo == "broll":
        ids = [str(i) for i in (d.get("ids") or []) if str(i).strip()]
        if not ids: raise ValueError("nada para apagar")
        return dict(ok=True, **biblioteca.apagar(ids))
    if tipo == "anuncio":
        arq = os.path.realpath(str(d.get("arquivo") or ""))
        if not arq.startswith(os.path.realpath(kanban.RAIZ) + "/") or not os.path.exists(arq): raise ValueError("anúncio inválido")
        A = next((a for a in lista_anuncios() if os.path.realpath(a["arquivo"]) == arq), None)
        if not A: raise ValueError("anúncio inválido")
        apagados = []
        if A["projeto"]:
            pasta = os.path.join(comum.RAIZ, A["projeto"])
            if os.path.isdir(pasta) and os.path.realpath(pasta).startswith(os.path.realpath(comum.RAIZ) + "/"):
                if estado_auto(pasta) and estado_auto(pasta).get("rodando"): raise ValueError("esse anúncio está sendo editado agora")
                shutil.rmtree(pasta); apagados.append("edição")
        if d.get("tudo"):
            kanban.apagar(A["cards"]); os.remove(arq); apagados.append("vídeo bruto")
        elif A["cards"]:
            with kanban.mexer() as Q:
                for i in A["cards"]:
                    if i in Q["cards"]: Q["cards"][i].update(projeto=None, coluna="edicao", estado="espera", revisado=False, msg="")
            apagados.append("voltou para Edição (reaproveitando o B-roll já buscado)")
        return dict(ok=True, apagados=apagados)
    raise ValueError("tipo inválido")

def criar_kanban(d):
    """Aba Kanban: uma demanda nova (um lote com vários anúncios). Os vídeos ficam em ~/Edições/.kanban/lotes/."""
    token = d.get("id", "")
    if not re.fullmatch(r"[a-z0-9]{8,40}", token): raise ValueError("envio inválido")
    expert, oferta = nome_pasta(d.get("expert")), nome_pasta(d.get("oferta"))
    if not expert or not oferta: raise ValueError("escolha o expert e a oferta")
    estilo = d.get("estilo") if d.get("estilo") in [e["id"] for e in estilos()] else "ultradinamico-criativo"
    fontes = [f for f in (d.get("fontes") or ["tiktok", "youtube"]) if f in ("tiktok", "youtube")] or ["youtube"]
    ent = os.path.join(entrada(), token); ans = []
    for a in d.get("anuncios") or []:
        arq = (glob.glob(os.path.join(ent, f"anuncio_{int(a.get('n', 0)):02d}.*")) or [None])[0]
        if arq: ans.append((str(a.get("nome") or "").strip() or f"Anúncio {int(a['n']):02d}", arq))
    if not ans: raise ValueError("suba pelo menos um anúncio")
    if len({n for n, _ in ans}) < len(ans): raise ValueError("dois anúncios com o mesmo nome")
    if any(os.path.exists(os.path.join(comum.RAIZ, nome_pasta(n))) for n, _ in ans): raise ValueError("já existe um projeto com esse nome")
    guarda = os.path.join(kanban.RAIZ, "lotes", token); os.makedirs(guarda, exist_ok=True)
    finais = []
    for nome, arq in ans:
        dest = os.path.join(guarda, f"{nome_pasta(nome)}{os.path.splitext(arq)[1].lower()}"); shutil.move(arq, dest)
        finais.append((nome, dest))
    shutil.rmtree(ent, ignore_errors=True)
    return kanban.criar_lote(str(d.get("nome") or "").strip() or f"{expert} · {oferta}", expert, oferta, estilo, finais, fontes)

def criar_pasta(d):
    """Aba B-rolls › Pastas: novo expert, nova oferta (dentro do expert) ou nova categoria (dentro da oferta)."""
    partes = [nome_pasta(d.get(k)) for k in ("expert", "oferta", "categoria") if str(d.get(k) or "").strip()]
    if not partes: raise ValueError("dê um nome à pasta")
    for n in partes:
        if not n or n.startswith(".") or n.lower() == os.path.basename(biblioteca.RAIZ).lower(): raise ValueError(f"nome de pasta inválido: {n!r}")
    base = os.path.join(biblioteca.BROLLS, *partes[:-1])
    if not os.path.isdir(base): raise ValueError("a pasta de cima não existe")
    alvo = os.path.join(base, partes[-1])
    if os.path.exists(alvo): raise ValueError(f"já existe uma pasta \"{partes[-1]}\" aqui")
    os.makedirs(alvo); return os.path.relpath(alvo, biblioteca.BROLLS)

def lancar_leva(dl):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))}
    subprocess.Popen([VENV_PY, os.path.join(LIB, "leva.py"), dl], stdout=open(os.path.join(dl, "saida.log"), "a"), stderr=subprocess.STDOUT,
                     start_new_session=True, env=env, cwd=dl)

def estudo_arq(): return os.path.join(biblioteca.RAIZ, "estudo_progresso.json")

def estado_estudo():
    try: s = json.load(open(estudo_arq()))
    except (OSError, ValueError): s = dict(rodando=False, feitos=0, total=0, usd=0.0, erro="")
    if s.get("rodando") and not vivo(s.get("pid")): s.update(rodando=False, erro=s.get("erro") or "o estudo parou no meio (dá para continuar)")
    return s

def estudar_biblioteca(nicho=None, expert=None, oferta=None, categoria=None, refazer=False):
    """Estuda, uma vez, os disponíveis sem ficha (Claude pela folha de contato, ou Gemini — ⚙). Processo separado na .venv."""
    if estado_estudo().get("rodando"): raise ValueError("já está estudando")
    motor = biblioteca.motor_estudo()
    if motor == "gemini":
        if not gemini.chave(): raise ValueError("configure a chave do Gemini primeiro (⚙) ou escolha o Claude para estudar")
        ok, motivo = gemini.funciona()
        if not ok: raise ValueError(motivo)
    elif not chaves.ler()["anthropic"]: raise ValueError("configure a chave da API do Claude primeiro (⚙)")
    if not os.path.exists(VENV_PY): raise ValueError("falta instalar o SDK (.venv da skill)")
    if refazer: ids = [x["id"] for x in biblioteca.itens("descartado", nicho, None, expert, oferta, categoria)]
    else: ids = [x["id"] for x in biblioteca.itens("disponivel", nicho, None, expert, oferta, categoria) if not x.get("estudo")]
    if not ids: raise ValueError("nada para estudar")
    env = env_workspace({k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))})
    json.dump(dict(rodando=True, feitos=0, total=len(ids), usd=0.0, erro="", motor=motor), open(estudo_arq(), "w"))
    pr = subprocess.Popen([VENV_PY, os.path.join(LIB, "biblioteca.py"), "estudar", "--progresso", estudo_arq()] + (["--refazer"] if refazer else []) + ids,
                          stdout=open(os.path.join(biblioteca.RAIZ, "estudo.log"), "a"), stderr=subprocess.STDOUT, start_new_session=True, env=env)
    d = json.load(open(estudo_arq())); d["pid"] = pr.pid; json.dump(d, open(estudo_arq(), "w"))

def lista_biblioteca(q):
    """Aba B-rolls: os clipes de uma pasta (expert, oferta, categoria), por estado, com busca por texto/tag/etiqueta."""
    ex, of, cat = q.get("expert") or None, q.get("oferta") or None, q.get("categoria") or None
    escopo = biblioteca.itens(None, None, None, ex, of, cat)
    estado = q.get("estado") or "disponivel"
    L = biblioteca.itens(None if estado == "todos" else estado, None, q.get("q") or None, ex, of, cat)
    if q.get("anuncio"): L = [x for x in L if q["anuncio"] in (x.get("anuncios") or [])]
    dias = q.get("dias")
    if dias:                                                  # "-30" = usado nos últimos 30 dias · "30" = usado há mais de 30
        try: n = int(dias)
        except ValueError: n = 0
        if n:
            corte = (datetime.date.today() - datetime.timedelta(days=abs(n))).isoformat()
            def ultimo(x):
                ds = [u.get("quando") or "" for u in (x.get("usos") or []) if u.get("quando")]
                return max(ds) if ds else ""
            L = [x for x in L if (ultimo(x) >= corte) if n < 0] if n < 0 else [x for x in L if ultimo(x) and ultimo(x) < corte]
    etiquetas = sorted({a for x in escopo for a in (x.get("anuncios") or [])})
    campos = ("id", "estado", "dur", "w", "h", "descricao", "tags", "nicho", "url", "autor", "capa", "arquivo", "usos", "nota", "fonte", "baixado",
              "titulo", "descarte", "categoria", "anuncios", "expert", "oferta")
    return dict(itens=[dict({k: x.get(k) for k in campos}, estudado=bool(x.get("estudo"))) for x in L[:600]],
                contagem=dict(disponivel=sum(1 for x in escopo if x["estado"] == "disponivel"), usado=sum(1 for x in escopo if x["estado"] == "usado"),
                              descartado=sum(1 for x in escopo if x["estado"] == "descartado"),
                              sem_estudo=sum(1 for x in escopo if x["estado"] == "disponivel" and not x.get("estudo"))),
                estudo=estado_estudo(), gemini=bool(gemini.chave()), motor=biblioteca.motor_estudo(), etiquetas=etiquetas)

def renderizar(slug, v):
    pasta = pasta_versao(slug, v); st = JOBS[(slug, v)]
    try:
        p = subprocess.Popen([PY, os.path.join(LIB, "render.py"), pasta], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for linha in p.stdout:
            linha = linha.strip()
            if linha.startswith("progresso"): st["pct"] = int(linha.split()[1])
            elif linha.startswith("etapa"): st["etapa"] = linha[6:]
            elif linha.startswith("saida"): st["saida"] = linha[6:]
            elif linha.startswith("tempo"): st["tempo"] = linha[6:]
        err = p.stderr.read(); p.wait()
        if p.returncode: raise RuntimeError(err[-800:])
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "3", "-i", st["saida"], "-frames:v", "1", "-vf", "scale=360:-2",
                        os.path.join(pasta, "capa.jpg")])
        st.update(etapa="pronto", pct=100)
    except Exception as e:
        st.update(erro=str(e), etapa="erro")
    finally:
        st["rodando"] = False

def novo_upload(proj, nome, handler):
    os.makedirs(os.path.join(proj, "uploads"), exist_ok=True); os.makedirs(os.path.join(proj, "acervo", "proxy"), exist_ok=True)
    os.makedirs(os.path.join(proj, "acervo", "thumbs"), exist_ok=True)
    base, ext = os.path.splitext(re.sub(r"[^\w.\- ]", "_", nome)); dest = os.path.join(proj, "uploads", base + ext); k = 1
    while os.path.exists(dest): dest = os.path.join(proj, "uploads", f"{base}-{k}{ext}"); k += 1
    if ext.lower() not in EXT_VIDEO + (".png", ".jpg", ".jpeg", ".webp"): raise ValueError("formato de mídia não aceito")
    cloud.receive(handler, dest)
    return registrar_upload(proj, dest)

def registrar_upload(proj, dest, categoria="Enviados do computador", descricao="enviado pelo editor"):
    os.makedirs(os.path.join(proj, "acervo", "proxy"), exist_ok=True); os.makedirs(os.path.join(proj, "acervo", "thumbs"), exist_ok=True)
    ext = os.path.splitext(dest)[1]
    h = hashlib.md5(dest.encode()).hexdigest()[:12]; thumb = os.path.join(proj, "acervo", "thumbs", h + ".jpg")
    ups = json.load(open(os.path.join(proj, "acervo", "uploads.json"))) if os.path.exists(os.path.join(proj, "acervo", "uploads.json")) else []
    it = dict(id=f"UP{len(ups) + 1:03d}", categoria=categoria, nome=os.path.basename(dest), descricao=descricao, src=dest)
    if ext.lower() in (".jpg", ".jpeg", ".png"):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", dest, "-vf", "scale=240:-2", thumb]); it.update(proxy=dest, thumb=thumb, dur=0, w=0, h=0)
    else:
        pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                                        "-of", "json", dest], capture_output=True, text=True).stdout)
        dur = float(pr["format"].get("duration", 0) or 0); w, hh = pr["streams"][0]["width"], pr["streams"][0]["height"]
        proxy = os.path.join(proj, "acervo", "proxy", h + ".mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", dest, "-an", "-vf", "scale='min(540,iw)':-2", "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "27", "-g", "15", "-pix_fmt", "yuv420p", "-movflags", "+faststart", proxy])
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{min(1.0, dur / 3):.2f}", "-i", dest, "-frames:v", "1", "-vf", "scale=240:-2", thumb])
        it.update(proxy=proxy, thumb=thumb, dur=round(dur, 2), w=w, h=hh)
    ups.append(it); json.dump(ups, open(os.path.join(proj, "acervo", "uploads.json"), "w"), ensure_ascii=False, indent=1)
    return it

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def setup(self):
        super().setup(); self.connection.settimeout(300)
    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        super().end_headers()
    def _login_guard(self):
        self.user = cloud.session(self)
        if not self.user:
            if urllib.parse.urlparse(self.path).path.startswith("/api/"):
                self._json(dict(erro="Entre para continuar.", login="/login"), 401)
            else:
                self.send_response(303); self.send_header("Location", "/login"); self.send_header("Content-Length", "0"); self.end_headers()
            return False
        self.workspace = resolver_workspace(self)
        comum.definir_workspace(self.workspace)   # daqui em diante nesta requisição, comum.RAIZ já é o deste workspace
        return True
    def _cookie_json(self, obj, value, age=cloud.TTL):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", cloud.cookie(value, age)); self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b)
    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b)
    def _arquivo(self, p, pagina=False):
        if not (pagina or permitido(p)) or not os.path.isfile(p): return self.send_error(404)
        tam = os.path.getsize(p); tipo = mimetypes.guess_type(p)[0] or "application/octet-stream"
        rng = self.headers.get("Range"); ini, fim = 0, tam - 1; code = 200
        if rng:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    ini = int(m.group(1))
                    if m.group(2): fim = min(int(m.group(2)), tam - 1)
                elif m.group(2): ini = max(0, tam - int(m.group(2)))
                else: ini = tam
                if ini >= tam or ini > fim:
                    self.send_response(416); self.send_header("Content-Range", f"bytes */{tam}")
                    self.send_header("Content-Length", "0"); self.end_headers(); return
                code = 206
        self.send_response(code); self.send_header("Content-Type", tipo); self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(fim - ini + 1)); self.send_header("Cache-Control", "no-cache")
        if not pagina:
            # "inline" pros tipos que tocam/mostram na tela (senão <video>/<img> força download em vez de
            # exibir) — mas SEMPRE com filename*, senão quem baixa (o <a download> do render, por exemplo)
            # não tem nome nenhum pra copiar da URL (que é só "/f?p=...") e o navegador chuta um sozinho.
            disposicao = "inline" if tipo.startswith(("video/", "audio/", "image/", "font/")) else "attachment"
            self.send_header("Content-Disposition", f"{disposicao}; filename*=UTF-8''" + urllib.parse.quote(os.path.basename(p)))
        if code == 206: self.send_header("Content-Range", f"bytes {ini}-{fim}/{tam}")
        self.end_headers()
        try:
            with open(p, "rb") as f:
                f.seek(ini); falta = fim - ini + 1
                while falta > 0:
                    ch = f.read(min(1 << 20, falta))
                    if not ch: break
                    self.wfile.write(ch); falta -= len(ch)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path in ("/api/health", "/health"): return self._json(dict(ok=True))
        if u.path == "/login":
            b = cloud.LOGIN_HTML.encode(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b); return
        if not self._login_guard(): return
        try:
            if u.path in ("/", "/inicio"): return self._arquivo(os.path.join(APP, "inicio.html"), pagina=True)
            if u.path == "/editor": return self._arquivo(os.path.join(APP, "editor.html"), pagina=True)
            if u.path == "/cortes": return self._arquivo(os.path.join(APP, "cortes.html"), pagina=True)
            if u.path == "/chat": return self._arquivo(os.path.join(APP, "chat.html"), pagina=True)
            if u.path == "/api/auth/me": return self._json({k: self.user.get(k) for k in ("email", "name", "role")})
            if u.path == "/api/arquivos": return self._json(dict(arquivos=listar_arquivos()))
            if u.path.startswith("/fontes/"):
                return self._arquivo(os.path.join(SKILL, "fontes", urllib.parse.unquote(u.path[len("/fontes/"):])))
            if u.path == "/api/chats":
                return self._json(dict(chats=lista_chats(), chave=bool(chaves.ler()["anthropic"]), modelo=chaves.ler().get("modelo"), home=os.path.expanduser("~")))
            if u.path == "/api/chat/eventos": return self._json(eventos_chat(pasta_chat(q.get("id")), int(q.get("pos") or 0)))
            if u.path == "/api/chat/mencoes": return self._json(mencoes())
            if u.path == "/api/ofertas": return self._json(biblioteca.arvore())
            if u.path == "/api/buscas": return self._json(levas())
            if u.path == "/api/kanban": return self._json(kanban.quadro())
            if u.path == "/api/anuncios-arquivos": return self._json(lista_anuncios())
            if u.path == "/api/cortes": return self._json(dados_cortes(q.get("p"), q.get("v", "A")))
            if u.path == "/api/anuncios":                        # etiquetas de anúncio da pasta (Busca de B-roll), para o "+ Novo vídeo"
                return self._json([dict(leva=os.path.basename(l["id"]), nome=n, criado=l["criado"], template=l.get("template")) for l in levas()
                                   if l["expert"] == q.get("expert") and l["oferta"] == q.get("oferta") for n in l["anuncios"]])
            if u.path == "/f": return self._arquivo(q.get("p", ""))
            if u.path == "/api/projetos": return self._json(info_projetos())
            if u.path == "/api/uso-disco": return self._json(uso_disco())
            if u.path == "/api/visao-geral": return self._json(visao_geral())
            if u.path == "/api/processamento": return self._json(processamento())
            if u.path == "/api/config": return self._json(dict(chaves.publico(), max_upload_bytes=cloud.MAX_UPLOAD))
            if u.path == "/api/workspaces":
                email = self.user.get("email", ""); admin = self.user.get("role") == "admin"
                return self._json(dict(itens=workspaces.acessiveis(email), ativo=(self.workspace or {}).get("id", workspaces.PADRAO_ID),
                                       admin=admin, eu=email, todos=workspaces.listar() if admin else [],
                                       admin_master=workspaces.ADMIN_MASTER if admin else ""))
            if u.path == "/api/estilos": return self._json(estilos(q.get("completo") == "1", q.get("rascunhos") == "1"))
            if u.path == "/api/templates/skill":
                b = zip_skill_criar_template()
                self.send_response(200); self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(b)))
                self.send_header("Content-Disposition", "attachment; filename=\"criar-template-skill.zip\"")
                self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b); return
            if u.path == "/api/estilo/amostra":
                a = amostra_letreiro(q.get("e", ""))
                return self._arquivo(a) if a else self.send_error(404)
            if u.path == "/api/sugestao": return self._json(sugestao())
            if u.path == "/api/custos": return self._json(todos_custos())
            if u.path == "/api/biblioteca": return self._json(lista_biblioteca(q))
            if u.path == "/api/estado":
                proj = pasta_proj(q["p"]); pasta = pasta_versao(q["p"], q["v"]); P = json.load(open(os.path.join(pasta, "plano.json")))
                d = json.load(open(os.path.join(proj, "projeto.json"))); E = comum.estilo(P.get("estilo", "ultradinamico"))
                gan = json.load(open(os.path.join(pasta, "ganhos.json"))) if os.path.exists(os.path.join(pasta, "ganhos.json")) else dict(gpk=1, gln=1)
                # O acervo do editor é só o que foi escolhido pra este projeto (curado + enviado do computador).
                # O resto da biblioteca (filtrando por expert/oferta/categoria de verdade, "recentes" etc.) o
                # pop-up "Escolher B-roll" busca ao vivo em /api/biblioteca — reaproveita a mesma rota da aba
                # Arquivos em vez de duplicar a filtragem aqui, e cada clipe mantém a categoria real dele (o
                # jeito antigo botava os 300 primeiros "disponível" do nicho todos numa categoria falsa só).
                ac = acervo(proj)
                quadros = quadros_prontos(pasta)
                if quadros is None:
                    nova_thread(gerar_quadros, args=(pasta, P["dur"]), daemon=True).start(); quadros = []
                return self._json(dict(plano=P, acervo=ac, proxy=os.path.join(pasta, "pm.mp4"), ganhos=gan, sfx=eventos_sfx(P),
                                       sfx_catalogo=comum.catalogo_sfx(), sfx_dir=os.path.join(SKILL, "sfx"), estilo=E,
                                       fonte_serif=os.path.join(SKILL, E["fontes"]["serif"]["arquivo"]), projeto=d["nome"],
                                       fontes_web=[dict(familia="Avenir Next", arquivo=os.path.join(SKILL, "fontes", f"AvenirNext-{face}.ttf"), peso=peso)
                                                   for face, peso in (("Bold", "700"), ("DemiBold", "600"), ("Heavy", "800"))],
                                       versoes=d["versoes"], versao=q["v"], expert=d.get("expert", ""), oferta=d.get("oferta", ""),
                                       onda=picos_audio_versao(pasta), quadros=quadros))
            if u.path == "/api/editor/quadros": return self._json(dict(quadros=quadros_prontos(pasta_versao(q["p"], q["v"])) or []))
            if u.path == "/api/status": return self._json({f"{k[0]}|{k[1]}": v for k, v in JOBS.items()})
        except (ValueError, KeyError) as e:
            return self._json(dict(erro=str(e)), 400)
        self.send_error(404)

    def _local(self):
        return cloud.safe_origin(self, PORTA)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path); q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if not self._local(): return self._json(dict(erro="origem não permitida"), 403)
        if u.path == "/api/auth/login":
            try:
                d = json.loads(self.rfile.read(cloud.body_size(self, 4096)))
                if not cloud.enabled(): return self._json(dict(ok=True))
                user = cloud.login(d.get("email"), d.get("password"), self.client_address[0])
                if not user: return self._json(dict(erro="E-mail ou senha inválidos."), 401)
                return self._cookie_json(dict(ok=True), cloud.token(user))
            except (ValueError, AttributeError) as e: return self._json(dict(erro=str(e)), 400)
        if not self._login_guard(): return
        if u.path == "/api/auth/logout": return self._cookie_json(dict(ok=True), "", 0)
        if u.path.startswith("/api/config") and self.user.get("role") != "admin":
            return self._json(dict(erro="Somente administradores podem alterar as configurações."), 403)
        if u.path.startswith("/api/workspaces/") and u.path != "/api/workspaces/trocar" and self.user.get("role") != "admin":
            return self._json(dict(erro="Somente administradores podem gerenciar workspaces."), 403)
        try:
            if u.path == "/api/arquivos/upload":
                item = receber_generico(self, q.get("nome", "")); return self._json(dict(ok=True, arquivo=item, arquivos=[item]))
            if u.path == "/api/upload":
                return self._json(dict(ok=True, item=novo_upload(pasta_proj(q["p"]), q.get("nome", "arquivo"), self)))
            if u.path == "/api/novo/arquivo":
                receber_arquivo(self, q.get("id", ""), q.get("tipo", ""), q.get("nome", "")); return self._json(dict(ok=True))
            if u.path == "/api/kanban/arquivo":
                receber_anuncio(self, q.get("id", ""), q.get("n", ""), q.get("nome", "")); return self._json(dict(ok=True))
            if u.path == "/api/busca/arquivo":
                receber_anuncio(self, q.get("id", ""), q.get("n", ""), q.get("nome", "")); return self._json(dict(ok=True))
            if u.path == "/api/chat/arquivo":
                return self._json(dict(ok=True, caminho=receber_anexo(self, pasta_chat(q.get("id")), q.get("nome", ""))))
        except cloud.UploadError as e:
            result = dict(erro=str(e))
            if e.status == 413: result["max_upload_bytes"] = cloud.MAX_UPLOAD
            return self._json(result, e.status)
        except (ValueError, KeyError) as e:
            return self._json(dict(erro=str(e)), 400)
        try: corpo = self.rfile.read(cloud.body_size(self, 8 * 1024**2))
        except ValueError as e: return self._json(dict(erro=str(e)), 413)
        try:
            if u.path == "/api/arquivos/usar": return self._json(usar_arquivo(json.loads(corpo or b"{}")))
            if u.path == "/api/workspaces/criar":
                d = json.loads(corpo or b"{}"); wid = workspaces.criar(d.get("nome"), self.user.get("email", ""))
                return self._json(dict(ok=True, id=wid))
            if u.path == "/api/workspaces/renomear":
                d = json.loads(corpo or b"{}"); workspaces.renomear(d.get("id"), d.get("nome")); return self._json(dict(ok=True))
            if u.path == "/api/workspaces/apagar":
                d = json.loads(corpo or b"{}"); workspaces.apagar(d.get("id")); return self._json(dict(ok=True))
            if u.path == "/api/workspaces/acesso":
                d = json.loads(corpo or b"{}")
                (workspaces.conceder if d.get("conceder") else workspaces.revogar)(d.get("id"), d.get("email"))
                return self._json(dict(ok=True))
            if u.path == "/api/workspaces/trocar":
                d = json.loads(corpo or b"{}"); wid = str(d.get("id") or workspaces.PADRAO_ID)
                email = self.user.get("email", "")
                if wid != workspaces.PADRAO_ID:
                    if not workspaces.pode_acessar(email, wid): raise ValueError("sem acesso a este workspace")
                    workspaces.obter(wid)   # garante que existe (levanta se não)
                b = json.dumps(dict(ok=True)).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
                secure = os.environ.get("EDITOR_IA_PUBLIC_URL", "").startswith("https://")
                self.send_header("Set-Cookie", f"atlas_workspace={wid}; Path=/; SameSite=Lax; Max-Age={365*24*3600}" + ("; Secure" if secure else ""))
                self.send_header("Content-Length", str(len(b))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b)
                return
            if u.path == "/api/config":
                d = json.loads(corpo or b"{}")
                if d.get("modelo") and d["modelo"] not in [m["id"] for m in chaves.MODELOS]: raise ValueError("modelo inválido")
                if d.get("modelo_gemini") and d["modelo_gemini"] not in [m["id"] for m in gemini.MODELOS]: raise ValueError("modelo do Gemini inválido")
                if d.get("modelo_estudo") and d["modelo_estudo"] not in [m["id"] for m in chaves.ESTUDO]: raise ValueError("quem estuda: opção inválida")
                chaves.gravar(anthropic=d.get("anthropic") or None, gemini=d.get("gemini") or None, apify=d.get("apify") or None,
                              openrouter=d.get("openrouter") or None, openai=d.get("openai") or None, heygen=d.get("heygen") or None,
                              modelo=d.get("modelo") or None, modelo_gemini=d.get("modelo_gemini") or None, modelo_estudo=d.get("modelo_estudo") or None)
                return self._json(chaves.publico())
            if u.path == "/api/config/testar":
                qual = q.get("qual")
                return self._json(testar_claude() if qual == "anthropic" else gemini.testar(qual) if qual in ("gemini", "openrouter")
                                  else testar_openai() if qual == "openai" else testar_heygen() if qual == "heygen" else testar_apify())
            if u.path == "/api/novo/iniciar": return self._json(dict(ok=True, slug=iniciar_auto(json.loads(corpo))))
            if u.path == "/api/auto/cancelar": cancelar_auto(pasta_auto(q["p"])); return self._json(dict(ok=True))
            if u.path == "/api/auto/retomar":
                retomar_auto(pasta_auto(q["p"])); return self._json(dict(ok=True))
            if u.path == "/api/templates/publicar":
                d = json.loads(corpo or b"{}"); publicar_template(d.get("id")); return self._json(dict(ok=True))
            if u.path == "/api/chat/novo": return self._json(dict(ok=True, id=novo_chat()))
            if u.path == "/api/pasta/criar": return self._json(dict(ok=True, caminho=criar_pasta(json.loads(corpo))))
            if u.path == "/api/busca/iniciar": return self._json(dict(ok=True, id=iniciar_leva(json.loads(corpo))))
            if u.path == "/api/busca/agendar": return self._json(dict(ok=True, id=agendar_busca_youtube(json.loads(corpo or b"{}"))))
            if u.path == "/api/busca/agendada/cancelar":
                cancelar_agendada(json.loads(corpo or b"{}").get("id")); return self._json(dict(ok=True))
            if u.path == "/api/cortes/salvar": return self._json(salvar_cortes(json.loads(corpo or b"{}")))
            if u.path == "/api/arquivos/apagar": return self._json(apagar_arquivo(json.loads(corpo or b"{}")))
            if u.path.startswith("/api/kanban/"):
                d = json.loads(corpo or b"{}"); acao = u.path.rsplit("/", 1)[1]
                if acao == "criar": return self._json(dict(ok=True, lote=criar_kanban(d)))
                if acao == "mover": kanban.mover(d.get("ids") or [], d.get("coluna")); return self._json(dict(ok=True))
                if acao == "revisado": return self._json(revisar_card(d))
                if acao == "apagar": kanban.apagar(d.get("ids") or []); return self._json(dict(ok=True))
                if acao == "pausar": kanban.pausar(d.get("v", True)); return self._json(dict(ok=True))
                raise ValueError("ação inválida")
            if u.path == "/api/busca/cancelar":
                dl = pasta_leva(q.get("id")); st = json.load(open(os.path.join(dl, "estado.json")))
                try: matar_processo(int(st["pid"]))
                except (ProcessLookupError, PermissionError, KeyError, ValueError): pass
                return self._json(dict(ok=True))
            if u.path == "/api/busca/retomar":
                dl = pasta_leva(q.get("id")); st = json.load(open(os.path.join(dl, "estado.json")))
                if st.get("rodando") and vivo(st.get("pid")): raise ValueError("já está rodando")
                lancar_leva(dl); return self._json(dict(ok=True))
            if u.path == "/api/revelar":
                alvo = os.path.realpath(os.path.expanduser(str(json.loads(corpo or b"{}").get("p") or "")))
                if not permitido(alvo) or not os.path.isfile(alvo): raise ValueError("arquivo não encontrado")
                return self._json(dict(ok=True, url="/f?" + urllib.parse.urlencode(dict(p=alvo))))
            if u.path.startswith("/api/chat/"):
                p = pasta_chat(q.get("id")); d = json.loads(corpo or b"{}"); acao = u.path.rsplit("/", 1)[1]
                if acao == "enviar": enviar_chat(p, d)
                elif acao == "parar": comando_chat(p, tipo="parar")
                elif acao == "responder": comando_chat(p, tipo="resposta", id=str(d.get("id")), r=d.get("r") or {})
                elif acao == "modo": modo_chat(p, d.get("modo"))
                elif acao == "renomear": grava_meta(p, titulo=str(d.get("titulo") or "").strip()[:90] or "Conversa")
                elif acao == "apagar": apagar_chat(p)
                else: raise ValueError("ação inválida")
                return self._json(dict(ok=True))
            if u.path == "/api/biblioteca/limpar-erro":
                st = estado_estudo(); st["erro"] = ""; st.pop("pid", None); json.dump(st, open(ESTUDO_ARQ, "w")); return self._json(dict(ok=True))
            if u.path == "/api/biblioteca/estudar":
                estudar_biblioteca(q.get("nicho") or None, q.get("expert") or None, q.get("oferta") or None, q.get("categoria") or None,
                                   q.get("refazer") == "1"); return self._json(dict(ok=True))
            if u.path == "/api/biblioteca/estado":
                if not biblioteca.mudar_estado(q.get("id", ""), q.get("estado", "")): raise ValueError("B-roll ou estado inválido")
                return self._json(dict(ok=True))
            if u.path == "/api/biblioteca/estados":
                d = json.loads(corpo or b"{}"); ids = [str(i) for i in (d.get("ids") or [])]
                if not ids: raise ValueError("nenhum B-roll selecionado")
                return self._json(dict(ok=True, **biblioteca.mudar_estados(ids, d.get("estado", ""))))
            pasta = pasta_versao(q["p"], q["v"]); chave = (q["p"], q["v"])
            if u.path == "/api/sfx": return self._json(dict(sfx=eventos_sfx(json.loads(corpo))))
            if u.path == "/api/salvar": return self._json(dict(ok=True, plano=salvar(pasta, json.loads(corpo))))
            if u.path == "/api/renderizar":
                if any(j.get("rodando") for j in JOBS.values()): return self._json(dict(erro="já existe um render rodando"), 409)
                if corpo: salvar(pasta, json.loads(corpo))
                JOBS[chave] = dict(rodando=True, etapa="começando", pct=0, saida=None, erro=None, inicio=time.time())
                nova_thread(renderizar, args=chave, daemon=True).start(); return self._json(dict(ok=True))
            if u.path == "/api/quadro":
                d = json.loads(corpo); t = float(d["t"]); P = limpar(d["plano"]); tmp = os.path.join(pasta, "_plano_previa.json")
                json.dump(P, open(tmp, "w"), ensure_ascii=False); img = os.path.join(pasta, "prev", f"quadro_{t:07.2f}.jpg")
                if os.path.exists(img): os.remove(img)
                r = subprocess.run([PY, os.path.join(LIB, "motor.py"), pasta, "preview", f"{t:.3f}", "--plano", "_plano_previa.json"], capture_output=True, text=True, env=env_workspace())
                return self._json(dict(ok=os.path.exists(img), img=img, erro=None if os.path.exists(img) else r.stderr[-500:]))
            if u.path == "/api/png":
                nome = re.sub(r"[^\w.-]", "_", q.get("nome", "x")); os.makedirs(os.path.join(pasta, "prev"), exist_ok=True)
                open(os.path.join(pasta, "prev", f"editor_{nome}.jpg"), "wb").write(base64.b64decode(corpo.decode().split(",", 1)[1]))
                return self._json(dict(ok=True))
        except (ValueError, KeyError) as e:
            return self._json(dict(erro=str(e)), 400)
        self.send_error(404)

if __name__ == "__main__":
    cloud.configuration(HOST)
    for directory in (comum.RAIZ, biblioteca.BROLLS, biblioteca.RAIZ, arquivos()): os.makedirs(directory, exist_ok=True)
    kanban.migrar_fluxo_sem_cortes()
    kanban.recuperar_travados()
    print(f"Atlas Editor em http://{HOST}:{PORTA}", flush=True)
    threading.Thread(target=kanban.laco, kwargs=dict(log=lambda m: print("[kanban]", m, flush=True)), daemon=True).start()
    threading.Thread(target=leva.laco_agendadas, kwargs=dict(log=lambda m: print("[busca agendada]", m, flush=True)), daemon=True).start()
    ThreadingHTTPServer((HOST, PORTA), H).serve_forever()
