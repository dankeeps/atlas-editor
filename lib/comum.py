"""Utilidades comuns do Estúdio: caminhos, estilo, regras de som."""
import os, sys, json, importlib.util, glob, contextvars

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = json.load(open(os.path.join(SKILL, "config.json"))) if os.path.exists(os.path.join(SKILL, "config.json")) else {}
# workspace ativo desta requisição/thread (None = o padrão, ~/Edições de sempre). Ver definir_workspace().
_WORKSPACE = contextvars.ContextVar("workspace_atual", default=None)

def _raiz_padrao():
    # Lido a cada chamada (não congelado na importação): um teste que muda ESTUDIO_RAIZ depois de importar este
    # módulo (comum, biblioteca e outros já importados por um teste anterior no mesmo processo) precisa ver o
    # valor novo, e não um default travado do primeiro import.
    return os.path.abspath(os.path.expanduser(os.environ.get("ESTUDIO_RAIZ") or CONFIG.get("raiz_projetos", "~/Edições")))

def _brolls_padrao():
    return os.path.abspath(os.path.expanduser(os.environ.get("ESTUDIO_BROLLS") or "~/B-rolls"))

def raiz():
    """A pasta de projetos do workspace ativo agora (contextvar por requisição), ou ~/Edições se nenhum workspace
    foi selecionado (uso direto por CLI, testes, ou processos que não passam por definir_workspace). Um teste que
    faça patch.object(comum, "RAIZ", ...) tem prioridade máxima — globals() enxerga o patch direto."""
    patch = globals().get("RAIZ")
    if patch is not None: return patch
    w = _WORKSPACE.get()
    return w["pasta"] if w else _raiz_padrao()

def brolls():
    """A pasta de B-rolls (~/B-rolls) do workspace ativo. Workspaces novos guardam a biblioteca dentro da própria
    pasta (pasta/.brolls); o workspace padrão preserva ~/B-rolls como sempre foi, sem mover nada."""
    patch = globals().get("BROLLS")
    if patch is not None: return patch
    w = _WORKSPACE.get()
    if w: return w.get("brolls") or os.path.join(w["pasta"], ".brolls")
    return _brolls_padrao()

def definir_workspace(w):
    """w = dict do workspace (id/pasta/brolls) ou None para o padrão. Devolve o token do contextvar — guarde e
    chame resetar_workspace(token) num finally, para nunca vazar o workspace de uma requisição para a próxima."""
    return _WORKSPACE.set(w)

def resetar_workspace(tok):
    _WORKSPACE.reset(tok)

def workspace_ativo():
    """O dict do workspace ativo agora, ou None se for o padrão. Para módulos que têm seu próprio caminho fixo de
    ambiente (ex.: ESTUDIO_BIBLIOTECA em biblioteca.py) e precisam saber se esse caminho vale — só quando NÃO há
    workspace ativo, senão o caminho fixo vazaria dados de todo workspace para o mesmo lugar."""
    return _WORKSPACE.get()

def __getattr__(nome):
    """comum.RAIZ nos módulos que já existiam continua funcionando, só que agora é dinâmico por requisição
    (PEP 562): todo `comum.RAIZ` é reavaliado na hora, nunca fica congelado do processo que importou primeiro."""
    if nome == "RAIZ": return raiz()
    if nome == "BROLLS": return brolls()
    raise AttributeError(f"module {__name__!r} has no attribute {nome!r}")

PY = sys.executable
PORTA = int(os.environ.get("ESTUDIO_PORTA") or os.environ.get("PORTA") or CONFIG.get("porta", 4123))
MODELOS = os.path.abspath(os.path.expanduser(os.environ.get("ESTUDIO_MODELOS") or os.path.join(SKILL, "modelos")))
URL_PUBLICA = (os.environ.get("ESTUDIO_URL_PUBLICA") or os.environ.get("EDITOR_IA_PUBLIC_URL") or f"http://localhost:{PORTA}").rstrip("/")
_extras = [os.path.dirname(PY), "/usr/local/bin"]
if sys.platform == "darwin": _extras.append("/opt/homebrew/bin")
os.environ["PATH"] = os.pathsep.join(dict.fromkeys(os.environ.get("PATH", "/usr/bin:/bin").split(os.pathsep) + _extras))

def estilo(nome):
    return json.load(open(os.path.join(SKILL, "estilos", nome, "estilo.json")))

def regras_sfx(nome):
    p = os.path.join(SKILL, "estilos", nome, "sfx_regras.py")
    if os.path.exists(p):
        spec = importlib.util.spec_from_file_location(f"sfx_{nome}", p); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    else:
        import sfx_padrao as m  # templates criados pela skill criar-template nunca ganham um sfx_regras.py próprio (ver lib/template_escrita.py)
    cat = catalogo_sfx()
    for it in cat:                                       # ataque medido para qualquer arquivo da biblioteca
        m.ATAQUE.setdefault(it["arquivo"], it.get("ataque", 0.0))
    return m

def catalogo_sfx():
    p = os.path.join(SKILL, "sfx", "sfx.json")
    return json.load(open(p)) if os.path.exists(p) else []

def caminho_sfx(arquivo, projeto=None):
    for base in ([os.path.join(projeto, "sfx")] if projeto else []) + [os.path.join(SKILL, "sfx")]:
        p = os.path.join(base, arquivo)
        if os.path.exists(p): return p
    raise FileNotFoundError(arquivo)

def projetos():
    out = []
    for p in sorted(glob.glob(os.path.join(raiz(), "*", "projeto.json"))):
        d = json.load(open(p)); d["pasta"] = os.path.dirname(p); out.append(d)
    return out

def quadros_video(p):
    """Número de quadros do vídeo (0 se não der para medir)."""
    import subprocess
    for args in (["stream=nb_frames"], ["stream=nb_read_packets", "-count_packets"]):
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries"] + args
                           + ["-of", "csv=p=0", p], capture_output=True, text=True).stdout.strip()
        if r.isdigit() and int(r) > 0: return int(r)
    return 0

def mascaras_ok(vd):
    """As máscaras da versão batem com o jc.mov atual? Refazer o jump cut invalida o recorte:
    o motor procura a máscara pelo número do quadro, então sobra/falta desloca a silhueta."""
    jc, md = os.path.join(vd, "jc.mov"), os.path.join(vd, "masks")
    if not (os.path.exists(jc) and os.path.isdir(md)): return False
    n = len([f for f in os.listdir(md) if f.endswith(".png")])
    q = quadros_video(jc)
    return q > 0 and n == q and os.path.getmtime(md) >= os.path.getmtime(jc)
