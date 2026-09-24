"""Linguagem do roteiro de edição (edicao.py do projeto). Tudo é ancorado em PALAVRAS da fala, não em segundos:
o mesmo roteiro vale para todas as versões (o corpo comum se ajusta sozinho ao tempo de cada uma).

Exemplo de edicao.py:
    def editar(E):                      # E = Edicao da versão (E.v = "A", "B"...)
        if E.v == "A":
            E.letreiro(L("azul", "prazer", "Prazer"), ate="Daniel")
            E.card("Prazer", ate="Sou", broll="IMG-instagram")
        E.letreiro(L("sans", "o primeiro", "primeiro"), L("azul", "brasileiro", "brasileiro"), ate="trazer")
        E.broll("cheia", "um método", ate="para manter", broll="TSN-BR001", ini=2.0, trans="whip", saida="whip")
        E.escuro("seco", ate="de forma")
Âncoras: texto (uma ou mais palavras) procurado a partir do cursor, que anda para frente a cada uso.
  "frase"      -> início da primeira palavra        ("frase", 1) -> início da 2ª ocorrência a partir do cursor
  fim("frase") -> fim da última palavra da frase    +0.3 / -0.2 com mais(ancora, s)
  E.t(5.2)     -> tempo absoluto (último recurso)."""
import re, unicodedata

def _n(w):
    w = unicodedata.normalize("NFD", w.lower()); w = "".join(c for c in w if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9%]", "", w)

class fim:
    def __init__(self, txt, n=0): self.txt, self.n = txt, n
class mais:
    def __init__(self, ancora, s): self.ancora, self.s = ancora, s
class T:
    def __init__(self, s): self.s = s

def L(estilo, texto, ancora=None, emoji=None):
    """Linha de letreiro: estilo (sans, azul, num, branca, lista...), texto na tela, âncora (palavra falada que faz a linha entrar)."""
    return [estilo, texto, ancora if ancora is not None else texto, emoji]

class Edicao:
    def __init__(self, v, palavras, dur, acervo, estilo):
        self.v, self.W, self.dur, self.acervo, self.ES = v, palavras, dur, acervo, estilo
        self.toks = [_n(p["w"]) for p in palavras]; self.cursor = 0
        self.blocos, self.cenas, self.escuros, self.pbs, self.glitches, self.flashes = [], [], [], [], [], []
        self.avisos = []

    # ---------------------------------------------------------------- âncoras
    def _acha(self, txt, n=0, mover=True):
        alvo = [_n(w) for w in str(txt).split() if _n(w)]
        if not alvo: raise ValueError(f"âncora vazia: {txt!r}")
        achou = -1; k = 0
        for i in range(self.cursor, len(self.toks) - len(alvo) + 1):
            if self.toks[i:i + len(alvo)] == alvo:
                if k == n: achou = i; break
                k += 1
        if achou < 0:                                   # tolerância: procura sem exigir a sequência inteira
            for i in range(self.cursor, len(self.toks)):
                if self.toks[i] == alvo[0]: achou = i; break
        if achou < 0: raise ValueError(f"[{self.v}] âncora não encontrada depois de {self.W[self.cursor]['t'] if self.cursor < len(self.W) else self.dur:.1f}s: {txt!r}")
        if mover: self.cursor = achou
        return achou, achou + len(alvo) - 1

    def tempo(self, a, mover=True):
        if a is None: return None
        if isinstance(a, T): return a.s
        if isinstance(a, (int, float)): return float(a)
        if isinstance(a, mais): return self.tempo(a.ancora, mover) + a.s
        if isinstance(a, fim): i, j = self._acha(a.txt, a.n, mover); return self.W[j]["e"]
        if isinstance(a, tuple): i, j = self._acha(a[0], a[1], mover); return self.W[i]["t"]
        i, j = self._acha(a, 0, mover); return self.W[i]["t"]

    def t(self, s): return T(s)
    def pula(self, ancora):
        """Move o cursor até a âncora sem criar nada (útil para desambiguar)."""
        self.tempo(ancora); return self

    def _ate(self, ate, padrao):
        if ate is None: return padrao
        c = self.cursor; t = self.tempo(ate, mover=False); self.cursor = c; return t

    # ---------------------------------------------------------------- itens
    def letreiro(self, *linhas, ate=None, dur=None, topo=None, atras=False, tam=None):
        ls = []
        for est, txt, anc, emo in linhas:
            ls.append([est, txt, round(self.tempo(anc), 3), emo])
        ini = min(l[2] for l in ls); fim_ = self._ate(ate, ini + (dur or 1.8))
        b = dict(linhas=ls, fim=round(fim_, 3), topo=topo if topo is not None else self.ES["letreiro"].get("topo_padrao", 0.12), atras=atras)
        if tam: b["tam"] = tam
        self.blocos.append(b); return self

    def broll(self, tipo, ancora, ate=None, dur=None, broll=None, ini=0.0, trans="whip", saida="corte", adianta=0.08, **kw):
        """tipo: cheia | canto | dividida | card. broll: ID do acervo (TSN-BR001, YT-91cecc, IMG-instagram, UP001...)."""
        t0 = self.tempo(ancora) - adianta; t1 = self._ate(ate, t0 + (dur or 2.5))
        it = self.acervo.get(broll)
        if not it: self.avisos.append(f"[{self.v}] B-roll {broll!r} não está no acervo do projeto"); return self
        c = dict(tipo=tipo, ini=round(max(0, t0), 3), fim=round(t1, 3), trans=trans, saida=saida, src=it["src"], id=it["id"],
                 slot="edicao", src_ini=ini); c.update(kw)
        if it.get("crop") and "crop" not in kw: c["crop"] = it["crop"]
        self.cenas.append(c); return self
    def card(self, ancora, **kw): return self.broll("card", ancora, **kw)

    def escuro(self, ancora, ate=None, dur=None):
        a = self.tempo(ancora) - 0.1; self.escuros.append([round(a, 3), round(self._ate(ate, a + (dur or 2.0)), 3)]); return self
    def pb(self, ancora, ate=None, dur=None, glitch=True):
        a = self.tempo(ancora) - 0.05; b = self._ate(ate, a + (dur or 2.5)); self.pbs.append([round(a, 3), round(b, 3)])
        if glitch: self.glitches.append([round(a, 3), 0.2])
        return self
    def glitch(self, ancora, dur=0.2): self.glitches.append([round(self.tempo(ancora), 3), dur]); return self
    def flash(self, ancora): self.flashes.append(round(self.tempo(ancora), 3)); return self

    def resultado(self):
        fixa = lambda l: sorted(l, key=lambda x: x[0] if isinstance(x, list) else x)
        for c in self.cenas:                        # o P&B do trecho vale para o B-roll que está por cima da pessoa
            if "pb" not in c and any(a < c["fim"] and b > c["ini"] for a, b in self.pbs): c["pb"] = True
        return dict(blocos=sorted(self.blocos, key=lambda b: min(l[2] for l in b["linhas"])), cenas=sorted(self.cenas, key=lambda c: c["ini"]),
                    escuro=fixa(self.escuros), pb=fixa(self.pbs), glitch=fixa(self.glitches), flash=sorted(self.flashes))
