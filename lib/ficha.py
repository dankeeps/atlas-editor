"""Ficha do B-roll: o "olhar" de editor sobre um clipe, escrito uma vez e guardado na biblioteca.

Quem olha (Gemini assistindo o vídeo, ou Claude pela folha de quadros) preenche este formato; toda edição depois escolhe
lendo a ficha, sem abrir o vídeo. Tem o que eu reparo quando escolho B-roll no chat: o que acontece a cada segundo, o
melhor momento, notas comparáveis (luz, nitidez, assunto…), como o clipe se comporta em cada formato (tela cheia, canto,
tela dividida), onde está o texto queimado (para recortar em pixels), que falas ele ilustra e os pontos fracos.
Só biblioteca padrão (roda no Python do sistema e no da .venv)."""

def schema(estrito=True):
    """estrito=True: additionalProperties false em todo objeto (exigido pelo structured outputs do Claude)."""
    S, N, I, B = {"type": "string"}, {"type": "number"}, {"type": "integer"}, {"type": "boolean"}
    def arr(x): return {"type": "array", "items": x}
    def enum(*v): return {"type": "string", "enum": list(v)}
    def obj(**p):
        o = {"type": "object", "properties": p, "required": list(p)}
        if estrito: o["additionalProperties"] = False
        return o
    return obj(
        descricao=S, tags=arr(S), pessoa=S, ambiente=S,
        enquadramento=enum("close", "plano médio", "plano aberto", "variado"), angulo=enum("costas", "frente", "lado", "cima", "variado"),
        linha_do_tempo=arr(obj(ini=N, fim=N, o_que=S)),
        melhor_momento=obj(ini=N, fim=N, porque=S),
        notas=obj(luz=I, nitidez=I, movimento=I, assunto=I, natural=I),
        formatos=obj(cheia=obj(serve=B, obs=S), canto=obj(serve=B, lado_livre=enum("esq", "dir", "nenhum"), obs=S),
                     dividida=obj(serve=B, centro=N, obs=S),
                     card=obj(serve=B, x_ini=N, x_fim=N, y_ini=N, y_fim=N, trechos=arr(obj(ini=N, fim=N)), obs=S)),
        serve_para=arr(S),
        trechos_limpos=arr(obj(ini=N, fim=N, o_que=S)),
        texto_na_tela=arr(obj(ini=N, fim=N, onde=enum("topo", "meio", "base"), y_ini=N, y_fim=N, tamanho=enum("pequeno", "grande"), texto=S)),
        fala_para_camera=arr(obj(ini=N, fim=N)), cortes_de_cena=arr(N),
        posicao_assunto=enum("alto", "meio", "baixo"), marca_dagua=obj(tem=B, onde=S),
        nota=I, serve_como_broll=B, problemas=arr(S))

CORPO = """Descreva o clipe para ele ser usado como B-roll (imagem de apoio por cima da fala de outra pessoa, num anúncio
VERTICAL em português). Escreva como um editor exigente que vai escolher entre dezenas de clipes parecidos lendo só esta
ficha: o que você anotar é tudo o que ele vai saber. Tempos em segundos do clipe (precisão de 0,5 s).

- descricao: uma linha do que aparece, na ordem em que se vê ("mulher de costas agachando com elástico verde, sala clara").
- tags: 6 a 14 palavras-chave em português, minúsculas: assunto, exercício/ação, equipamento, lugar, pessoa, roupa, luz.
- angulo: de onde a câmera vê o assunto principal.
- linha_do_tempo: o clipe inteiro em trechos de 1 a 3 s, sem buracos, cada um com o que acontece ("subida do quadril,
  glúteo contraído, de costas"; "ajusta o elástico olhando para baixo").
- melhor_momento: o trecho de 1,5 a 4 s mais forte para a tela (ação no pico, ângulo mais bonito, sem texto) e porquê.
- notas de 0 a 10, comparáveis entre clipes: luz; nitidez (foco, compressão); movimento (câmera estável e ação legível);
  assunto (o quanto o que importa aparece bem — no fitness, o corpo e o resultado); natural (parece vida real, não
  propaganda nem pose forçada).
- formatos — como o clipe funciona em cada formato do editor:
  · cheia (cobre a tela toda): serve? obs curta.
  · canto (a pessoa que fala fica recortada num canto de baixo, cobrindo ~40% da largura): serve? lado_livre = o lado de
    baixo em que o assunto NÃO está (a pessoa recortada vai para lá sem tapar o assunto); "nenhum" se o assunto ocupa tudo.
  · dividida (aparece uma faixa horizontal do clipe com ~metade da altura dele, em cima; a pessoa embaixo): serve?
    centro = a altura (0 = topo, 1 = base) do centro da faixa que melhor mostra o assunto (ex.: glúteo no meio = 0,55).
  · card (um RECORTE do clipe dentro de um retângulo arredondado no topo da tela, com a pessoa atrás; pode ser quadrado,
    vertical ou deitado): serve? x_ini/x_fim/y_ini/y_fim = a região do quadro (frações de 0 a 1) que melhor enquadra o
    assunto. O card NÃO é saída para clipe com texto queimado: clipe com texto não entra na biblioteca, seja qual for o
    recorte. Não serve se o pedaço bom for menor que ~35% da largura ou da altura, ou se cortar o movimento pela metade.
    `trechos`: os intervalos em que ESSA REGIÃO fica limpa do começo ao fim (nenhum texto entra nela, ninguém fala para a
    câmera, sem corte de cena). Confira segundo a segundo: um texto que aparece só no fim já encerra o trecho. Se a
    região fica limpa o clipe inteiro, devolva um intervalo de 0 até a duração.
- serve_para: 3 a 8 falas ou ideias de anúncio que este clipe ilustra LITERALMENTE ("treino pesado na academia",
  "resultado no espelho", "ativação antes do treino", "cansaço de treinar sem resultado").
- trechos_limpos: intervalos contínuos de 1,5 s ou mais SEM ninguém falando para a câmera, SEM corte de cena, com luz boa
  e SEM nenhum texto na tela. Legenda, título, contador, seta, emoji desenhado, @ do criador — tudo isso é texto e tira o
  trecho, esteja onde estiver no quadro.
- texto_na_tela: todo texto queimado com ini/fim, onde (topo/meio/base), y_ini/y_fim (faixa da altura que ele ocupa, 0 =
  topo, 1 = base), tamanho e o que diz. ESTE CAMPO DERRUBA O CLIPE: quem tem texto não entra na biblioteca. Anote só o
  que você vê mesmo — não invente marca d'água que não está lá, e não deixe passar a que está.
- fala_para_camera: quando alguém olha para a câmera e fala. cortes_de_cena: segundos em que a cena muda.
- posicao_assunto: alto/meio/baixo. nota: 0 a 10 como B-roll em geral.
  serve_como_broll: false se o clipe não serve em NENHUM formato (slide de fotos, compilação com corte a cada segundo,
  gente falando o tempo todo, tela preta) ou se tem texto queimado em cima da imagem.
- problemas: os pontos fracos que um editor precisa saber ("rosto aparece aos 3 s", "câmera treme de 5 a 6 s", "logo de
  marca na roupa", "luz estourada no fim")."""

INTRO_VIDEO = "Você é o olho de um editor de vídeo. Assista este clipe INTEIRO, segundo a segundo.\n\n"
INTRO_FOLHA = ("Você é o olho de um editor de vídeo. Você recebe a folha de contato de UM clipe: quadros em ordem (da esquerda "
               "para a direita, de cima para baixo) com o segundo de cada um embaixo. Deduza o movimento entre os quadros; um "
               "trecho limpo vai do primeiro ao último quadro limpo seguidos.\n\n")

def instrucoes(video=True): return (INTRO_VIDEO if video else INTRO_FOLHA) + CORPO

def ajustar(e, dur):
    """Limpa a ficha: tempos dentro do clipe, trechos curtos fora, tags minúsculas, centro da dividida numa faixa possível."""
    def dentro(t): return 0 <= float(t.get("ini", -1)) < float(t.get("fim", -1)) <= dur + 0.5
    e["trechos_limpos"] = [t for t in e.get("trechos_limpos", []) if dentro(t) and t["fim"] - t["ini"] >= 1.2]
    for t in e["trechos_limpos"]: t["fim"] = round(min(float(t["fim"]), dur), 2)
    e["linha_do_tempo"] = [t for t in e.get("linha_do_tempo", []) if dentro(t)]
    e["tags"] = sorted({str(t).strip().lower() for t in e.get("tags", []) if str(t).strip()})
    for t in e.get("texto_na_tela", []):
        a, b = sorted((min(max(float(t.get("y_ini", 0)), 0.0), 1.0), min(max(float(t.get("y_fim", 1)), 0.0), 1.0)))
        t["y_ini"], t["y_fim"] = round(a, 3), round(b, 3)
    F = e.get("formatos") or {}
    dv = F.get("dividida")
    if dv and dv.get("centro") is not None: dv["centro"] = round(min(max(float(dv["centro"]), 0.25), 0.75), 3)
    cd = F.get("card")
    if cd and cd.get("serve"):
        cd["trechos"] = [t for t in (cd.get("trechos") or []) if dentro(t) and t["fim"] - t["ini"] >= 1.2]
        x0, x1 = sorted((min(max(float(cd.get("x_ini", 0)), 0), 1), min(max(float(cd.get("x_fim", 1)), 0), 1)))
        y0, y1 = sorted((min(max(float(cd.get("y_ini", 0)), 0), 1), min(max(float(cd.get("y_fim", 1)), 0), 1)))
        if x1 - x0 < 0.3 or y1 - y0 < 0.3: cd["serve"] = False     # pedaço pequeno demais não vira card
        cd.update(x_ini=round(x0, 3), x_fim=round(x1, 3), y_ini=round(y0, 3), y_fim=round(y1, 3))
    return e

def resumo(x):
    """A ficha numa linha, para o Claude escolher sem abrir o vídeo (ficha antiga, só com o básico, também serve)."""
    e = x.get("estudo") or {}; forma = "horizontal" if (x.get("w") or 0) > (x.get("h") or 0) else "vertical"
    base = f"{x['id']} · {x.get('dur', 0):.1f}s · {forma}"
    if not e: return f"{base} · (sem ficha) {x.get('descricao') or x.get('titulo', '')} · busca: {x.get('termo', '')}"
    limpos = ", ".join(f"{t['ini']:.1f}–{t['fim']:.1f}s" for t in e.get("trechos_limpos", [])[:5]) or "nenhum"
    txt = "; ".join(f"{t['ini']:.0f}–{t['fim']:.0f}s {t['onde']}" + (f" ({t['y_ini']:.0%}–{t['y_fim']:.0%})" if "y_ini" in t else "")
                    for t in e.get("texto_na_tela", [])[:4]) or "sem texto"
    L = [base, f"nota {e.get('nota', x.get('nota', '?'))}", x.get("descricao", "")]
    n = e.get("notas")
    if n: L.append("luz {luz} · nitidez {nitidez} · movimento {movimento} · assunto {assunto} · natural {natural}".format(**{k: n.get(k, "?") for k in ("luz", "nitidez", "movimento", "assunto", "natural")}))
    if e.get("angulo"): L.append(f"de {e['angulo']}")
    m = e.get("melhor_momento")
    if m: L.append(f"melhor {m['ini']:.1f}–{m['fim']:.1f}s ({str(m.get('porque', ''))[:60]})")
    L.append(f"limpos {limpos}"); L.append(f"texto: {txt}")
    fl = "; ".join(f"{t['ini']:.0f}–{t['fim']:.0f}s" for t in e.get("fala_para_camera", [])[:3])
    if fl: L.append(f"fala p/ câmera {fl}")
    f = e.get("formatos")
    if f:
        fs = []
        if f.get("cheia", {}).get("serve"): fs.append("cheia")
        if f.get("canto", {}).get("serve"): fs.append(f"canto (lado livre {f['canto'].get('lado_livre', '?')})")
        if f.get("dividida", {}).get("serve"): fs.append(f"dividida (centro {f['dividida'].get('centro', 0.5):.2f})")
        cd = f.get("card") or {}
        if cd.get("serve"):
            tr = ", ".join(f"{t['ini']:.1f}–{t['fim']:.1f}s" for t in (cd.get("trechos") or [])[:3])
            fs.append(f"card (recorte {cd.get('x_ini', 0):.0%}–{cd.get('x_fim', 1):.0%} × {cd.get('y_ini', 0):.0%}–{cd.get('y_fim', 1):.0%}"
                      + (f", limpo em {tr}" if tr else "") + ")")
        L.append("formatos: " + (", ".join(fs) or "nenhum"))
    else: L.append(f"assunto {e.get('posicao_assunto', 'meio')}")
    if e.get("serve_para"): L.append("ilustra: " + "; ".join(e["serve_para"][:5]))
    if e.get("problemas"): L.append("fraco: " + "; ".join(e["problemas"][:3]))
    L.append("tags: " + ", ".join(x.get("tags", [])[:10]))
    return " · ".join(str(p) for p in L if p)
