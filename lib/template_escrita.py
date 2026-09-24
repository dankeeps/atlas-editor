"""Escrita segura de um template novo (estilos/<nome>/), pensado para um agente de IA que só deve tocar
nesta pasta chamando `criar_rascunho()` — hoje é a skill `.claude/skills/criar-template/` (Claude Code local,
ver `scripts/criar_template.py`), mas a regra vale pra qualquer agente que vier a usar isto.

Regras, todas fixas em código (não dependem do agente "se comportar"):
- o nome da pasta NUNCA pode já existir em estilos/ — criar_rascunho() só cria, nunca edita nem substitui
  nada, nem um rascunho seu de uma tentativa anterior. Pra "melhorar" um template já criado, o jeito é pedir
  um NOVO template com um nome de versão (ex. "meu-estilo-v1", "meu-estilo-v2" — ver proximo_nome_versao()),
  nunca reescrever o que já existe. Apagar um antigo que não serve mais é sempre uma ação manual de humano,
  fora daqui;
- lista fixa de arquivos permitidos, e **nenhum `.py` nunca** — `sfx_regras.py` é código executado de
  verdade por `comum.regras_sfx()`; deixar a IA escrever esse arquivo seria uma forma de rodar código no
  servidor mesmo restringindo a pasta. Todo template criado por aqui usa o som padrão de `lib/sfx_padrao.py`
  até um humano escrever um `sfx_regras.py` próprio à mão;
- `estilo.json` passa por validação de estrutura antes de gravar;
- nasce sempre com `publicado: false` e `origem: "claude"` (o que vier no payload é ignorado nesses dois
  campos) — só fica selecionável para anúncios reais depois que alguém publica pela tela (ver
  `publicar_template` em app/servidor.py); `origem` é só informativo (diferencia um template feito pela
  skill dos 3 originais feitos à mão, que não têm essa chave) — não abre nenhum fluxo de "pedir ajuste".
"""
import os, re, sys, json, shutil, tempfile, fcntl, contextlib

LIB = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, LIB)
import comum

def _estilos_raiz(): return os.path.join(comum.SKILL, "estilos")   # função, não constante: comum.SKILL pode ser trocado nos testes
def _fontes_raiz(): return os.path.join(comum.SKILL, "fontes")

ARQUIVOS_PERMITIDOS = {"estilo.json", "LEIA.md", "BUSCA.md", "exemplo.md", "exemplo_fonte.txt", "amostra.jpg"}
TAMANHO_MAX = {"estilo.json": 200_000, "LEIA.md": 50_000, "BUSCA.md": 50_000, "exemplo.md": 50_000,
               "exemplo_fonte.txt": 20_000, "amostra.jpg": 6_000_000}
NOME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
LETREIRO_CAMPOS = ("familia", "px", "cor", "tracking", "efeito")
MODOS_VALIDOS = ("broll_primeiro", "plano")
LIMITE_ANINHAMENTO = 40  # um estilo.json real tem uns 5 níveis; isso sobra bastante e ainda fica bem abaixo
                         # do que derruba o parser recursivo do json (RecursionError) com uma estrutura hostil


class TemplateInvalido(ValueError):
    pass


def _profundidade_excessiva(dados):
    """Confere ANTES de json.loads(): sem isso, um "[[[[...]]]]" bem aninhado (nem precisa ser grande em
    bytes) derruba o parser recursivo do Python com RecursionError em vez de um erro tratável. Passada
    única pelos bytes, sem recursão, respeitando o que está dentro de string (aspas/escape)."""
    texto = dados.decode("utf-8", "replace") if isinstance(dados, (bytes, bytearray)) else dados
    profundidade = 0; dentro_string = False; escapado = False
    for c in texto:
        if dentro_string:
            if escapado: escapado = False
            elif c == "\\": escapado = True
            elif c == '"': dentro_string = False
            continue
        if c == '"': dentro_string = True
        elif c in "{[":
            profundidade += 1
            if profundidade > LIMITE_ANINHAMENTO: return True
        elif c in "}]":
            profundidade -= 1
    return False


def _dentro(p, raiz):
    raiz = os.path.realpath(raiz).rstrip(os.sep)
    p = os.path.realpath(p)
    return p == raiz or p.startswith(raiz + os.sep)


@contextlib.contextmanager
def _trava():
    estilos = _estilos_raiz()
    os.makedirs(estilos, exist_ok=True)
    with open(os.path.join(estilos, ".trava"), "w") as tv:
        fcntl.flock(tv, fcntl.LOCK_EX)
        yield


def validar_estilo_json(E):
    """Checa só o que é carregado sem valor padrão em lib/motor.py e o que outras telas dependem — não
    valida cada parâmetro estético (zoom, escuro, cor...): a prova real de que um template funciona é o
    Laboratório rodar um teste com ele, não uma lista de regras aqui."""
    if not isinstance(E, dict): raise TemplateInvalido("estilo.json precisa ser um objeto")
    for chave, tipo in (("nome", str), ("descricao", str), ("usa", dict), ("processo", list),
                        ("letreiro", dict), ("legenda", dict)):
        if not isinstance(E.get(chave), tipo): raise TemplateInvalido(f"estilo.json: \"{chave}\" ausente ou com tipo errado")
    if E.get("modo") not in MODOS_VALIDOS:
        raise TemplateInvalido(f"estilo.json: \"modo\" precisa ser um de {MODOS_VALIDOS}")

    letreiro_estilos = E["letreiro"].get("estilos")
    if not isinstance(letreiro_estilos, dict) or not letreiro_estilos:
        raise TemplateInvalido("estilo.json: precisa de pelo menos um item em letreiro.estilos")
    efeitos_definidos = E["letreiro"].get("efeitos") or {}
    for chave, le in letreiro_estilos.items():
        if not isinstance(le, dict) or any(c not in le for c in LETREIRO_CAMPOS):
            raise TemplateInvalido(f"estilo.json: letreiro.estilos.{chave} incompleto (precisa de {LETREIRO_CAMPOS})")
        cor = le.get("cor")
        if not (isinstance(cor, list) and len(cor) == 3 and all(isinstance(c, int) and 0 <= c <= 255 for c in cor)):
            raise TemplateInvalido(f"estilo.json: letreiro.estilos.{chave}.cor precisa ser [r, g, b] (0-255)")
        if le["efeito"] not in efeitos_definidos:
            raise TemplateInvalido(f"estilo.json: letreiro.estilos.{chave}.efeito \"{le['efeito']}\" não existe em letreiro.efeitos")

    leg = E["legenda"]
    for chave in ("familia", "tam", "pad", "x", "y", "max_palavras", "max_caracteres", "sombras"):
        if chave not in leg: raise TemplateInvalido(f"estilo.json: legenda.{chave} ausente")
    formatos = (E.get("usa") or {}).get("formatos") or []
    if "canto" in formatos and ("canto_x" not in leg or "canto_dy" not in leg):
        raise TemplateInvalido("estilo.json: usa.formatos inclui \"canto\", mas falta legenda.canto_x/canto_dy")
    if "dividida" in formatos and "dividida_dy" not in leg:
        raise TemplateInvalido("estilo.json: usa.formatos inclui \"dividida\", mas falta legenda.dividida_dy")

    # lib/motor.py:fonte() resolve "arquivo" relativo à raiz do repositório (SKILL), não a fontes/ — por isso
    # o join é com comum.SKILL aqui, igual lá; só a checagem de fronteira (_dentro) usa fontes_raiz, pra
    # travar em "arquivo" tem que ser algo tipo "fontes/Nome.ttf", nunca escapar pra fora dessa pasta.
    fontes_raiz = _fontes_raiz()
    for chave, F in (E.get("fontes") or {}).items():
        if not isinstance(F, dict) or not isinstance(F.get("arquivo"), str):
            raise TemplateInvalido(f"estilo.json: fontes.{chave} sem \"arquivo\"")
        caminho = os.path.join(comum.SKILL, F["arquivo"])
        if not _dentro(caminho, fontes_raiz) or not os.path.exists(caminho):
            raise TemplateInvalido(
                f"estilo.json: fontes.{chave}.arquivo precisa apontar para uma fonte que já existe em "
                f"{fontes_raiz}, como \"fontes/{{nome-do-arquivo}}\" (com esse prefixo — é assim que "
                f"lib/motor.py resolve o caminho)")


def criar_rascunho(nome, arquivos):
    """arquivos: dict com o nome de cada arquivo -> conteúdo em bytes. Cria estilos/<nome>/ do zero;
    nunca sobrescreve nem toca em nada que já exista. Devolve o caminho da pasta criada."""
    if not isinstance(nome, str) or not NOME_RE.fullmatch(nome) or len(nome) > 60:
        raise TemplateInvalido("nome do template precisa ser em letras minúsculas, números e hífen (ex.: \"vsl-gancho-forte\")")
    if not isinstance(arquivos, dict) or not arquivos:
        raise TemplateInvalido("nenhum arquivo recebido")
    if "estilo.json" not in arquivos:
        raise TemplateInvalido("estilo.json é obrigatório")
    extras = set(arquivos) - ARQUIVOS_PERMITIDOS
    if extras:
        raise TemplateInvalido(f"arquivo(s) não permitido(s): {', '.join(sorted(extras))}")
    for nome_arq, conteudo in arquivos.items():
        if not isinstance(conteudo, (bytes, bytearray)):
            raise TemplateInvalido(f"{nome_arq}: conteúdo precisa ser bytes")
        if len(conteudo) > TAMANHO_MAX[nome_arq]:
            raise TemplateInvalido(f"{nome_arq}: maior que o limite permitido ({TAMANHO_MAX[nome_arq]} bytes)")

    if _profundidade_excessiva(arquivos["estilo.json"]):
        raise TemplateInvalido("estilo.json: estrutura aninhada demais")
    try:
        E = json.loads(arquivos["estilo.json"])
    except (ValueError, RecursionError):
        raise TemplateInvalido("estilo.json: JSON inválido")
    validar_estilo_json(E)
    E["publicado"] = False  # nunca confia no que veio no payload — todo template novo nasce rascunho
    E["origem"] = "claude"  # marca "isto foi a skill criar-template que gravou" — só informativo na tela
    arquivos = dict(arquivos, **{"estilo.json": json.dumps(E, ensure_ascii=False, indent=1).encode("utf-8")})

    estilos_raiz = _estilos_raiz()
    destino = os.path.join(estilos_raiz, nome)
    if not _dentro(destino, estilos_raiz):
        raise TemplateInvalido("nome de template inválido")

    with _trava():
        if os.path.exists(destino):
            raise TemplateInvalido("já existe um template com esse nome — peça um novo com nome de versão (ex.: \"-v2\") em vez de reescrever este")
        tmp = tempfile.mkdtemp(prefix=".rascunho-", dir=estilos_raiz)
        try:
            for nome_arq, conteudo in arquivos.items():
                caminho = os.path.join(tmp, nome_arq)
                if not _dentro(caminho, tmp): raise TemplateInvalido(f"{nome_arq}: nome de arquivo inválido")
                with open(caminho, "wb") as f: f.write(conteudo)
            os.rename(tmp, destino)
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    return destino


def proximo_nome_versao(base):
    """O próximo nome disponível pra "melhorar" um template existente, sem editar nem substituir nada: o
    próprio "base" (se ele mesmo ainda não existir — primeira vez), senão "base-v2", "base-v3"… É o nome que
    a skill `.claude/skills/criar-template/` usa (`scripts/criar_template.py proximo-nome`) quando o pedido
    é "melhora esse template", em vez de escolher um nome novo por conta própria."""
    if not isinstance(base, str) or not NOME_RE.fullmatch(base) or len(base) > 60:
        raise TemplateInvalido("nome de template inválido")
    estilos_raiz = _estilos_raiz()
    if not os.path.exists(os.path.join(estilos_raiz, base)):
        return base
    n = 2
    while os.path.exists(os.path.join(estilos_raiz, f"{base}-v{n}")):
        n += 1
    return f"{base}-v{n}"
