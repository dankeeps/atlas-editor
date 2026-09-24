"""Chaves de API do Estúdio (Claude, Gemini e Apify) e os modelos escolhidos para a edição automática.
Ficam só neste Mac, em ~/.config/estudio-edicao/chaves.json (permissão 600), fora das pastas que o servidor
serve. A página nunca recebe a chave inteira: só se está configurada e os 4 últimos caracteres."""
import os, json

ARQ = os.path.abspath(os.path.expanduser(os.environ.get("ESTUDIO_CHAVES") or "~/.config/estudio-edicao/chaves.json"))
PASTA = os.path.dirname(ARQ)
MODELOS = [dict(id="claude-opus-5", nome="Claude Opus 5 (recomendado)"),
           dict(id="claude-sonnet-5", nome="Claude Sonnet 5 (mais barato)")]
PADRAO = dict(anthropic="", gemini="", openrouter="", apify="", openai="", heygen="",
              modelo="claude-opus-5", modelo_gemini="gemini-3.8-flash", modelo_estudo="gemini")
ESTUDO = [dict(id="gemini", nome="Gemini (assiste o vídeo segundo a segundo; ~US$ 0,01 por clipe, uma vez)"),
          dict(id="claude-opus-5", nome="Claude Opus 5 (pela folha de quadros; ~US$ 0,05 por clipe, uma vez)"),
          dict(id="claude-sonnet-5", nome="Claude Sonnet 5 (pela folha de quadros; ~US$ 0,02 por clipe, uma vez)")]

def ler():
    d = dict(PADRAO)
    if os.path.exists(ARQ):
        try: d.update(json.load(open(ARQ)))
        except ValueError: pass
    return d

def gravar(**novos):
    d = ler()
    for k, v in novos.items():
        if v is None: continue
        d[k] = v.strip() if isinstance(v, str) else v
    os.makedirs(PASTA, mode=0o700, exist_ok=True); os.chmod(PASTA, 0o700)
    tmp = ARQ + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: json.dump(d, f, indent=1)
    os.replace(tmp, ARQ); os.chmod(ARQ, 0o600)
    return d

def publico():
    """O que a página pode ver."""
    import gemini
    d = ler(); final = lambda k: ("…" + k[-4:]) if len(k or "") > 8 else ""
    return dict(anthropic=dict(ok=bool(d["anthropic"]), final=final(d["anthropic"])),
                gemini=dict(ok=bool(d.get("gemini")), final=final(d.get("gemini", ""))),
                openrouter=dict(ok=bool(d.get("openrouter")), final=final(d.get("openrouter", ""))),
                apify=dict(ok=bool(d["apify"]), final=final(d["apify"])),
                openai=dict(ok=bool(d.get("openai")), final=final(d.get("openai", ""))),
                heygen=dict(ok=bool(d.get("heygen")), final=final(d.get("heygen", ""))),
                modelo=d.get("modelo") or PADRAO["modelo"], modelos=MODELOS,
                modelo_gemini=d.get("modelo_gemini") or PADRAO["modelo_gemini"], modelos_gemini=gemini.MODELOS,
                modelo_estudo=d.get("modelo_estudo") or PADRAO["modelo_estudo"], estudos=ESTUDO)
