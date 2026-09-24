"""Mede um vídeo de referência com ffmpeg/numpy/Pillow — só números, sem IA nenhuma no meio.

Porta para dentro do repo a mesma medição de `~/.claude/skills/edicao-dinamica/scripts/estudar.py`, que já
criou os templates Ultradinâmico e Simples Dinâmico à mão. A regra de lá vale aqui: "meça antes de replicar,
nunca confie em relatório de IA" — um relatório do Gemini já acertou todos os timestamps dos cortes e errou a
duração (disse "2,5 a 4s por inserção" quando a mediana real era 6,4s). Por isso a skill `criar-template` usa
esses números como fato, e `lib/gemini.py:estudar_referencia_anuncio` nunca pede pro Gemini estimar
ritmo, posição ou duração por conta própria — só julgamento qualitativo (gancho, texto na tela, vibe).
"""
import os
import statistics
import subprocess
import tempfile

_PATH = os.environ.get("PATH", "/usr/bin:/bin")


def _ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", *args], check=False, env=dict(os.environ, PATH=_PATH))


def duracao(arq):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", arq],
                       capture_output=True, text=True, env=dict(os.environ, PATH=_PATH)).stdout.strip()
    try: return float(r)
    except ValueError: return 0.0


def _cortes(arq, limiar=0.08):
    """Instantes de troca de imagem, agrupados: transição gradual (flash, whoosh, dissolve) dispara o
    detector várias vezes seguidas — sem agrupar, um vídeo de 15 blocos parece ter 48 cortes."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", arq, "-vf", f"select='gt(scene,{limiar})',metadata=print:file=-",
                        "-an", "-f", "null", "-"], capture_output=True, text=True, env=dict(os.environ, PATH=_PATH)).stdout
    ts = [float(x.split(":")[1]) for x in r.split() if x.startswith("pts_time:")]
    grupos = []
    for t in ts:
        if grupos and t - grupos[-1][-1] < 0.6: grupos[-1].append(t)
        else: grupos.append([t])
    return [g[0] for g in grupos], len(ts)


def medir_ritmo(arq):
    """Blocos reais (não detecções brutas) e a duração de cada um — a mediana é o que separa um estilo do
    outro (ex.: Ultradinâmico Criativo ~5s de mediana, Simples Dinâmico 6,4s)."""
    dur = duracao(arq)
    blocos, brutos = _cortes(arq)
    limites = [0.0] + blocos + [dur]
    duracoes = [limites[i + 1] - limites[i] for i in range(len(limites) - 1)]
    if not duracoes: return dict(duracao=round(dur, 1), deteccoes_brutas=brutos, blocos=0)
    return dict(
        duracao=round(dur, 1),
        deteccoes_brutas=brutos,
        blocos=len(duracoes),
        trocas_por_minuto=round(len(blocos) / dur * 60, 1) if dur else 0.0,
        mediana_s=round(statistics.median(duracoes), 2),
        minimo_s=round(min(duracoes), 2),
        maximo_s=round(max(duracoes), 2),
        inicios_s=[round(l, 2) for l in limites[:-1]],
    )


def medir_legenda(arq, passo=1.5):
    """Posição e tamanho da legenda pela COR DE DESTAQUE (karaokê) — nunca por pixel branco: roupa clara,
    parede e mesa também são brancas e destroem a medição. Sem cor de destaque, não dá pra medir por aqui
    (achou=False) — nesse caso a posição da legenda é julgamento visual, não medição."""
    import numpy as np
    from PIL import Image
    dur = duracao(arq)
    achados = []
    with tempfile.TemporaryDirectory(prefix="atlas-estudo-legenda-") as tmp:
        alvo = os.path.join(tmp, "q.png")
        t = passo
        while t < dur:
            _ff("-ss", f"{t:.2f}", "-i", arq, "-frames:v", "1", alvo, "-y")
            if os.path.exists(alvo):
                im = np.asarray(Image.open(alvo).convert("RGB")).astype(int); H, W = im.shape[:2]
                sat = im.max(2) - im.min(2)
                forte = (sat > 70) & (im.max(2) > 150)
                lin = forte.sum(1)
                if lin.max() >= W * 0.012:
                    k = int(np.argmax(lin)); corte = max(W * 0.006, lin[k] * 0.25)
                    a = k
                    while a > 0 and lin[a - 1] > corte: a -= 1
                    b = k
                    while b < H - 1 and lin[b + 1] > corte: b += 1
                    if 0.008 < (b - a) / H < 0.12: achados.append((t, (a + b) / 2 / H, (b - a) / H))
                os.remove(alvo)
            t += passo
    if not achados: return dict(achou=False)
    centros = [c for _, c, _ in achados]; mediana = statistics.median(centros)
    bons = [(t, c, h) for t, c, h in achados if abs(c - mediana) < 0.08]
    if not bons: return dict(achou=False)
    desvio = statistics.pstdev([c for _, c, _ in bons]) if len(bons) > 1 else 0.0
    altura = statistics.median([h for _, _, h in bons])
    return dict(achou=True, quadros_com_legenda=len(bons), quadros_medidos=len(achados),
               centro_pct_altura=round(100 * mediana, 1), fixa=desvio < 0.03, desvio_pct=round(100 * desvio, 1),
               altura_pct_tela=round(100 * altura, 2))


def medir_som(arq):
    """Ataques por banda e trilha de fundo — não diz QUE som é, diz onde há som, de que tipo (grave/agudo) e
    se casa com os cortes. Não controla sfx_regras.py (a IA nunca escreve esse arquivo — ver
    lib/template_escrita.py), mas ajuda a julgar a energia geral da edição (zoom, transição, flash)."""
    import numpy as np
    import wave
    with tempfile.TemporaryDirectory(prefix="atlas-estudo-som-") as tmp:
        w16 = os.path.join(tmp, "a.wav")
        _ff("-i", arq, "-vn", "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", w16, "-y")
        if not os.path.exists(w16): return dict(medido=False)
        w = wave.open(w16); sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    if len(x) < 2048: return dict(medido=False)
    N, H = 1024, 256
    esp = np.array([np.abs(np.fft.rfft(x[i:i + N] * np.hanning(N))) for i in range(0, len(x) - N, H)])
    fr = np.fft.rfftfreq(N, 1 / sr); t = np.arange(len(esp)) * H / sr

    def banda(a, b): return esp[:, (fr >= a) & (fr < b)].mean(1)

    def picos(e, k=3.5):
        d = np.diff(e, prepend=e[0]); lim = d.mean() + k * d.std(); out = []
        for i in range(1, len(d) - 1):
            if d[i] > lim and d[i] >= d[i - 1] and d[i] >= d[i + 1]:
                if out and t[i] - out[-1] < 0.12: continue
                out.append(round(float(t[i]), 2))
        return out

    graves, agudos = picos(banda(20, 150)), picos(banda(6000, 16000))
    blocos, _ = _cortes(arq)
    casamentos = {}
    for nome, p in (("grave", graves), ("agudo", agudos)):
        casa = [c for c in blocos if any(abs(s - c) <= 0.35 for s in p)]
        casamentos[nome] = dict(casa=len(casa), de=len(blocos), pct=round(100 * len(casa) / max(1, len(blocos))))
    secos = [c for c in blocos if not any(abs(s - c) <= 0.35 for s in graves + agudos)]
    j = int(0.05 * sr)
    db = 20 * np.log10(np.array([np.sqrt((x[i:i + j] ** 2).mean()) + 1e-9 for i in range(0, len(x) - j, j)])) if j else np.array([0.0])
    fundo = float(np.percentile(db, 10))
    env = banda(40, 160); env = env - env.mean()
    ac = np.correlate(env, env, "full")[len(env) - 1:]
    k0, k1 = int(0.25 / (H / sr)), int(1.5 / (H / sr))
    batida = dict(bpm=0.0, forca=0.0)
    if k1 > k0 and len(ac) > k1:
        k = k0 + int(np.argmax(ac[k0:k1])); forca = float(ac[k] / ac[0]) if ac[0] else 0.0
        batida = dict(bpm=round(60 / (k * H / sr), 0) if k else 0.0, forca=round(forca, 2))
    return dict(medido=True, ataques_graves=len(graves), ataques_agudos=len(agudos), casamento_com_cortes=casamentos,
               trocas_sem_som=dict(quantas=len(secos), de=len(blocos)), fundo_dbfs=round(fundo, 1),
               tem_ambiencia_ou_trilha=fundo > -50, batida_regular=batida["forca"] > 0.3, **{"batida": batida})


def extrair_quadros(arq, destino, maximo=8):
    """Um quadro do MEIO de cada bloco de ritmo (nunca um tempo qualquer — cai em quadro escuro, transição ou
    corte pela metade). Até `maximo` quadros, amostrados uniformemente pelo vídeo inteiro (não só o começo) —
    o bastante pra dar uma noção do vídeo inteiro sem virar uma mensagem gigante de imagens."""
    os.makedirs(destino, exist_ok=True)
    r = medir_ritmo(arq)
    limites = (r.get("inicios_s") or [0.0]) + [r["duracao"]]
    meios = [(limites[i] + limites[i + 1]) / 2 for i in range(len(limites) - 1)]
    if not meios: meios = [r["duracao"] / 2] if r["duracao"] else [0.0]
    if len(meios) > maximo > 1:
        passo = (len(meios) - 1) / (maximo - 1)
        meios = [meios[round(i * passo)] for i in range(maximo)]
    elif len(meios) > maximo:
        meios = meios[:maximo]
    caminhos = []
    for i, t in enumerate(meios):
        dest = os.path.join(destino, f"quadro{i:02d}.jpg")
        _ff("-ss", f"{t:.2f}", "-i", arq, "-frames:v", "1", "-vf", "scale=720:-2", dest, "-y")
        if os.path.exists(dest): caminhos.append(dest)
    return caminhos


def estudar(arq, destino_quadros, maximo_quadros=8):
    """Tudo junto: ritmo + legenda + som + quadros representativos — o pacote que a skill `criar-template`
    usa como fato ao estudar um vídeo de referência para propor um template novo."""
    return dict(ritmo=medir_ritmo(arq), legenda=medir_legenda(arq), som=medir_som(arq),
               quadros=extrair_quadros(arq, destino_quadros, maximo_quadros))
