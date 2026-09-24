"""Render paralelo: divide a versão em pedaços, renderiza todos ao mesmo tempo, junta e põe o som.
uso: python3 render.py <pasta da versão> [--saida arquivo.mp4] [--processos N] [--rascunho]
Imprime 'progresso N' (0-100) e, no fim, 'saida <arquivo>'. Sem --rascunho, marca como usados na biblioteca os B-rolls
que entraram no vídeo."""
import sys, os, json, subprocess, time, threading, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from comum import SKILL
LIB = os.path.dirname(os.path.abspath(__file__)); PY = sys.executable

def main():
    args = sys.argv[1:]; pasta = os.path.abspath(args[0])
    saida = args[args.index("--saida") + 1] if "--saida" in args else None
    n = int(args[args.index("--processos") + 1]) if "--processos" in args else max(2, min(6, (os.cpu_count() or 4) - 4))
    P = json.load(open(os.path.join(pasta, "plano.json"))); total = int(round(P["dur"] * 30))
    print("etapa efeitos sonoros", flush=True)
    r = subprocess.run([PY, os.path.join(LIB, "mix.py"), pasta], capture_output=True, text=True)
    if r.returncode: print(r.stderr[-1500:], file=sys.stderr); sys.exit(1)
    print("etapa vídeo", flush=True)
    tmp = os.path.join(pasta, "_pedacos"); os.makedirs(tmp, exist_ok=True)
    cortes = [round(total * k / n) for k in range(n + 1)]; feitos = [0] * n; procs = []
    for k in range(n):
        a, b = cortes[k], cortes[k + 1]
        p = subprocess.Popen([PY, os.path.join(LIB, "motor.py"), pasta, "video", os.path.join(tmp, f"p{k:02d}.mp4"), "--quadros", f"{a}:{b}"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        procs.append(p)
        def ler(k=k, p=p):
            for linha in p.stdout:
                m = re.match(r"quadro (\d+) (\d+)", linha)
                if m: feitos[k] = int(m.group(1))
        threading.Thread(target=ler, daemon=True).start()
    ult = -1; t0 = time.time()
    while any(p.poll() is None for p in procs):
        pct = int(sum(feitos) * 97 / max(1, total))
        if pct != ult: print(f"progresso {pct}", flush=True); ult = pct
        time.sleep(1)
    for k, p in enumerate(procs):
        if p.returncode: print(p.stderr.read()[-1500:], file=sys.stderr); sys.exit(1)
    lista = os.path.join(tmp, "lista.txt")
    open(lista, "w").write("".join(f"file 'p{k:02d}.mp4'\n" for k in range(n)))
    video = os.path.join(tmp, "video.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lista, "-c", "copy", video], check=True)
    if not saida:
        proj = json.load(open(os.path.join(os.path.dirname(os.path.dirname(pasta)), "projeto.json")))
        nome_v = next((v["nome"] for v in proj["versoes"] if v["id"] == os.path.basename(pasta)), os.path.basename(pasta))
        os.makedirs(os.path.join(pasta, "renders"), exist_ok=True); rev = 1
        while True:
            saida = os.path.join(pasta, "renders", f"{proj['nome']} - {nome_v} rev{rev}.mp4")
            if not os.path.exists(saida): break
            rev += 1
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video, "-i", os.path.join(pasta, "mix.wav"), "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart", saida], check=True)
    for f in os.listdir(tmp): os.remove(os.path.join(tmp, f))
    os.rmdir(tmp)
    if "--rascunho" not in args:
        try:
            import biblioteca
            ids = sorted({i for i in (biblioteca.de_src(c["src"]) for c in P["cenas"] if c.get("src")) if i})
            proj = json.load(open(os.path.join(os.path.dirname(os.path.dirname(pasta)), "projeto.json")))
            if ids: biblioteca.marcar_usado(ids, proj["nome"], os.path.basename(pasta)); print(f"usados {' '.join(ids)}", flush=True)
        except Exception as e: print(f"aviso: não marquei os B-rolls usados na biblioteca ({e})", file=sys.stderr)
    print("progresso 100", flush=True); print(f"tempo {time.time() - t0:.0f}s", flush=True); print(f"saida {saida}", flush=True)

if __name__ == "__main__":
    main()
