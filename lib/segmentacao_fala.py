"""Segmentação acústica conservadora: remove só ausência de fala concordante.

Não escolhe takes, não altera transcrição e não chama provedores. Faixas são o
complemento cronológico de exclusões aprovadas. Silero embarcado no faster-whisper
mantém fala mesmo omitida pelo ASR; palavras curtas preservam discordâncias/baixa voz.
Palavras com timestamps >1s não bloqueiam pausas internas evidentes de vários segundos.
"""
import math
import wave


def unir(intervalos, gap=0.0):
    out = []
    for a, b in sorted((float(a), float(b)) for a, b in intervalos if b > a):
        if out and a <= out[-1][1] + gap + 1e-9: out[-1][1] = max(out[-1][1], b)
        else: out.append([a, b])
    return out


def ler_audio(wav16k):
    import numpy as np
    with wave.open(str(wav16k), 'rb') as f:
        if (f.getframerate(), f.getnchannels(), f.getsampwidth()) != (16000, 1, 2):
            raise ValueError('VAD requer WAV mono PCM16 a 16 kHz')
        return np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def detectar_fala(audio):
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    resultado = []
    for limiar in (0.35, 0.5):
        partes = get_speech_timestamps(audio, VadOptions(threshold=limiar,
            min_speech_duration_ms=0, min_silence_duration_ms=160, speech_pad_ms=0), sampling_rate=16000)
        resultado.append([[p['start'] / 16000, p['end'] / 16000] for p in partes])
    return resultado


def confianca_refino(p):
    value=p.get('probabilidade')
    if isinstance(value,bool) or not isinstance(value,(int,float)):
        return None
    return float(value) if math.isfinite(value) and 0<=value<=1 else None


def protecoes_palavras(palavras, voz, margem=.08):
    """Protege 80ms antes/depois da voz; palavra sem VAD fica inteira/incerta.

    ASR curto sobreposto à voz não é prova de que sua antecedência inteira tem
    fala. Limitá-lo pela acústica evita os600ms artificiais, mantendo margem
    para consoantes fracas. Ausência TOTAL de VAD não autoriza apagar a palavra.
    """
    if not math.isfinite(margem) or not 0<=margem<=.12:
        raise ValueError('Margem acústica deve estar entre0 e120ms')
    protecoes=[];incertas=[]
    voz=unir(voz)
    for p in palavras or ():
        a,b=float(p.get('t',p.get('start',0))),float(p.get('e',p.get('end',0)))
        if not all(math.isfinite(v) for v in (a,b)) or not 0<b-a<=1:
            continue
        sobrepostas=[[c,d] for c,d in voz if min(b,d)>max(a,c)]
        if not sobrepostas:
            # Uma hipótese nova e fraca do refino não pode criar uma ilha de
            # silêncio protegido. Originais permanecem conservadoras.
            confianca=confianca_refino(p)
            if p.get('_refino_adicional') and (confianca is None or confianca<.6):
                continue
            protecoes.append([a,b])
            incertas.append(dict(p,motivo='palavra inteira fora dos dois detectores de voz'))
        else:
            protecoes.extend([[max(a,c-margem),min(b,d+margem)] for c,d in sobrepostas])
    return unir(protecoes),incertas


def planejar_segmentos(duracao, faixas, fala_baixa, fala_alta, palavras=(), *, pausa_min=0.16,
                        respiro=1/30, cauda=1/30, fps=30, relatorio=None):
    """Planeja sem I/O; só retira silêncio de ambos limiares e fora de palavra plausível.

    Intervalos curtos do ASR são proteções conservadoras, não prova de ruído/fala.
    Um timestamp esticado não é tratado como 12 segundos de fala contínua.
    Padding é de um frame e fica dentro das faixas aprovadas. Todas as bordas
    arredondam para fora para proteger fala válida adjacente; pode retornar
    menos de um frame da fronteira removida, nunca uma palavra/take inteiro.
    """
    params = (duracao, pausa_min, respiro, cauda, fps)
    if not all(math.isfinite(float(v)) for v in params) or duracao <= 0 or fps <= 0:
        raise ValueError('Duração/parâmetros VAD inválidos')
    if pausa_min < .15 or not 0 <= respiro <= .12 or not 0 <= cauda <= .12:
        raise ValueError('VAD requer pausa mínima de 150 ms e padding entre 0 e 120 ms')
    limitadas = []
    for a, b in faixas:
        a, b = float(a), float(b)
        if not all(math.isfinite(v) for v in (a, b)) or b <= a: raise ValueError('Faixa inválida')
        a, b = max(0., a), min(float(duracao), b)
        if b > a: limitadas.append([a, b])
    limitadas = unir(limitadas)
    voz=unir([*fala_baixa,*fala_alta])
    protecoes,incertas=protecoes_palavras(palavras,voz)
    if relatorio is not None:
        relatorio['margem_protecao_asr']=.08
        relatorio['palavras_sem_vad']=[p for p in incertas if any(
            min(float(p.get('e',p.get('end',0))),hi)>
            max(float(p.get('t',p.get('start',0))),lo) for lo,hi in limitadas)]
    # Toda voz detectada permanece; ASR só amplia80ms onde sobrepõe voz.
    fala = unir([*voz, *protecoes], gap=pausa_min)
    out = []
    for lo, hi in limitadas:
        first_frame = math.floor(lo*fps + 1e-7)
        last_frame = math.ceil(hi*fps - 1e-7)
        local = []
        for a, b in fala:
            if b <= lo or a >= hi: continue
            a, b = max(lo, a), min(hi, b)
            start = max(0., max(first_frame, math.floor((a-respiro)*fps + 1e-7)) / fps)
            end = min(float(duracao), min(last_frame, math.ceil((b+cauda)*fps - 1e-7)) / fps)
            if end > start: local.append([start, end])
        # Não criar um jump cut que removeria menos de um frame.
        out.extend(unir(local, gap=1/fps - 1e-7))
    # Arredondamento externo de faixas vizinhas pode sobrepor um frame.
    return unir(out)


def segmentos_fala_vad(wav16k, faixas, palavras=None, pausa_min=.16, respiro=1/30, cauda=1/30, fps=30):
    audio = ler_audio(wav16k)
    baixo, alto = detectar_fala(audio)
    return planejar_segmentos(len(audio)/16000, faixas, baixo, alto, palavras,
        pausa_min=pausa_min, respiro=respiro, cauda=cauda, fps=fps)


# Refino independente: não substitui nem reescreve fonte/whisper.json.
def janelas_refino(duracao, palavras, fala_baixa, fala_alta, contexto=1.5):
    suspeitas = []
    normais = []
    for p in palavras:
        a, b = float(p.get('t', p.get('start', 0))), float(p.get('e', p.get('end', 0)))
        if b-a > 1.0: suspeitas.append([a, b])
        elif b > a: normais.append([a, b])
    cobertura = unir(normais, gap=.15)
    for a, b in unir([*fala_baixa, *fala_alta]):
        cursor = a
        for c, d in cobertura:
            if d <= cursor: continue
            if c >= b: break
            if c-cursor > .25: suspeitas.append([cursor, min(c, b)])
            cursor = max(cursor, d)
        if b-cursor > .25: suspeitas.append([cursor, b])
    # ASR também antecipa palavras curtas em pausas longas. Refinar só onde
    # ambos VADs rejeitam voz por >500ms e uma proteção original ocupa essa pausa.
    voz = unir([*fala_baixa, *fala_alta])
    cursor = 0.
    silencios = []
    for a, b in voz:
        if a-cursor > .5: silencios.append([cursor, a])
        cursor = max(cursor, b)
    if duracao-cursor > .5: silencios.append([cursor, duracao])
    for a, b in normais:
        if any(min(b, d) > max(a, c) for c, d in silencios):
            suspeitas.append([a, b])
    janelas = unir([[max(0, a-contexto), min(duracao, b+contexto)] for a, b in suspeitas], gap=1.0)
    out = []
    for a, b in janelas:
        # Contexto sobreposto em janelas longas, nunca uma inferência ilimitada.
        while b-a > 45:
            out.append([round(a, 3), round(a+45, 3)]); a += 42
        out.append([round(a, 3), round(b, 3)])
    return out



def combinar_palavras(originais, refinadas, confianca=.6):
    """Troca só alinhamentos com texto, tempo e vizinho seguros.

    Exige confiança numérica explícita e finita>=.6, sobreposição temporal,
    palavra até1s e vizinho textual também confiável/próximo ao seu original.
    Essas condições valem para TODAS as palavras. Ambiguidade ou falta de
    evidência preserva o original. VAD continua independente desta proteção.
    """
    import unicodedata
    def texto(p):
        return ''.join(c for c in unicodedata.normalize('NFKC',
            p.get('w', p.get('word', ''))).casefold() if c.isalnum())
    def limites(p):
        return float(p.get('t', p.get('start', 0))), float(p.get('e', p.get('end', 0)))
    score=confianca_refino
    def compativeis(p,q):
        a,b=limites(p);c,d=limites(q);prob=score(q)
        return (prob is not None and prob>=confianca and
            all(math.isfinite(v) for v in (a,b,c,d)) and
            0<b-a<=1 and 0<d-c<=1 and min(b,d)>max(a,c) and
            abs((a+b-c-d)/2)<=.75)
    original=list(originais)
    # Janela sobreposta pode repetir exatamente o mesmo token/intervalo.
    unique={}
    for p in refinadas:
        a,b=limites(p);key=(texto(p),round(a,3),round(b,3))
        prior=unique.get(key)
        current_score=score(p)
        prior_score=score(prior) if prior is not None else None
        if prior is None or (current_score is not None and
                            (prior_score is None or current_score>prior_score)):
            unique[key]=p
    refined=sorted(unique.values(),key=lambda p:limites(p))
    ot=[texto(p) for p in original];rt=[texto(p) for p in refined]
    proposed={}
    for i,p in enumerate(original):
        if not ot[i]:continue
        candidates=[]
        for j,q in enumerate(refined):
            if rt[j]!=ot[i] or not compativeis(p,q):continue
            anchored=False
            for offset in (-1,1):
                oi,rj=i+offset,j+offset
                if (0<=oi<len(original) and 0<=rj<len(refined) and
                    ot[oi] and ot[oi]==rt[rj] and
                    compativeis(original[oi],refined[rj])):
                    anchored=True
                    break
            if not anchored:continue
            candidates.append(j)
        if len(candidates)==1:proposed[i]=candidates[0]
    counts={}
    for j in proposed.values():counts[j]=counts.get(j,0)+1
    matches={i:j for i,j in proposed.items() if counts[j]==1}
    consumed=set(matches.values())
    return sorted([refined[matches[i]] if i in matches else p for i,p in enumerate(original)]
        +[dict(p,_refino_adicional=True) for j,p in enumerate(refined) if j not in consumed],
        key=lambda p:limites(p))


def identidade_modelo():
    import importlib.metadata
    import os
    from pathlib import Path
    from comum import MODELOS
    from faster_whisper.utils import download_model
    nome = os.environ.get('ESTUDIO_WHISPER_MODELO', 'large-v3-turbo')
    path = nome if os.path.isdir(nome) else download_model(nome,
        cache_dir=os.path.join(MODELOS, 'whisper'), local_files_only=True)
    weights = Path(path)/'model.bin'; st = weights.stat()
    return dict(nome=nome, caminho=str(Path(path).resolve()), tamanho=st.st_size,
        mtime_ns=st.st_mtime_ns, faster_whisper=importlib.metadata.version('faster-whisper'),
        device='cpu', compute_type='int8', algoritmo='refino-cortes-v1')


def chave_refino(wav16k, palavras, modelo):
    import hashlib
    import json
    from pathlib import Path
    path = Path(wav16k); st = path.stat()
    conteudo = dict(audio=dict(caminho=str(path.resolve()), tamanho=st.st_size, mtime_ns=st.st_mtime_ns,
        inode=st.st_ino), transcricao=hashlib.sha256(json.dumps(palavras, sort_keys=True,
        ensure_ascii=False).encode()).hexdigest(), modelo=modelo)
    return hashlib.sha256(json.dumps(conteudo, sort_keys=True).encode()).hexdigest()


def _salvar_cache(path, dados):
    import json
    import os
    import tempfile
    fd, temp = tempfile.mkstemp(prefix='.refino-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(dados, f, ensure_ascii=False); f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def refinar_palavras(wav16k, palavras, audio, fala_baixa, fala_alta, *, cache_path=None, log=print,
                     transcrever=None, modelo_info=None):
    """Uma inferência local por janela pendente, com cache invalidado pela fonte/modelo.

    transcrever/modelo_info são injeções de teste; produção sempre usa snapshot
    instalado, sem download nem provedor. No máximo oito threads CPU.
    """
    import json
    import os
    from pathlib import Path
    windows = janelas_refino(len(audio)/16000, palavras, fala_baixa, fala_alta)
    if not windows: return list(palavras)
    info = modelo_info if modelo_info is not None else identidade_modelo()
    key = chave_refino(wav16k, palavras, info)
    path = Path(cache_path) if cache_path else Path(wav16k).with_name('whisper_refino_cortes.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path)+'.lock', 'a') as lock:
        # fcntl não existe no Windows; instalação local é de um usuário só, então sem ele o pior
        # caso é uma corrida entre duas edições simultâneas do MESMO projeto, que já é raro.
        if os.name != 'nt':
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with path.open() as f: cache = json.load(f)
        except (FileNotFoundError, ValueError): cache = {}
        if cache.get('chave') != key:
            cache = dict(chave=key, modelo=info, janelas={}, versao=1)
        model = None
        for index, (a, b) in enumerate(windows, 1):
            wa, wb = int(a*16000), int(b*16000); wid = f'{wa}-{wb}'
            if wid in cache['janelas']: continue
            log(f'Refinando fala local {index}/{len(windows)}: {a:.2f}–{b:.2f}s')
            if transcrever is None:
                if model is None:
                    from faster_whisper import WhisperModel
                    model = WhisperModel(info['caminho'], device='cpu', compute_type='int8',
                        cpu_threads=min(8, max(1, int(os.environ.get('ESTUDIO_WHISPER_THREADS') or 8))),
                        local_files_only=True)
                segments, _ = model.transcribe(audio[wa:wb], language='pt', word_timestamps=True,
                    condition_on_previous_text=False, vad_filter=False, beam_size=5)
                refined = [dict(w=w.word.strip(), t=round(a+w.start, 4), e=round(a+w.end, 4),
                    probabilidade=round(w.probability, 4)) for seg in segments for w in (seg.words or [])
                    if w.word.strip() and w.end > w.start]
            else:
                refined = transcrever(audio[wa:wb], a, b)
            cache['janelas'][wid] = dict(ini=a, fim=b, palavras=refined)
            _salvar_cache(path, cache)
        # Reutiliza inferência por janela idêntica, mas não mistura alinhamentos
        # de janelas antigas que não pertencem ao plano atual de refino.
        active=[f'{int(a*16000)}-{int(b*16000)}' for a,b in windows]
        refined=[p for wid in active for p in cache['janelas'][wid]['palavras']]
        return combinar_palavras(palavras, refined)


def segmentar_arquivo(wav16k, faixas, palavras, cfg=None, *, log=print, cache_path=None):
    cfg = cfg or {}
    audio = ler_audio(wav16k)
    baixo, alto = detectar_fala(audio)
    refined = refinar_palavras(wav16k, palavras, audio, baixo, alto, cache_path=cache_path, log=log)
    relatorio={}
    segmentos=planejar_segmentos(len(audio)/16000, faixas, baixo, alto, refined,
        pausa_min=cfg.get('pausa_min', .16), respiro=cfg.get('respiro', 1/30),
        cauda=cfg.get('cauda', 1/30), fps=cfg.get('fps', 30),relatorio=relatorio)
    if relatorio['palavras_sem_vad']:
        import json
        log('Palavras sem VAD preservadas por incerteza: '+json.dumps(
            relatorio['palavras_sem_vad'],ensure_ascii=False))
    return segmentos
