"""Conversa com o Claude pela API (roda no Python da .venv da skill, que tem o SDK oficial `anthropic`).

Cada etapa da edição automática abre uma Conversa: manda o material, recebe JSON no formato pedido
(structured outputs) e, na conferência, continua a mesma conversa com o relatório — assim o Claude corrige
o que ele mesmo fez, com o contexto inteiro (e o prefixo fica em cache, mais barato).

  .venv/bin/python lib/ia.py testar      -> testa a chave configurada (não gasta tokens)"""
import os, sys, json, time, base64
import anthropic

LIB = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, LIB)
import chaves
from custos import custo_claude
BETA_FALLBACK = "server-side-fallback-2026-07-01"

class Recusa(Exception): pass

class Uso:
    def __init__(self): self.ent = self.sai = self.cw = self.cr = 0; self.usd = 0.0; self.chamadas = 0
    def soma(self, modelo, u):
        ent, sai = u.input_tokens or 0, u.output_tokens or 0
        cw, cr = getattr(u, "cache_creation_input_tokens", 0) or 0, getattr(u, "cache_read_input_tokens", 0) or 0
        custo = custo_claude(modelo, ent, sai, cw, cr)
        self.ent += ent; self.sai += sai; self.cw += cw; self.cr += cr; self.usd += custo; self.chamadas += 1
        return custo
    def dict(self): return dict(entrada=self.ent, saida=self.sai, cache_escrita=self.cw, cache_leitura=self.cr, usd=round(self.usd, 4), chamadas=self.chamadas)

class Claude:
    def __init__(self, chave=None, modelo=None, log=print, ao_cobrar=None):
        """ao_cobrar(dict): chamado a cada resposta cobrada (modelo, tokens, US$) — é daí que sai a aba Custo."""
        d = chaves.ler(); chave = chave or d["anthropic"]
        if not chave: raise RuntimeError("a chave da API do Claude não está configurada (⚙ Configurações)")
        self.modelo = modelo or d.get("modelo") or "claude-opus-5"
        self.c = anthropic.Anthropic(api_key=chave, max_retries=4, timeout=1200.0)
        self.uso = Uso(); self.log = log; self.fallback = True; self.ao_cobrar = ao_cobrar

    def conversa(self, sistema, esforco="high"):
        return Conversa(self, sistema, esforco)

    def _chamar(self, sistema, msgs, schema, max_tokens, esforco):
        kw = dict(model=self.modelo, max_tokens=max_tokens, messages=msgs,
                  system=[{"type": "text", "text": sistema, "cache_control": {"type": "ephemeral"}}],
                  thinking={"type": "adaptive"}, cache_control={"type": "ephemeral"},
                  output_config={"effort": esforco, "format": {"type": "json_schema", "schema": schema}})
        if self.fallback: kw.update(betas=[BETA_FALLBACK], fallbacks="default")
        with self.c.beta.messages.stream(**kw) as s:
            return s.get_final_message()

class Conversa:
    def __init__(self, cl, sistema, esforco):
        self.cl, self.sistema, self.esforco, self.msgs = cl, sistema, esforco, []

    def pedir(self, conteudo, schema, max_tokens=64000, rotulo="Claude"):
        """conteudo: texto ou lista de blocos (use texto()/imagem()). Devolve o JSON já lido."""
        self.msgs.append({"role": "user", "content": conteudo})
        t0 = time.time(); esperas = 0; tentativa = -1
        while tentativa < 2:
            tentativa += 1
            try:
                final = self.cl._chamar(self.sistema, self.msgs, schema, max_tokens, self.esforco)
            except anthropic.RateLimitError as e:          # conta nova tem limite de tokens por minuto: espera e tenta de novo
                esperas += 1
                if esperas > 6: self.msgs.pop(); raise RuntimeError("a API recusou por limite de uso várias vezes seguidas (veja os limites da sua conta no console da Anthropic)")
                try: seg = int(e.response.headers.get("retry-after", "60"))
                except (ValueError, AttributeError): seg = 60
                self.cl.log(f"{rotulo}: limite de uso por minuto da API; esperando {seg}s…"); time.sleep(min(max(seg, 10), 120)); tentativa -= 1; continue
            except anthropic.BadRequestError as e:
                if self.cl.fallback and ("fallback" in str(e).lower() or "beta" in str(e).lower()):
                    self.cl.fallback = False; self.cl.log("(o modelo reserva não está disponível nesta conta; seguindo sem ele)"); continue
                self.msgs.pop(); raise
            except Exception:
                self.msgs.pop(); raise
            custo = self.cl.uso.soma(final.model, final.usage)
            if self.cl.ao_cobrar:
                u = final.usage
                self.cl.ao_cobrar(dict(rotulo=rotulo, modelo=final.model, entrada=u.input_tokens or 0, saida=u.output_tokens or 0,
                                       cache_escrita=getattr(u, "cache_creation_input_tokens", 0) or 0,
                                       cache_leitura=getattr(u, "cache_read_input_tokens", 0) or 0, usd=custo))
            if final.stop_reason == "refusal":
                self.msgs.pop(); det = getattr(final, "stop_details", None)
                raise Recusa(f"o Claude recusou este pedido ({getattr(det, 'category', None) or 'sem categoria'})")
            if final.stop_reason == "max_tokens":
                if tentativa < 2 and max_tokens < 128000:
                    max_tokens = min(128000, max_tokens * 2); self.cl.log(f"{rotulo}: resposta longa, pedindo de novo com mais espaço…"); continue
                self.msgs.pop(); raise RuntimeError("a resposta do Claude passou do limite de tamanho")
            texto = "".join(b.text for b in final.content if b.type == "text")
            try:
                dados = json.loads(texto)
            except ValueError:
                if tentativa < 2: self.cl.log(f"{rotulo}: resposta ilegível, pedindo de novo…"); continue
                self.msgs.pop(); raise RuntimeError("o Claude devolveu um JSON inválido")
            self.msgs.append({"role": "assistant", "content": final.content})
            u = final.usage
            self.cl.log(f"{rotulo}: respondeu em {time.time() - t0:.0f}s · {u.output_tokens} tokens escritos · US$ {custo:.3f}")
            return dados
        self.msgs.pop(); raise RuntimeError("o Claude não conseguiu responder")

def texto(t): return {"type": "text", "text": t}

def imagem(caminho, tipo="image/jpeg"):
    return {"type": "image", "source": {"type": "base64", "media_type": tipo, "data": base64.standard_b64encode(open(caminho, "rb").read()).decode()}}

def testar():
    d = chaves.ler()
    if not d["anthropic"]: return dict(ok=False, msg="nenhuma chave salva")
    try:
        c = anthropic.Anthropic(api_key=d["anthropic"], max_retries=1, timeout=20.0)
        m = c.models.retrieve(d.get("modelo") or "claude-opus-5")
        return dict(ok=True, msg=f"chave válida · modelo {m.display_name} disponível")
    except anthropic.AuthenticationError:
        return dict(ok=False, msg="chave inválida")
    except anthropic.PermissionDeniedError:
        return dict(ok=False, msg="a chave não tem permissão para este modelo")
    except anthropic.NotFoundError:
        return dict(ok=False, msg="o modelo escolhido não está disponível nesta conta")
    except anthropic.APIConnectionError:
        return dict(ok=False, msg="sem conexão com a API")
    except anthropic.APIStatusError as e:
        return dict(ok=False, msg=f"a API respondeu {e.status_code}")

if __name__ == "__main__":
    if sys.argv[1:] == ["testar"]: print(json.dumps(testar(), ensure_ascii=False))
