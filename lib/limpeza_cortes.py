"""Conservative cleanup: retain speech unless a removal is proven."""
import math
import re
import unicodedata

TIPOS = ("retomada", "duplicata", "gaguejo", "operacional")
MAX_PALAVRAS = 60
MAX_DURACAO = 35.0
MAX_GAP = 30.0

OPERACIONAIS = (
    "corta", "corta ai", "pode cortar", "pode cortar ai",
    "errei", "errei aqui", "vou repetir", "vou repetir essa frase",
    "deixa eu repetir", "vou falar de novo", "vamos gravar de novo",
    "vou comecar de novo", "vamos comecar de novo",
    "pode parar de gravar", "para a gravacao", "pausa a gravacao",
    "corta essa gravacao", "corta essa parte", "corta isso",
    "gravando", "ta gravando", "esta gravando",
)
NEGACOES = {"nao", "nunca", "nem", "jamais", "sem"}
NUMEROS = {"zero", "um", "uma", "dois", "duas", "tres", "quatro", "cinco",
           "seis", "sete", "oito", "nove", "dez", "onze", "doze", "vinte",
           "trinta", "quarenta", "cinquenta", "cem", "mil", "milhao"}
ENFASE = {"repito", "enfatizo", "reafirmo", "reforco"}
VAZIAS = set("a o as os de do da dos das e em no na nos nas por para com que se voce eu ele ela isso um uma ao aos".split())


def tokens(texto):
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z0-9]+", texto)


def _numero(x):
    return type(x) in (int, float) and math.isfinite(x)


def _palavras(W):
    if not W:
        raise ValueError("Transcrição vazia: não é seguro gerar cortes.")
    anterior = -1.0
    for w in W:
        if not isinstance(w, dict) or not isinstance(w.get("w"), str) or not w["w"].strip():
            raise ValueError("Palavra inválida na transcrição.")
        a, b = w.get("t"), w.get("e")
        # a == b acontece de verdade: interjeições muito curtas ("é", "mas") às vezes saem com
        # duração zero depois do arredondamento do alinhamento forçado. Só duração negativa ou
        # ordem quebrada indicam transcrição corrompida.
        if not _numero(a) or not _numero(b) or not 0 <= a <= b or a < anterior:
            raise ValueError("Tempos inválidos ou fora de ordem na transcrição.")
        anterior = a


def _faixa(a, b, W):
    return type(a) is int and type(b) is int and 0 <= a <= b < len(W)


def _sobrepoe(a, b, c, d):
    return a <= d and c <= b


def _texto(W, a, b):
    return " ".join(w["w"] for w in W[a:b + 1])


def _protegidos(ws):
    return [w for w in ws if w in NEGACOES or w in NUMEROS or any(c.isdigit() for c in w)]


def _operacional(ws):
    # Exact phrases only. No broad keywords such as "mecanismo" or "erro".
    if len(ws) == 2 and ws[0] in ("lead", "gancho", "hook") and (ws[1].isdigit() or ws[1] in NUMEROS):
        return True
    phrases = [tokens(x) for x in OPERACIONAIS]
    reachable = {0}
    for i in range(len(ws)):
        if i not in reachable:
            continue
        for phrase in phrases:
            if ws[i:i + len(phrase)] == phrase:
                reachable.add(i + len(phrase))
    return bool(ws) and len(ws) in reachable


def _isolado(W, a, b):
    antes = a == 0 or W[a]["t"] - W[a - 1]["e"] >= .25
    depois = b == len(W) - 1 or W[b + 1]["t"] - W[b]["e"] >= .25
    return antes and depois


def _subsequencia(pequeno, grande):
    indices, cursor = [], 0
    for palavra in pequeno:
        while cursor < len(grande) and grande[cursor] != palavra:
            cursor += 1
        if cursor == len(grande):
            return None
        indices.append(cursor)
        cursor += 1
    return indices


def _evidencia_retomada(W, a, b, c, d, tipo, confianca):
    original, substituto = tokens(_texto(W, a, b)), tokens(_texto(W, c, d))
    if not original or not substituto:
        return None, "texto sem correspondência verificável"
    if _sobrepoe(a, b, c, d):
        return None, "a remoção sobrepõe sua tomada substituta"
    if b < c:
        gap = W[c]["t"] - W[b]["e"]
        meio = tokens(_texto(W, b + 1, c - 1)) if c > b + 1 else []
    else:
        gap = W[a]["t"] - W[d]["e"]
        meio = tokens(_texto(W, d + 1, a - 1)) if a > d + 1 else []
    if not 0 <= gap <= MAX_GAP:
        return None, "tomadas distantes ou tempos sobrepostos"
    contexto = tokens(_texto(W, max(0, min(a, c) - 6), min(len(W) - 1, max(b, d) + 4)))
    if set(contexto) & ENFASE:
        return None, "há indicação de repetição enfática; fala preservada"
    if tipo == "gaguejo":
        # Only visibly unfinished syllables, never "muito muito" or "não não".
        bruto = _texto(W, a, b).rstrip()
        incompleto = bruto.endswith(("-", "…", "..."))
        if (confianca >= .995 and len(original) == 1 and len(original[0]) >= 2 and
                len(substituto) >= 1 and substituto[0].startswith(original[0]) and
                substituto[0] != original[0] and b + 1 == c and gap <= .6 and incompleto):
            return dict(correspondencia="silaba_incompleta", gap=round(gap, 3)), None
        return None, "gaguejo não comprovado; repetição curta pode ser ênfase"
    if original == substituto:
        # Long, immediately repeated identical take; either earlier/later may be retained.
        conteudo = sum(t not in VAZIAS for t in original)
        adjacente = not meio and gap <= 3
        marcador = _operacional(meio) if meio else False
        hesitacao = _texto(W, a, b).rstrip().endswith(("…", "...", "-"))
        if len(original) < 8 or conteudo < 5 or confianca < .995:
            return None, "duplicata curta ou confiança insuficiente"
        if not (adjacente or marcador or hesitacao):
            return None, "duplicata sem contexto claro de retomada"
        return dict(correspondencia="duplicata_exata", tokens=len(original), gap=round(gap, 3)), None
    if tipo != "retomada":
        return None, "duplicata não é idêntica à tomada mantida"
    alinhamento = _subsequencia(original, substituto)
    if alinhamento is None or len(original) < 3:
        return None, "parte da fala removida não consta da tomada mantida"
    if _protegidos(original) != _protegidos(substituto[:alinhamento[-1] + 1]):
        return None, "número ou negação diverge no conteúdo substituído"
    if len(substituto) <= len(original):
        return None, "a tomada substituta não completa o fragmento"
    prefixo = alinhamento == list(range(len(original)))
    inseridos = alinhamento[-1] + 1 - len(original)
    if not prefixo and (alinhamento[0] != 0 or inseridos > 2):
        return None, "correspondência parcial excessiva ou início diferente"
    if meio and not _operacional(meio):
        return None, "há fala intermediária ou retomada distante"
    return dict(correspondencia="prefixo" if prefixo else "subsequencia", alinhamento=alinhamento,
                gap=round(gap, 3), tokens=len(original)), None

def _segura(proposta):
    if not isinstance(proposta, dict):
        return {"valor": repr(proposta)[:300]}
    out = {}
    for chave in ("de", "ate", "tipo", "substituto_de", "substituto_ate", "motivo", "confianca"):
        value = proposta.get(chave)
        if value is None or type(value) in (str, int, bool) or _numero(value):
            out[chave] = value[:500] if isinstance(value, str) else value
        else:
            out[chave] = repr(value)[:300]
    return out


def validar_remocoes(W, propostas, *, max_fracao=.20):
    """Approve only evidenced removals; rejected or absent proposals retain words.

    Both directions of replacement are supported: the earlier take may be better.
    Conflicting proposals are rejected together, independently of proposal order.
    The 20% cap rejects the proposed speech cleanup as a whole, never truncates it.
    """
    _palavras(W)
    if not _numero(max_fracao) or not 0 <= max_fracao <= .20:
        raise ValueError("O limite de remoção de fala deve estar entre zero e 20%.")
    rejeitadas, candidatas = [], []
    def rejeitar(p, motivo):
        rejeitadas.append(dict(proposta=_segura(p), motivo=motivo))
    if not isinstance(propostas, list):
        rejeitar(propostas, "resposta sem lista de remoções; toda a fala foi preservada")
        propostas = []
    if len(propostas) > 500:
        rejeitar({}, "propostas em excesso; toda a fala foi preservada")
        propostas = []
    for raw in propostas:
        p = _segura(raw)
        if not isinstance(raw, dict):
            rejeitar(raw, "remoção inválida"); continue
        a, b = raw.get("de"), raw.get("ate")
        c, d = raw.get("substituto_de"), raw.get("substituto_ate")
        tipo, conf = raw.get("tipo"), raw.get("confianca")
        if not _faixa(a, b, W):
            rejeitar(p, "índices inválidos; não foram corrigidos silenciosamente"); continue
        if tipo not in TIPOS or not _numero(conf) or not .98 <= conf <= 1:
            rejeitar(p, "tipo ou confiança insuficiente"); continue
        if not isinstance(raw.get("motivo"), str) or not raw["motivo"].strip():
            rejeitar(p, "remoção sem justificativa"); continue
        if b - a + 1 > MAX_PALAVRAS or W[b]["e"] - W[a]["t"] > MAX_DURACAO:
            rejeitar(p, "remoção longa demais; mantida para revisão"); continue
        # Never cut a neighbouring word because ASR timestamps overlap.
        if ((a and W[a - 1]["e"] > W[a]["t"]) or
                (b + 1 < len(W) and W[b]["e"] > W[b + 1]["t"])):
            rejeitar(p, "borda da remoção invade palavra vizinha"); continue
        if tipo == "operacional":
            if c is not None or d is not None:
                rejeitar(p, "comando operacional não deve apontar substituto"); continue
            if conf < .995 or b - a + 1 > 16 or not _operacional(tokens(_texto(W, a, b))) or not _isolado(W, a, b):
                rejeitar(p, "comando não consta da lista explícita ou está misturado à fala"); continue
            evidencia = dict(correspondencia="comando_explicito", texto=_texto(W, a, b))
        else:
            if not _faixa(c, d, W):
                rejeitar(p, "tomada substituta ausente ou inválida"); continue
            evidencia, erro = _evidencia_retomada(W, a, b, c, d, tipo, conf)
            if erro:
                rejeitar(p, erro); continue
        p.update(de=a, ate=b, substituto_de=c, substituto_ate=d, tipo=tipo,
                 confianca=conf, motivo=raw["motivo"][:500], evidencia=evidencia)
        candidatas.append(p)
    conflitos = set()
    for i, a in enumerate(candidatas):
        for j in range(i + 1, len(candidatas)):
            b = candidatas[j]
            if (_sobrepoe(a["de"], a["ate"], b["de"], b["ate"]) or
                    (a["substituto_de"] is not None and _sobrepoe(a["substituto_de"], a["substituto_ate"], b["de"], b["ate"])) or
                    (b["substituto_de"] is not None and _sobrepoe(b["substituto_de"], b["substituto_ate"], a["de"], a["ate"]))):
                conflitos.update((i, j))
    aprovadas = []
    for i, p in enumerate(candidatas):
        if i in conflitos:
            rejeitar(p, "conflito entre remoções ou tomada substituta protegida; ambas preservadas")
        else:
            aprovadas.append(p)
    removidas = {i for p in aprovadas for i in range(p["de"], p["ate"] + 1)}
    if len(removidas) > math.floor(len(W) * max_fracao):
        for p in aprovadas:
            rejeitar(p, "limite total de 20% da fala excedido; limpeza de fala preservada para revisão")
        aprovadas, removidas = [], set()
    aprovadas.sort(key=lambda p: (p["de"], p["ate"]))
    trechos, inicio = [], None
    for k in range(len(W) + 1):
        if k < len(W) and k not in removidas:
            if inicio is None:
                inicio = k
        elif inicio is not None:
            trechos.append(dict(de=inicio, ate=k - 1, versoes=["A"]))
            inicio = None
    mantidas = {i for t in trechos for i in range(t["de"], t["ate"] + 1)}
    if not trechos or mantidas & removidas or mantidas | removidas != set(range(len(W))):
        raise ValueError("A auditoria de cobertura da fala falhou; nenhum corte deve ser aplicado.")
    for p in aprovadas:
        if p["substituto_de"] is not None and not set(range(p["substituto_de"], p["substituto_ate"] + 1)) <= mantidas:
            raise ValueError("Uma tomada substituta deixou de ser preservada.")
    avisos = [f"Palavras {p['proposta'].get('de', '?')}–{p['proposta'].get('ate', '?')}: {p['motivo']}" for p in rejeitadas]
    return dict(politica="limpeza_conservadora_v1", remocoes=aprovadas, rejeitadas=rejeitadas,
                avisos=avisos, trechos=trechos, palavras_total=len(W),
                palavras_removidas=len(removidas), palavras_mantidas=len(mantidas),
                limite_fracao=max_fracao, cobertura_verificada=True)


def criar_documento(W, resultado, duracao):
    """Generate one version from the full audio, subtracting approved removals only.

    Silence/noise removal belongs to the acoustic fala_vad stage, not to the LLM.
    """
    _palavras(W)
    if not _numero(duracao) or duracao <= 0 or W[-1]["e"] > duracao + .1:
        raise ValueError("Duração do áudio incompatível com a transcrição.")
    # Revalidate the evidence instead of trusting an externally forged result.
    validado = validar_remocoes(W, resultado.get("remocoes", []),
                               max_fracao=resultado.get("limite_fracao", .20))
    if [(p["de"], p["ate"]) for p in validado["remocoes"]] != [(p["de"], p["ate"]) for p in resultado.get("remocoes", [])]:
        raise ValueError("Resultado de remoções alterado ou inconsistente.")
    faixas, cursor = [], 0.0
    for p in validado["remocoes"]:
        a, b = W[p["de"]]["t"], W[p["ate"]]["e"]
        if a > cursor:
            faixas.append([cursor, a])
        cursor = b
    if cursor < duracao:
        faixas.append([cursor, duracao])
    if not faixas:
        raise ValueError("A limpeza removeria o áudio inteiro.")
    # Account for every retained ASR word. VAD audits its own acoustic boundaries.
    for t in validado["trechos"]:
        for w in W[t["de"]:t["ate"] + 1]:
            if not any(a <= w["t"] and b >= min(w["e"], duracao) for a, b in faixas):
                raise ValueError("Faixa final cortaria uma palavra preservada.")
    return dict(versoes=[dict(id="A", nome="Versão única", secoes=[], faixas=faixas)],
                frases=[], descartes=resultado["remocoes"],
                cortes_cfg=dict(modo="fala_vad", pausa_min=.16, respiro=1 / 30, cauda=1 / 30),
                ia=dict(trechos=validado["trechos"], limpeza=resultado),
                observacoes=list(resultado.get("avisos", [])))
