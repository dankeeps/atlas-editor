#!/usr/bin/env python3
"""Prepara um upload já cortado: normalização técnica, sem mudar a montagem."""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import wave

FPS = 30
ARQUIVOS = ("jc.mov", "voz.wav", "voz16k.wav", "mapa.json", "whisper.json")
FONTES = ("voz48k.wav", "voz16k.wav", "whisper.json")


def identidade_arquivo(caminho):
    p = Path(caminho).resolve(strict=True)
    st = p.stat()
    if not p.is_file() or st.st_size <= 0:
        raise ValueError("O vídeo de origem não é um arquivo válido.")
    return dict(caminho=str(p), tamanho=st.st_size, mtime_ns=st.st_mtime_ns)


def validar_fonte(projeto, pedido=None):
    p = projeto["fonte_video"]
    identidade = identidade_arquivo(p)
    if projeto.get("fonte_identidade") != identidade:
        raise ValueError("A origem mudou ou não tem identidade confirmada. Crie um projeto para este upload.")
    if pedido and identidade_arquivo(pedido["video"]) != identidade:
        raise ValueError("O pedido e o projeto apontam para vídeos diferentes.")
    return identidade


def _json(p, dados=None):
    if dados is None:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    p = Path(p)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")
        os.replace(tmp, p)
    finally:
        if tmp.exists(): tmp.unlink()


def _ff(*args):
    p = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-nostdin", "-y", *map(str, args)],
                       capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError("Normalização do vídeo falhou: " + p.stderr.strip()[-2000:])


def _probe(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(p)],
                       check=True, capture_output=True, text=True)
    return json.loads(r.stdout)


def _duracao(info):
    valores = [info.get("format", {}).get("duration")]
    valores += [s.get("duration") for s in info.get("streams", []) if s.get("codec_type") in ("audio", "video")]
    dur = max(float(v) for v in valores if v is not None and math.isfinite(float(v)))
    if dur <= 0:
        raise ValueError("O vídeo não tem duração válida.")
    return dur


def _pcm(p):
    h = hashlib.sha256()
    with wave.open(str(p), "rb") as w:
        meta = (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes())
        while bloco := w.readframes(65536):
            h.update(bloco)
    return meta, h.hexdigest()


def _resumo_arquivo(p):
    st = Path(p).stat()
    return dict(tamanho=st.st_size, mtime_ns=st.st_mtime_ns)


def versao_pronta(d, identidade):
    """Só retoma nossa versão concluída, nunca um jump cut ou outro upload."""
    vd = Path(d) / "versoes" / "A"
    p = vd / "video_integral.json"
    if not p.is_file():
        return False
    m = _json(p)
    if m.get("fonte") != identidade:
        return False
    f = Path(d) / "fonte"
    return (set(m.get("arquivos", {})) == set(ARQUIVOS) and set(m.get("fontes", {})) == set(FONTES)
        and all((vd / nome).is_file() and _resumo_arquivo(vd / nome) == m["arquivos"][nome] for nome in ARQUIVOS)
        and all((f / nome).is_file() and _resumo_arquivo(f / nome) == m["fontes"][nome] for nome in FONTES))


def _metadados(d, projeto, dur):
    cortes = dict(modo="video_ja_cortado", cortes_cfg=dict(modo="video_ja_cortado"),
        versoes=[dict(id="A", nome="Versão única", faixas=[[0.0, dur]], segs=[[0.0, dur]], secoes=[])], frases=[])
    _json(d / "fonte" / "cortes.json", cortes)
    projeto.update(versoes=[dict(id="A", nome="Versão única")], video_ja_cortado=True)
    _json(d / "projeto.json", projeto)


def preparar(d, log=print):
    """Mantém todo o áudio e a ordem original; a grade de vídeo pode diferir ≤1 frame."""
    d = Path(d)
    projeto = _json(d / "projeto.json")
    identidade = validar_fonte(projeto)
    vd = d / "versoes" / "A"
    if versao_pronta(d, identidade):
        dur = _json(vd / "mapa.json")["dur"]
        _metadados(d, projeto, dur)
        return dur
    if vd.exists() or projeto.get("versoes"):
        raise ValueError("Este projeto já tem uma versão diferente. O upload já cortado precisa de um projeto novo.")
    f = d / "fonte"
    whisper = _json(f / "whisper.json")
    voz48 = _pcm(f / "voz48k.wav")
    voz16 = _pcm(f / "voz16k.wav")
    if voz48[0][:3] != (2, 2, 48000) or voz16[0][:3] != (1, 2, 16000):
        raise ValueError("Os áudios da fonte precisam ser PCM 48 kHz estéreo e 16 kHz mono.")
    origem = _probe(projeto["fonte_video"])
    if not all(any(s.get("codec_type") == tipo for s in origem["streams"]) for tipo in ("video", "audio")):
        raise ValueError("O upload precisa ter vídeo e áudio.")
    dur_fonte = max(_duracao(origem), voz48[0][3] / 48000, voz16[0][3] / 16000)
    for seg in whisper["segments"]:
        for pal in seg.get("words", []):
            a, b = float(pal["start"]), float(pal["end"])
            if not math.isfinite(a) or not math.isfinite(b) or a < 0 or b < a or b > dur_fonte + 1 / FPS:
                raise ValueError("A transcrição não corresponde à duração do upload.")
    vd.parent.mkdir(parents=True, exist_ok=True)
    temporario = Path(tempfile.mkdtemp(prefix=".video-integral-", dir=vd.parent))
    try:
        log("normalizando o vídeo já cortado, sem retirar fala ou pausas…")
        _ff("-i", projeto["fonte_video"], "-map", "0:v:0", "-map", "0:a:0",
            "-vf", "scale=1440:2560:flags=lanczos,fps=30", "-c:v", "libx264", "-crf", "13", "-preset", "fast",
            "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-ac", "2", "-ar", "48000", "-movflags", "+faststart", temporario / "jc.mov")
        _ff("-i", temporario / "jc.mov", "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", temporario / "voz.wav")
        if _pcm(temporario / "voz.wav") != voz48:
            raise ValueError("O áudio normalizado não é idêntico à fonte; publicação da versão interrompida.")
        shutil.copy2(f / "voz16k.wav", temporario / "voz16k.wav")
        shutil.copy2(f / "whisper.json", temporario / "whisper.json")
        info = _probe(temporario / "jc.mov")
        video = next(s for s in info["streams"] if s.get("codec_type") == "video")
        dur_normalizada = _duracao(info)
        if (video["width"], video["height"], video["avg_frame_rate"]) != (1440, 2560, "30/1"):
            raise ValueError("A normalização não produziu o formato esperado.")
        if abs(dur_normalizada - dur_fonte) > 1 / FPS + .002:
            raise ValueError("A normalização alterou a duração além de um quadro.")
        # A última fração de frame só estende a grade visual, nunca encurta o áudio.
        dur = max(dur_fonte, dur_normalizada)
        mapa = dict(mapa=[[0.0, dur, 0.0]], dur=dur, modo="video_ja_cortado", duracao_fonte=dur_fonte)
        _json(temporario / "mapa.json", mapa)
        (temporario / "verificacao.md").write_text("# Vídeo já cortado\n\nUma versão; timeline integral. Nenhum corte automático, fade ou análise de erros.\n"
            "Áudio PCM idêntico à fonte e transcrição reaproveitada sem mudar os tempos.\n", encoding="utf-8")
        arquivos = {n: _resumo_arquivo(temporario / n) for n in ARQUIVOS}
        fontes = {n: _resumo_arquivo(f / n) for n in FONTES}
        _json(temporario / "video_integral.json", dict(fonte=identidade, arquivos=arquivos, fontes=fontes, audio_sha256=voz48[1], duracao_fonte=dur_fonte))
        if validar_fonte(projeto) != identidade:
            raise ValueError("O upload mudou durante a preparação.")
        # A versão só fica disponível depois de todas as verificações.
        os.replace(temporario, vd)
        _metadados(d, projeto, dur)
        return dur
    finally:
        if temporario.exists():
            shutil.rmtree(temporario)


if __name__ == "__main__":
    import sys
    preparar(sys.argv[1])
