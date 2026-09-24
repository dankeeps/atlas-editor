"""Pose multi-pessoa em CPU: YOLOv8n ONNX. Sem modelo, o motor mantém o enquadramento de fallback.

Instalação: python lib/pose.py --instalar
Origem: https://huggingface.co/Xenova/yolov8n-pose (export dos pesos Ultralytics, AGPL-3.0).
"""
import glob
import hashlib
import os
import sys
import urllib.request
import numpy as np
from PIL import Image
from comum import MODELOS

REVISAO = "da4224085e2f6ce4c9ff9b670e28765194619db2"
URL = f"https://huggingface.co/Xenova/yolov8n-pose/resolve/{REVISAO}/onnx/model.onnx"
SHA256 = "04f6d2416266f2aba6c5ba8b26de33ed9eba3279f972ac02e33f9f1366547586"
ARQ = os.path.join(MODELOS, "yolov8n-pose.onnx")
_sessao = None


def instalar():
    os.makedirs(MODELOS, exist_ok=True)
    if os.path.exists(ARQ) and hashlib.sha256(open(ARQ, "rb").read()).hexdigest() == SHA256:
        return ARQ
    tmp = ARQ + ".parcial"
    try:
        urllib.request.urlretrieve(URL, tmp)
        if hashlib.sha256(open(tmp, "rb").read()).hexdigest() != SHA256:
            raise ValueError("Modelo de pose não confere com o SHA-256 da versão fixada.")
        os.replace(tmp, ARQ)
    finally:
        if os.path.exists(tmp): os.remove(tmp)
    return ARQ


def sessao():
    global _sessao
    if _sessao is None:
        if not os.path.isfile(ARQ): return None
        import onnxruntime as ort
        op = ort.SessionOptions()
        op.intra_op_num_threads = max(1, int(os.environ.get("ESTUDIO_ONNX_THREADS") or min(4, os.cpu_count() or 4)))
        _sessao = ort.InferenceSession(ARQ, op, providers=["CPUExecutionProvider"])
    return _sessao


def preparar(im, tamanho=640):
    """Letterbox quadrado, mantendo proporção e registrando a inversa para as juntas."""
    w, h = im.size
    escala = min(tamanho / w, tamanho / h)
    nw, nh = round(w * escala), round(h * escala)
    x, y = (tamanho - nw) // 2, (tamanho - nh) // 2
    tela = Image.new("RGB", (tamanho, tamanho), (114, 114, 114))
    tela.paste(im.convert("RGB").resize((nw, nh), Image.Resampling.BILINEAR), (x, y))
    tensor = np.asarray(tela, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
    return tensor, (x, y, nw, nh)


def pessoas(saida, transformacao, limiar=0.3):
    """COCO: ombros 5/6, quadris 11/12; coordenadas normalizadas com origem em cima."""
    linhas = np.asarray(saida)[0]
    if linhas.shape[0] == 56: linhas = linhas.T
    if linhas.shape[1] != 56: raise ValueError("Saída inesperada do modelo YOLOv8n-pose")
    x, y, w, h = transformacao
    candidatos = []
    for r in linhas:
        if r[4] < limiar: continue
        juntas = r[5:].reshape(17, 3).copy()
        juntas[:, 0] = (juntas[:, 0] - x) / w
        juntas[:, 1] = (juntas[:, 1] - y) / h
        candidatos.append((float(r[4]), r[:4], juntas))
    # NMS antes de escolher o maior tronco: duplicatas menos confiáveis não ganham por ruído.
    candidatos.sort(key=lambda c: c[0], reverse=True)
    aceitos = []
    for candidato in candidatos:
        if all(iou(candidato[1], outro[1]) <= 0.5 for outro in aceitos): aceitos.append(candidato)
    return [j for _, _, j in aceitos]


def iou(a, b):
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    w = max(0, min(ax + aw / 2, bx + bw / 2) - max(ax - aw / 2, bx - bw / 2))
    h = max(0, min(ay + ah / 2, by + bh / 2) - max(ay - ah / 2, by - bh / 2))
    inter = w * h
    return inter / max(float(aw * ah + bw * bh - inter), 1e-9)


def maior_tronco(deteccoes, largura, altura, limiar=0.3):
    melhor, maior = None, -1.0
    for j in deteccoes:
        if not all(j[i, 2] >= limiar for i in (5, 6)): continue
        pescoco = float((j[5, 1] + j[6, 1]) / 2)
        quadris = [float(j[i, 1]) for i in (11, 12) if j[i, 2] >= limiar]
        cintura = sum(quadris) / len(quadris) if quadris else None
        tronco = cintura - pescoco if cintura is not None else 0.0
        if tronco > maior:
            maior = tronco
            melhor = (cintura * altura if cintura is not None else None,
                       pescoco * altura, abs(float(j[5, 0] - j[6, 0])) * largura)
    return melhor or (None, None, None)


def juntas_pasta(pasta, largura=1080, altura=1920):
    s = sessao()
    if s is None: return None, None, None
    medidas = []
    for p in sorted(glob.glob(os.path.join(pasta, "*.jpg"))):
        with Image.open(p) as im: tensor, transformacao = preparar(im)
        saida = s.run(None, {s.get_inputs()[0].name: tensor})[0]
        medidas.append(maior_tronco(pessoas(saida, transformacao), largura, altura))
    resultado = []
    for i in range(3):
        valores = [m[i] for m in medidas if m[i] is not None]
        resultado.append(float(np.median(valores)) if valores else None)
    return tuple(resultado)


if __name__ == "__main__":
    if "--instalar" in sys.argv: print(instalar())
    else:
        import json
        print(json.dumps(juntas_pasta(sys.argv[1])))
