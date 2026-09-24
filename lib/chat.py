"""Chat do Estúdio: o mesmo Claude Code desta máquina (Claude Agent SDK), pago pela chave da API do ⚙.

Um processo por conversa, rodando no Python da .venv da skill:
    .venv/bin/python lib/chat.py <pasta da conversa>
- lê comandos que o servidor deixa em <pasta>/entrada/*.json (mensagem, parar, resposta, modo)
- escreve tudo o que acontece em <pasta>/eventos.jsonl (a tela lê daí)
- meta.json guarda título, sessão (para retomar), modo de permissão e custo; estado.json diz se está trabalhando.
Mesmo motor, mesmas memórias, skills e ferramentas do Claude Code: cwd = home, configurações do usuário carregadas."""
import os, sys, json, time, uuid, glob, base64, asyncio, traceback, warnings
warnings.filterwarnings("ignore", message=".*can_use_tool will not be invoked.*")   # o Skill já é liberado, como no Claude Code

LIB = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, LIB)
import chaves
import comum

HOME = os.path.abspath(os.path.expanduser(os.environ.get("ESTUDIO_CHAT_CWD") or comum.SKILL))
OCIOSO = 30 * 60                       # sem nada para fazer há 30 min: o processo fecha (a próxima mensagem retoma a sessão)
MAX_TXT = 12000                        # resultado de ferramenta guardado para a tela (o Claude recebe inteiro)
EXT_IMG = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
MODOS = ("auto", "default", "acceptEdits", "bypassPermissions", "plan")

def instrucoes(pasta):
    return f"""# Chat do Estúdio de Edição
Você está no chat do Estúdio de Edição ({comum.URL_PUBLICA}), não num terminal. É o mesmo trabalho do Claude Code, com as mesmas skills e memórias.
- O usuário lê o seu texto em markdown. Todo caminho completo de vídeo (.mp4/.mov) ou imagem que você escrever na resposta vira um player ou uma miniatura no chat: ao entregar um render, escreva o caminho completo.
- Arquivos que ele anexa chegam em {pasta}/anexos/ e vêm listados na mensagem.
- Editor de um projeto: {comum.URL_PUBLICA}/editor?p=<pasta do projeto em {comum.RAIZ}>&v=<versão>. A tela inicial do Estúdio já lista os renders.
- O sistema de edição fica em {comum.SKILL}. Leia {comum.SKILL}/SKILL.md antes de editar; execute os scripts de lib/ com {sys.executable}. Os projetos ficam em {comum.RAIZ}, os B-rolls em {os.environ.get('ESTUDIO_BROLLS') or os.path.expanduser('~/B-rolls')}. Caminhos locais citados na documentação equivalem a estas pastas neste servidor.
- Não existe painel de navegador, de terminal nem de arquivos aqui: mostre o que importa na própria resposta."""

# ---------------------------------------------------------------- arquivos da conversa
class Conversa:
    def __init__(s, pasta):
        s.pasta = pasta; s.ev = os.path.join(pasta, "eventos.jsonl"); os.makedirs(os.path.join(pasta, "entrada"), exist_ok=True)
        os.makedirs(os.path.join(pasta, "img"), exist_ok=True)
        s.meta = json.load(open(os.path.join(pasta, "meta.json")))
        s.estado = dict(pid=os.getpid(), ocupado=False, aguardando=0, desde=time.time(), inicio_turno=None)

    MEUS = ("session_id", "custo", "entrada", "saida", "cache_escrita", "cache_leitura", "atualizado", "modo")
    def grava_meta(s):
        """Relê o meta.json do disco (o servidor muda título/modo) e só troca os campos que o motor cuida."""
        try: d = json.load(open(os.path.join(s.pasta, "meta.json")))
        except (OSError, ValueError): d = {}
        d.update({k: s.meta[k] for k in s.MEUS if k in s.meta}); s.meta = dict(d, **{k: s.meta[k] for k in s.MEUS if k in s.meta})
        tmp = os.path.join(s.pasta, "meta.json.tmp"); json.dump(d, open(tmp, "w"), ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(s.pasta, "meta.json"))

    def grava_estado(s, **kw):
        s.estado.update(kw, vivo_em=time.time())
        tmp = os.path.join(s.pasta, "estado.json.tmp"); json.dump(s.estado, open(tmp, "w")); os.replace(tmp, os.path.join(s.pasta, "estado.json"))

    def evento(s, t, **d):
        d = dict(t=t, quando=time.time(), **d)
        with open(s.ev, "a", encoding="utf-8") as f: f.write(json.dumps(d, ensure_ascii=False) + "\n")

    def comandos(s):
        out = []
        for p in sorted(glob.glob(os.path.join(s.pasta, "entrada", "*.json"))):
            try: out.append(json.load(open(p)))
            except ValueError: continue
            finally: os.remove(p)
        return out

    def salva_imagem(s, data, mime):
        ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}.get(mime, ".img")
        p = os.path.join(s.pasta, "img", f"{time.time_ns()}{ext}"); open(p, "wb").write(base64.b64decode(data)); return p

def corta(v, n=MAX_TXT):
    if isinstance(v, str): return v if len(v) <= n else v[:n] + f"\n… (+{len(v) - n} caracteres)"
    if isinstance(v, dict): return {k: corta(x, 20000) for k, x in v.items()}
    if isinstance(v, list): return [corta(x, 20000) for x in v]
    return v

def limpa_ambiente():
    """O servidor pode ter sido aberto de dentro do app do Claude: tira a sessão/login dele para usar só a chave da API."""
    for k in list(os.environ):
        if k.startswith(("CLAUDE", "ANTHROPIC")) or k in ("AI_AGENT", "BAGGAGE", "SENTRY_TRACE"): del os.environ[k]

# ---------------------------------------------------------------- o motor
async def rodar(C):
    from claude_agent_sdk import (ClaudeSDKClient, ClaudeAgentOptions, PermissionResultAllow, PermissionResultDeny,
                                  AssistantMessage, UserMessage, SystemMessage, ResultMessage, StreamEvent,
                                  TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock)
    K = chaves.ler(); chave = K.get("anthropic") or ""
    if not chave: C.evento("erro", txt="Configure a chave da API do Claude no ⚙ da tela inicial."); return
    limpa_ambiente()
    env = dict(ANTHROPIC_API_KEY=chave, CLAUDE_CODE_ENABLE_ASK_USER_QUESTION_TOOL="true", CLAUDE_AGENT_SDK_CLIENT_APP="estudio-edicao/1.0")
    if os.environ.get("ESTUDIO_CHAT_API"): env["ANTHROPIC_BASE_URL"] = os.environ["ESTUDIO_CHAT_API"]      # testes: API falsa
    pend = {}                                             # pedidos de permissão/pergunta esperando a tela
    loop = asyncio.get_running_loop()

    async def pode(nome, entrada, ctx):
        rid = uuid.uuid4().hex[:12]; fut = loop.create_future(); pend[rid] = fut
        C.grava_estado(aguardando=len(pend))
        if nome == "AskUserQuestion": C.evento("pergunta", id=rid, perguntas=entrada.get("questions", []))
        else:
            C.evento("permissao", id=rid, nome=nome, entrada=corta(entrada), titulo=ctx.title or "", motivo=ctx.decision_reason or "",
                     caminho=ctx.blocked_path or "", sempre=bool(ctx.suggestions), ferramenta=ctx.tool_use_id or "")
        try: r = await fut
        finally: pend.pop(rid, None); C.grava_estado(aguardando=len(pend))
        C.evento("respondido", id=rid, r=r)
        if nome == "AskUserQuestion":
            if r.get("respostas"): return PermissionResultAllow(updated_input=dict(entrada, answers=r["respostas"]))
            return PermissionResultDeny(message="O usuário preferiu não responder agora.", interrupt=bool(r.get("parar")))
        if r.get("ok"):
            return PermissionResultAllow(updated_permissions=ctx.suggestions if r.get("sempre") else None)
        return PermissionResultDeny(message=r.get("motivo") or "O usuário negou esta ação.", interrupt=bool(r.get("parar")))

    modo = C.meta.get("modo") if C.meta.get("modo") in MODOS else "auto"
    o = ClaudeAgentOptions(model=C.meta.get("modelo") or K.get("modelo") or "claude-opus-5", cwd=HOME,
                           system_prompt={"type": "preset", "preset": "claude_code", "append": instrucoes(C.pasta)},
                           setting_sources=["user", "project", "local"], skills="all", include_partial_messages=True,
                           permission_mode=modo, can_use_tool=pode, resume=C.meta.get("session_id") or None, env=env,
                           max_buffer_size=256 << 20,     # folha de contato lida pelo Read volta em base64 e passa de 1 MB (padrão do SDK)
                           stderr=lambda l: open(os.path.join(C.pasta, "motor.log"), "a").write(l.rstrip() + "\n"))
    rasc = dict(buf="", t=0.0)
    def solta():
        if rasc["buf"]: C.evento("rasc", txt=rasc["buf"]); rasc["buf"] = ""
        rasc["t"] = time.time()
    custo_proc = dict(base=None)
    ultimo = dict(t=time.time())

    cli = ClaudeSDKClient(o)
    try: await cli.connect()
    except Exception as e:
        if not o.resume: raise
        C.evento("aviso", txt=f"Não consegui retomar a sessão anterior ({str(e)[:160]}); o Claude começa uma sessão nova — o histórico acima fica só na tela.")
        o.resume = None; C.meta["session_id"] = None; custo_proc["base"] = float(C.meta.get("custo") or 0)
        cli = ClaudeSDKClient(o); await cli.connect()
    init = dict(visto=False)
    try:
        C.grava_estado(ocupado=False)

        async def ler():
            async for m in cli.receive_messages():
                ultimo["t"] = time.time()
                if isinstance(m, StreamEvent):
                    if m.parent_tool_use_id: continue
                    e = m.event; tp = e.get("type")
                    if tp == "content_block_start":
                        b = e.get("content_block", {}).get("type")
                        if b == "text": solta(); C.evento("rasc_ini")
                        elif b in ("thinking", "redacted_thinking"): C.evento("pensando")
                    elif tp == "content_block_delta" and e.get("delta", {}).get("type") == "text_delta":
                        rasc["buf"] += e["delta"].get("text", "")
                        if time.time() - rasc["t"] > 0.12: solta()
                    elif tp == "content_block_stop": solta()
                    continue
                if isinstance(m, AssistantMessage):
                    pai = m.parent_tool_use_id
                    if m.error: C.evento("erro", txt=erro_legivel(m.error), pai=pai)
                    for b in m.content:
                        if isinstance(b, TextBlock) and b.text.strip(): C.evento("texto", txt=b.text, pai=pai)
                        elif isinstance(b, ThinkingBlock) and b.thinking.strip(): C.evento("pensou", txt=b.thinking, pai=pai)
                        elif isinstance(b, ToolUseBlock): C.evento("ferr", id=b.id, nome=b.name, entrada=corta(b.input), pai=pai)
                elif isinstance(m, UserMessage):
                    if not isinstance(m.content, list): continue
                    for b in m.content:
                        if not isinstance(b, ToolResultBlock): continue
                        txt, imgs = [], []
                        partes = b.content if isinstance(b.content, list) else [dict(type="text", text=b.content or "")]
                        for p in partes:
                            if p.get("type") == "text": txt.append(p.get("text", ""))
                            elif p.get("type") == "image" and (p.get("source") or {}).get("data"):
                                imgs.append(C.salva_imagem(p["source"]["data"], p["source"].get("media_type", "image/jpeg")))
                        C.evento("res", id=b.tool_use_id, txt=corta("\n".join(txt)), erro=bool(b.is_error), imgs=imgs, pai=m.parent_tool_use_id)
                elif isinstance(m, ResultMessage):
                    solta(); tot = float(m.total_cost_usd or 0); u = m.usage or {}
                    if custo_proc["base"] is None:
                        # o motor devolve o custo acumulado da SESSÃO; ao retomar, ele costuma trazer o das vezes anteriores junto.
                        # Se não trouxe (tot ≈ custo só desta rodada), soma ao que a conversa já tinha.
                        import custos
                        rodada = custos.custo_claude(o.model, int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0),
                                                     int(u.get("cache_creation_input_tokens") or 0), int(u.get("cache_read_input_tokens") or 0))
                        antes = float(C.meta.get("custo") or 0)
                        custo_proc["base"] = 0.0 if antes and tot - rodada > 0.5 * antes else antes
                    novo = custo_proc["base"] + tot; delta = max(0.0, novo - float(C.meta.get("custo") or 0))
                    for k_m, k_u in (("entrada", "input_tokens"), ("saida", "output_tokens"), ("cache_escrita", "cache_creation_input_tokens"),
                                     ("cache_leitura", "cache_read_input_tokens")):
                        C.meta[k_m] = C.meta.get(k_m, 0) + int(u.get(k_u) or 0)
                    C.meta["custo"] = round(novo, 6); C.meta["atualizado"] = time.time(); C.meta["session_id"] = m.session_id
                    C.grava_meta()
                    fim = dict(custo=round(delta, 4), total=C.meta["custo"], dur=m.duration_ms, turnos=m.num_turns)
                    if m.is_error and m.subtype != "success":
                        fim["erro"] = erro_legivel(m.subtype) if m.subtype != "error_during_execution" else "parado"
                    if m.is_error and m.result: fim["erro"] = m.result[:600]
                    C.evento("fim", **fim)
                    C.grava_estado(ocupado=False, inicio_turno=None)
                elif isinstance(m, SystemMessage):
                    d = m.data or {}
                    if m.subtype == "init":
                        if d.get("session_id") and d["session_id"] != C.meta.get("session_id"):
                            C.meta["session_id"] = d["session_id"]; C.grava_meta()
                        if not init["visto"]: C.evento("init", modelo=d.get("model"), modo=d.get("permissionMode"), skills=len(d.get("skills") or []))
                        init["visto"] = True
                    elif m.subtype == "compact_boundary": C.evento("aviso", txt="A conversa ficou longa e foi resumida para continuar (igual ao Claude Code).")
                    elif m.subtype == "status" and d.get("status") == "compacting": C.evento("aviso", txt="Resumindo a conversa…")
                    elif m.subtype == "permission_denied":
                        C.evento("negado", id=d.get("tool_use_id"), nome=d.get("tool_name"), motivo=d.get("decision_reason") or "")
                    elif m.subtype in ("task_started", "task_notification"):
                        C.evento("tarefa", sub=m.subtype, desc=d.get("description") or d.get("summary") or "", status=d.get("status"),
                                 id=d.get("tool_use_id"))

        async def comandos():
            while True:
                for c in C.comandos():
                    ultimo["t"] = time.time(); tipo = c.get("tipo")
                    if tipo == "msg":
                        texto = str(c.get("texto") or ""); anexos = [a for a in c.get("anexos") or [] if os.path.isfile(a)]
                        C.evento("usuario", txt=texto, anexos=anexos)
                        C.meta["atualizado"] = time.time(); C.grava_meta()
                        await cli.query(mensagem(texto, anexos))
                        C.grava_estado(ocupado=True, inicio_turno=C.estado.get("inicio_turno") or time.time())
                    elif tipo == "parar":
                        for f in list(pend.values()):
                            if not f.done(): f.set_result(dict(ok=False, parar=True, motivo="O usuário parou."))
                        await cli.interrupt(); C.evento("aviso", txt="Parado.")
                    elif tipo == "resposta":
                        f = pend.get(c.get("id"))
                        if f and not f.done(): f.set_result(c.get("r") or {})
                    elif tipo == "modo" and c.get("modo") in MODOS:
                        await cli.set_permission_mode(c["modo"]); C.meta["modo"] = c["modo"]; C.grava_meta()
                        C.evento("modo", modo=c["modo"])
                    elif tipo == "fechar": return
                if leitor.done(): return                      # o motor fechou (erro ou fim): o finally mostra o motivo
                if not C.estado["ocupado"] and not pend and time.time() - ultimo["t"] > OCIOSO: return
                C.grava_estado(); await asyncio.sleep(0.25)

        async def mensagem(texto, anexos):
            partes = [dict(type="text", text=texto or "(anexos)")]
            outros = []
            for a in anexos:
                ext = os.path.splitext(a)[1].lower()
                if ext in EXT_IMG and os.path.getsize(a) < 4.5e6:
                    partes.append(dict(type="image", source=dict(type="base64", media_type=EXT_IMG[ext], data=base64.b64encode(open(a, "rb").read()).decode())))
                outros.append(a)
            if outros: partes[0]["text"] += "\n\nArquivos anexados:\n" + "\n".join(f"- {a}" for a in outros)
            yield dict(type="user", message=dict(role="user", content=partes), parent_tool_use_id=None)

        leitor = asyncio.create_task(ler())
        try:
            await comandos()
        finally:
            if leitor.done() and not leitor.cancelled() and leitor.exception(): raise leitor.exception()
            leitor.cancel()
    finally:
        await cli.disconnect()

def erro_legivel(e):
    return {"authentication_failed": "A chave da API do Claude foi recusada. Confira no ⚙ da tela inicial.",
            "billing_error": "A conta da API está sem crédito (console.anthropic.com → Billing).",
            "rate_limit": "Limite de uso da API atingido; espere um pouco e mande de novo.",
            "invalid_request": "A API recusou o pedido.", "server_error": "A API do Claude falhou; tente de novo.",
            "error_max_turns": "Parou no limite de passos.", "error_max_budget_usd": "Parou no limite de gasto."}.get(e, str(e))

def main():
    pasta = os.path.abspath(sys.argv[1]); C = Conversa(pasta)
    try:
        asyncio.run(rodar(C))
    except Exception as e:
        C.evento("erro", txt=f"O chat parou: {e}\nMande uma mensagem (ex.: \"continua\") para ele retomar de onde parou.")
        open(os.path.join(pasta, "motor.log"), "a").write(traceback.format_exc())
    finally:
        C.estado.update(pid=None, ocupado=False, aguardando=0); C.grava_estado()

if __name__ == "__main__":
    main()
