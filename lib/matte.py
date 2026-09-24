"""Recorte da pessoa (matting) com RobustVideoMatting em ONNX — local, sem chave, sem custo.

Por que não a Vision do macOS: ela decide cada quadro do zero e devolve máscara praticamente dura
(0,56% dos pixels em valor intermediário, medido). Resultado: borda tremendo onde falta contraste e
cabelo recortado a machado. O RVM é um modelo feito para VÍDEO — carrega um estado de um quadro para
o outro (r1..r4) e devolve alfa contínuo, então a borda para quieta e o cabelo fica semitransparente.

O estado recorrente NÃO atravessa emenda de corte: ali o quadro anterior é de outro momento e o
modelo arrastaria um fantasma. Em cada emenda o estado zera.

Roda em vários processos: o estado recorrente exige ordem, então cada pedaço começa alguns quadros ANTES
do seu trecho só para o estado esquentar, e esses quadros de aquecimento são descartados.

uso: python3 matte.py <pasta da versão> [--res 720]
     python3 matte.py --instalar [mobilenetv3|resnet50]      baixa o modelo (não vem no git) [--modelo mobilenetv3|resnet50] [--ratio 0.25] [--procs 4]
"""
import os, sys, json, subprocess
import numpy as np
from PIL import Image

LIB = os.path.dirname(os.path.abspath(__file__)); SKILL = os.path.dirname(LIB)
from comum import MODELOS


def emendas(vd, fps=30):
    """Os quadros em que começa uma tomada nova (o jump cut costurou dois pedaços distantes)."""
    p = os.path.join(vd, "mapa.json")
    if not os.path.exists(p): return set()
    try: mapa = json.load(open(p))["mapa"]
    except (OSError, ValueError, KeyError): return set()
    q, saltos = 0, set()
    for a, b, *_ in mapa:
        q += int(round((float(b) - float(a)) * fps))
        saltos.add(q)                                     # primeiro quadro do pedaço seguinte
    return saltos


URL = "https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/"
SOMAS = {"rvm_mobilenetv3_fp32.onnx": "88d4531297118f595bf2fd60f6f566aec2e559393802d1f436c380f0cbbd2828"}


def instalar(modelo="mobilenetv3", log=print):
    """Baixa o modelo da release oficial. Não vai no git: são 14 MB (mobilenetv3) ou 103 MB (resnet50)."""
    import hashlib, urllib.request
    nome = f"rvm_{modelo}_fp32.onnx"; arq = os.path.join(MODELOS, nome)
    if os.path.exists(arq): return arq
    os.makedirs(MODELOS, exist_ok=True)
    log(f"baixando {nome} de github.com/PeterL1n/RobustVideoMatting…")
    tmp = arq + ".parcial"
    urllib.request.urlretrieve(URL + nome, tmp)
    if nome in SOMAS:
        soma = hashlib.sha256(open(tmp, "rb").read()).hexdigest()
        if soma != SOMAS[nome]:
            os.remove(tmp); sys.exit(f"o arquivo baixado não confere (sha256 {soma[:16]}…)")
    os.rename(tmp, arq); log(f"pronto: {arq}")
    return arq


def sessao(modelo="mobilenetv3"):
    import onnxruntime as ort
    arq = os.path.join(MODELOS, f"rvm_{modelo}_fp32.onnx")
    if not os.path.exists(arq): arq = instalar(modelo)
    o = ort.SessionOptions(); o.intra_op_num_threads = int(os.environ.get("ESTUDIO_ONNX_THREADS") or (os.cpu_count() or 4))
    # CPU de propósito: o CoreML só aceita parte do grafo (282 de 303 nós, em 10 pedaços) e o vaivém
    # entre ANE e CPU sai 10x mais lento que rodar tudo na CPU — medido.
    prov = os.environ.get("ESTUDIO_ONNX_EP", "CPUExecutionProvider")
    return ort.InferenceSession(arq, o, providers=[prov]), prov


AQUECE = 12          # quadros só para o estado recorrente assentar antes do trecho que interessa


def gerar(vd, res=720, modelo="mobilenetv3", ratio=None, saida=None, log=print, de=0, ate=None, pasta=None):
    """Grava masks/f00000.png … a partir do jc.mov da versão. Uma máscara por quadro, mesmo nome de antes.
    Com `de`/`ate`, faz só essa faixa de quadros (é assim que os processos dividem o trabalho)."""
    src = os.path.join(vd, "jc.mov")
    if not os.path.exists(src): sys.exit(f"não achei {src}")
    W = int(res); H = int(round(W * 16 / 9)) // 2 * 2
    # o RVM estima em baixa e refina na resolução cheia: o downsample tem que cair perto de 512 px na borda LONGA
    if ratio is None: ratio = round(max(0.12, min(1.0, 512 / H)), 3)
    if pasta: tmp = pasta; os.makedirs(tmp, exist_ok=True)
    else:
        out = saida or os.path.join(vd, "masks")
        tmp = out + ".novo"
        if os.path.isdir(tmp): subprocess.run(["rm", "-rf", tmp])
        os.makedirs(tmp)

    sess, prov = sessao(modelo)
    if not pasta: log(f"recorte com RVM {modelo} em {W}x{H} (ratio {ratio:.2f}) por {prov.replace('ExecutionProvider', '')}")
    cortes = emendas(vd)
    ini = max(0, de - AQUECE)                             # começa antes para o estado assentar
    zero = np.zeros((1, 1, 1, 1), dtype=np.float32)
    rec = [zero] * 4
    rat = np.array([ratio], dtype=np.float32)

    cmd = ["ffmpeg", "-v", "error"]
    if ini: cmd += ["-ss", f"{ini / 30:.5f}"]
    cmd += ["-i", src, "-an", "-vf", f"scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=W * H * 3 * 4)
    n = ini; gravados = 0
    try:
        while ate is None or n < ate:
            buf = p.stdout.read(W * H * 3)
            if len(buf) < W * H * 3: break
            if n in cortes: rec = [zero] * 4              # tomada nova: o modelo não pode lembrar da anterior
            x = np.frombuffer(buf, dtype=np.uint8).reshape(H, W, 3).transpose(2, 0, 1)[None].astype(np.float32) / 255.0
            fgr, pha, *rec = sess.run(None, {"src": x, "r1i": rec[0], "r2i": rec[1], "r3i": rec[2],
                                             "r4i": rec[3], "downsample_ratio": rat})
            if n >= de:                                   # antes disso é só aquecimento: não grava
                a = (np.clip(pha[0, 0], 0, 1) * 255).astype(np.uint8)
                Image.fromarray(a, "L").save(os.path.join(tmp, f"f{n:05d}.png"))
                gravados += 1
            n += 1
            if not pasta and gravados and gravados % 300 == 0: log(f"  {gravados} quadros…")
    finally:
        p.stdout.close(); p.kill(); p.wait()
    if not gravados: sys.exit("nenhum quadro lido do jc.mov")
    if pasta: return gravados
    if os.path.isdir(out): subprocess.run(["rm", "-rf", out])
    os.rename(tmp, out)
    log(f"{gravados} máscaras em {out}")
    return gravados


def gerar_paralelo(vd, res=720, modelo="mobilenetv3", ratio=None, procs=None, log=print):
    """Divide o vídeo entre vários processos. Cada um esquenta o estado antes do seu trecho, então o
    resultado é o mesmo do sequencial — só que usando os núcleos todos em vez de um."""
    import concurrent.futures as cf
    src = os.path.join(vd, "jc.mov")
    sys.path.insert(0, LIB); import comum
    total = comum.quadros_video(src)                      # a mesma contagem que o motor usa para conferir as máscaras
    nproc = int(procs or max(1, min(6, (os.cpu_count() or 4) // 2)))
    if not total or nproc < 2:
        return gerar(vd, res, modelo, ratio, log=log)

    out = os.path.join(vd, "masks"); tmp = out + ".novo"
    if os.path.isdir(tmp): subprocess.run(["rm", "-rf", tmp])
    os.makedirs(tmp)
    corte = sorted(emendas(vd))
    passo = total / nproc
    marcos = [0]
    for k in range(1, nproc):                             # fatia preferindo emenda de corte: ali o estado zera sozinho
        alvo = passo * k
        perto = min(corte, key=lambda c: abs(c - alvo)) if corte else None
        marcos.append(int(perto if perto is not None and abs(perto - alvo) < passo * 0.35 else alvo))
    marcos.append(total)
    faixas = [(marcos[i], marcos[i + 1]) for i in range(nproc) if marcos[i + 1] > marcos[i]]
    log(f"recorte com RVM {modelo} em {res}px · {len(faixas)} processos · {total} quadros")

    env = dict(os.environ, ESTUDIO_ONNX_THREADS=str(max(2, (os.cpu_count() or 8) // len(faixas))))
    def roda(f):
        a, b = f
        c = [sys.executable, os.path.abspath(__file__), vd, "--res", str(res), "--modelo", modelo,
             "--de", str(a), "--ate", str(b), "--pasta", tmp]
        if ratio: c += ["--ratio", str(ratio)]
        p = subprocess.run(c, capture_output=True, text=True, env=env)
        if p.returncode: raise RuntimeError(f"pedaço {a}-{b} falhou: {(p.stderr or p.stdout)[-300:]}")
        return b - a
    with cf.ThreadPoolExecutor(len(faixas)) as ex:
        for _ in ex.map(roda, faixas): pass
    n = len([f for f in os.listdir(tmp) if f.endswith(".png")])
    if n != total:                                        # não bateu: refaz em um processo só, que é o caminho seguro
        subprocess.run(["rm", "-rf", tmp])
        log(f"o corte em pedaços deu {n} de {total} máscaras; refazendo em um processo só")
        return gerar(vd, res, modelo, ratio, log=log)
    if os.path.isdir(out): subprocess.run(["rm", "-rf", out])
    os.rename(tmp, out)
    log(f"{n} máscaras em {out}")
    return n


if __name__ == "__main__":
    A = sys.argv[1:]
    if A and A[0] == "--instalar":
        instalar(A[1] if len(A) > 1 else "mobilenetv3"); sys.exit()
    def opt(nome, padrao=None):
        if nome in A:
            i = A.index(nome); v = A[i + 1]; del A[i:i + 2]; return v
        return padrao
    res = int(opt("--res", 720)); modelo = opt("--modelo", "mobilenetv3")
    r = opt("--ratio"); saida = opt("--saida"); pasta = opt("--pasta")
    de = opt("--de"); ate = opt("--ate"); procs = opt("--procs")
    if pasta:                                             # um pedaço, chamado pelo gerar_paralelo
        gerar(os.path.abspath(A[0]), res, modelo, float(r) if r else None, log=lambda m: None,
              de=int(de or 0), ate=int(ate) if ate else None, pasta=pasta)
    else:
        gerar_paralelo(os.path.abspath(A[0]), res, modelo, float(r) if r else None, procs)
