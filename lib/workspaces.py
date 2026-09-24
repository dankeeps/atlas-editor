"""Workspaces: cada um é uma pasta própria e isolada (projetos, biblioteca de B-roll, Kanban, Laboratório —
tudo dentro dela), para times/clientes diferentes não verem dado um do outro no mesmo Atlas Editor.

O registro (quem existe, quem tem acesso a quê) fica FORA de qualquer workspace, em
~/.config/estudio-edicao/workspaces.json — não pode morar dentro de um workspace específico, senão apagar aquele
workspace apagaria também quem tem acesso a todos os outros.

O e-mail em ATLAS_ADMIN_MASTER (env) é o admin master: sempre tem acesso a todo workspace, mesmo sem estar na
lista — é a garantia de que o dono nunca fica trancado para fora do próprio sistema, mesmo que alguém erre a
lista de acesso. Sem essa variável (instalação local, uso pessoal), não existe admin master fixo.

O workspace "padrão" é o ~/Edições / ~/B-rolls que já existia antes de workspaces existirem: nada foi movido, nada
migrado. Fica aberto para qualquer pessoa que já loga no Atlas Editor até o admin master decidir restringir
(conceder para alguém específico passa a exigir a lista; ver pode_acessar).

  python3 workspaces.py listar
"""
import os, sys, json, re, secrets, threading, time
LIB = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, LIB)
import comum

ARQ = os.path.abspath(os.path.expanduser(os.environ.get("ESTUDIO_WORKSPACES") or "~/.config/estudio-edicao/workspaces.json"))
PASTA_CONFIG = os.path.dirname(ARQ)
ADMIN_MASTER = os.environ.get("ATLAS_ADMIN_MASTER", "").strip().lower()
PADRAO_ID = "padrao"
_trava = threading.RLock()


def _padrao():
    return dict(id=PADRAO_ID, nome="Principal", pasta=comum._raiz_padrao(), brolls=comum._brolls_padrao(), criado="", dono=ADMIN_MASTER)


def _ler():
    if not os.path.exists(ARQ): return dict(workspaces={}, acesso={})
    try:
        d = json.load(open(ARQ))
    except (OSError, ValueError):
        d = dict(workspaces={}, acesso={})
    if not isinstance(d, dict): d = {}
    d.setdefault("workspaces", {}); d.setdefault("acesso", {})
    return d


def _gravar(d):
    os.makedirs(PASTA_CONFIG, mode=0o700, exist_ok=True); os.chmod(PASTA_CONFIG, 0o700)
    tmp = ARQ + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ARQ); os.chmod(ARQ, 0o600)


def obter(wid):
    """O dict do workspace (id/nome/pasta/brolls/...), sem checar acesso — quem chama decide se pode ou não."""
    if not wid or wid == PADRAO_ID: return _padrao()
    w = _ler()["workspaces"].get(wid)
    if not w: raise ValueError("workspace não encontrado")
    return w


def listar():
    """Todos os workspaces que existem (o padrão sempre primeiro), cada um com sua lista de acesso (a do padrão
    vem vazia == aberto, a não ser que o admin master já tenha restringido)."""
    d = _ler()
    out = [dict(_padrao(), acesso=sorted(d["acesso"].get(PADRAO_ID, [])))]
    for wid, w in sorted(d["workspaces"].items(), key=lambda kv: kv[1].get("criado", "")):
        # a lista de acesso é só o que está gravado — o dono só continua ali se ninguém revogou dele
        out.append(dict(w, acesso=sorted(set(d["acesso"].get(wid, [])))))
    return out


def pode_acessar(email, wid):
    email = str(email or "").strip().lower()
    if not email: return False
    if email == ADMIN_MASTER: return True
    d = _ler()
    if (wid or PADRAO_ID) == PADRAO_ID and PADRAO_ID not in d["acesso"]:
        return True   # ninguém restringiu o principal ainda: continua aberto pra quem já loga, como sempre foi
    return email in {e.lower() for e in d["acesso"].get(wid or PADRAO_ID, [])}


def acessiveis(email):
    return [w for w in listar() if pode_acessar(email, w["id"])]


def criar(nome, dono):
    """Cria a pasta (própria, fora de qualquer outro workspace) e registra. Só quem já é admin (role) deve poder
    chamar isto — checagem fica em app/servidor.py, aqui é só o mecanismo."""
    nome = str(nome or "").strip()
    if not nome: raise ValueError("dê um nome ao workspace")
    if len(nome) > 80: raise ValueError("nome muito longo")
    with _trava:
        d = _ler()
        if any(w.get("nome", "").strip().lower() == nome.lower() for w in d["workspaces"].values()):
            raise ValueError(f"já existe um workspace chamado '{nome}'")
        wid = "w" + secrets.token_hex(4)
        slug = re.sub(r"[^\w -]", "", nome, flags=re.UNICODE).strip() or wid
        pasta = os.path.abspath(os.path.expanduser(os.path.join("~", f"Edições - {slug}")))
        k = 2
        while os.path.exists(pasta): pasta = os.path.abspath(os.path.expanduser(os.path.join("~", f"Edições - {slug} ({k})"))); k += 1
        os.makedirs(pasta, exist_ok=True)
        d["workspaces"][wid] = dict(id=wid, nome=nome, pasta=pasta, brolls=None,
                                    criado=time.strftime("%Y-%m-%d %H:%M"), dono=str(dono or "").strip().lower())
        d["acesso"][wid] = sorted({str(dono or "").strip().lower(), ADMIN_MASTER} - {""})
        _gravar(d)
    return wid


def renomear(wid, nome):
    if wid == PADRAO_ID: raise ValueError("o workspace principal não pode ser renomeado por aqui")
    nome = str(nome or "").strip()
    if not nome: raise ValueError("dê um nome ao workspace")
    with _trava:
        d = _ler()
        if wid not in d["workspaces"]: raise ValueError("workspace não encontrado")
        d["workspaces"][wid]["nome"] = nome[:80]
        _gravar(d)


def conceder(wid, email):
    email = str(email or "").strip().lower()
    if not email or "@" not in email: raise ValueError("e-mail inválido")
    with _trava:
        d = _ler()
        if wid != PADRAO_ID and wid not in d["workspaces"]: raise ValueError("workspace não encontrado")
        d["acesso"].setdefault(wid, [])
        if email not in [e.lower() for e in d["acesso"][wid]]: d["acesso"][wid].append(email)
        _gravar(d)


def revogar(wid, email):
    email = str(email or "").strip().lower()
    if email == ADMIN_MASTER: raise ValueError("o admin master não pode perder acesso")
    with _trava:
        d = _ler()
        d["acesso"][wid] = [e for e in d["acesso"].get(wid, []) if e.lower() != email]
        _gravar(d)


def apagar(wid):
    """Só desliga do registro (a pasta com o dado real do cliente continua no disco — apagar é ação manual,
    de propósito, pra nunca perder anúncio/B-roll de cliente por engano de clique)."""
    if wid == PADRAO_ID: raise ValueError("o workspace principal não pode ser apagado")
    with _trava:
        d = _ler()
        if wid not in d["workspaces"]: raise ValueError("workspace não encontrado")
        pasta = d["workspaces"][wid]["pasta"]
        d["workspaces"].pop(wid); d["acesso"].pop(wid, None)
        _gravar(d)
    return pasta


if __name__ == "__main__":
    if sys.argv[1:2] == ["listar"]:
        for w in listar(): print(w["id"], "·", w["nome"], "·", w["pasta"], "· acesso:", ", ".join(w["acesso"]) or "(aberto)")
    else:
        print(__doc__)
