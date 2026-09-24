"""Real CPU matting, four previews and H.264/AAC export; no paid APIs or user media."""
import json, os, subprocess, sys, tempfile
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'lib'
sys.path.insert(0, str(LIB))

def run(*args):
    p = subprocess.run([str(a) for a in args], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f'{Path(str(args[0])).name} failed: {p.stderr[-2500:]} {p.stdout[-1000:]}')
    return p.stdout

base = Path(tempfile.mkdtemp(prefix='atlas-media-smoke-'))
os.environ['ESTUDIO_RAIZ'] = str(base / 'Edições')
os.environ['ESTUDIO_BROLLS'] = str(base / 'B-rolls')
os.environ['ESTUDIO_BIBLIOTECA'] = str(base / 'B-rolls' / 'biblioteca')
proj = base / 'Edições' / 'Teste Atlas'; vd = proj / 'versoes' / 'A'
vd.mkdir(parents=True)
(proj / 'assets').mkdir()
(vd / 'historico').mkdir()
def save(p, d): p.write_text(json.dumps(d, ensure_ascii=False), encoding='utf8')
run('ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=270x480:rate=30','-t','2','-c:v','libx264','-pix_fmt','yuv420p',vd/'jc.mov')
run('ffmpeg','-v','error','-y','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','2','-ac','2',vd/'voz.wav')
run('ffmpeg','-v','error','-y','-i',vd/'voz.wav','-ar','16000','-ac','1',vd/'voz16k.wav')
source = proj/'assets'/'broll.png'
im = Image.new('RGB',(270,480),'#244c83'); ImageDraw.Draw(im).ellipse((30,100,240,330),fill='#edc467'); im.save(source)
whisper={'segments':[{'start':0.1,'end':1.9,'text':' Teste do Atlas Editor','words':[
    {'word':' Teste','start':0.1,'end':0.4},{'word':' do','start':0.45,'end':0.6},
    {'word':' Atlas','start':0.7,'end':1.0},{'word':' Editor','start':1.2,'end':1.8}]}]}
save(vd/'whisper.json',whisper)
save(vd/'mapa.json',{'dur':2,'mapa':[[0,2,0]]})
save(proj/'projeto.json',{'nome':'Teste Atlas','estilo':'ultradinamico','versoes':[{'id':'A','nome':'A'}], 'fonte_video':str(vd/'jc.mov')})
p={'src':'jc.mov','masks':'masks','whisper':'whisper.json','estilo':'ultradinamico','dur':2,'cortes':[0],
   'blocos':[{'uid':'b1','linhas':[['sans','Atlas',0.05,'✅']],'fim':0.95,'topo':0.25,'atras':False}],
   'cenas':[{'uid':'c1','tipo':'cheia','ini':1,'fim':2,'src':str(source),'src_ini':0,'trans':'corte','saida':'corte'}],
   'escuro':[],'pb':[],'glitch':[],'flash':[],'correcoes':{},'sfx_edicoes':{},'sfx_extras':[],
   'zoom':[[0,1]],'legendas':[], '_prox_uid':3}
save(vd/'plano.json',p)
run(sys.executable,LIB/'matte.py',vd,'--res','270','--procs','1')
assert len(list((vd/'masks').glob('f*.png'))) == 60
run(sys.executable,LIB/'motor.py',vd,'legendas')
points=[0.4,0.7,1.2,1.7]
run(sys.executable,LIB/'motor.py',vd,'preview',*points)
run(sys.executable,LIB/'render.py',vd,'--processos','2')
final=next((vd/'renders').glob('*.mp4'))
probe=json.loads(run('ffprobe','-v','error','-show_streams','-of','json',final))
video=next(s for s in probe['streams'] if s['codec_type']=='video')
assert (video['width'],video['height'],int(video['nb_frames']))==(1080,1920,60)
assert any(s['codec_type']=='audio' for s in probe['streams'])
errors=[]
for t in points:
    decoded=vd/f'decoded-{t}.png'
    run('ffmpeg','-v','error','-y','-ss',t,'-i',final,'-frames:v','1',decoded)
    preview=vd/'prev'/f'quadro_{t:07.2f}.jpg'
    err=float(np.abs(np.asarray(Image.open(preview),dtype=float)-np.asarray(Image.open(decoded),dtype=float)).mean())
    assert err < 12, (t,err)
    errors.append(round(err,3))
print(json.dumps({'ok':True,'mask_frames':60,'render_frames':60,'dimensions':[1080,1920],
                  'preview_render_mean_errors':errors,'fixture':str(base),'render':str(final)}))
