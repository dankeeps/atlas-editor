"""lib/template_escrita.py: o único jeito seguro de um template nascer — testa a trava de segurança
isoladamente, sem precisar de nenhuma skill nem estudo de vídeo de verdade (o "proponente" aqui é só um dict
Python)."""
import contextlib
import json
import threading
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def setUpModule():
    global template_escrita, comum
    import template_escrita
    import comum


def estilo_valido(**sobrepor):
    E = dict(
        nome="Meu Estilo de Teste",
        descricao="Um template de teste.",
        usa=dict(letreiro=True, formatos=["cheia"], escuro=False, pb=False, flash=False),
        processo=[dict(titulo="Transcreve", detalhe="...")],
        modo="broll_primeiro",
        letreiro=dict(
            estilos=dict(sans=dict(rotulo="Branca", familia="sans", px=96, cor=[255, 255, 255], tracking=-0.04, efeito="sombra")),
            efeitos=dict(sombra=[[0, 0, 0, 0.45, 26, 0]]),
        ),
        legenda=dict(familia="demi", tam=60, pad=30, x=0.5, y=0.7, max_palavras=3, max_caracteres=17, sombras=[[0, 0, 0, 0.5, 10, 0]]),
    )
    E.update(sobrepor)
    return E


class TemplateEscritaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas-template-escrita-")
        self.addCleanup(self.tmp.cleanup)
        self.skill = Path(self.tmp.name)
        (self.skill / "fontes").mkdir(parents=True)
        (self.skill / "fontes" / "Avenir.ttf").write_bytes(b"fake-font")
        (self.skill / "estilos").mkdir()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(template_escrita.comum, "SKILL", str(self.skill)))

    def arquivos(self, **sobrepor_estilo):
        return {"estilo.json": json.dumps(estilo_valido(**sobrepor_estilo)).encode("utf-8")}

    def test_cria_template_valido_como_rascunho(self):
        destino = template_escrita.criar_rascunho("meu-estilo-novo", self.arquivos())
        self.assertEqual(Path(destino), self.skill / "estilos" / "meu-estilo-novo")
        gravado = json.load(open(Path(destino) / "estilo.json"))
        self.assertFalse(gravado["publicado"])  # nasce sempre rascunho

    def test_ignora_publicado_true_vindo_do_payload(self):
        destino = template_escrita.criar_rascunho("outro-estilo", self.arquivos(publicado=True))
        gravado = json.load(open(Path(destino) / "estilo.json"))
        self.assertFalse(gravado["publicado"])

    def test_recusa_arquivo_py(self):
        arqs = self.arquivos(); arqs["sfx_regras.py"] = b"import os"
        with self.assertRaises(template_escrita.TemplateInvalido):
            template_escrita.criar_rascunho("com-python", arqs)
        self.assertFalse((self.skill / "estilos" / "com-python").exists())

    def test_recusa_extensao_desconhecida(self):
        arqs = self.arquivos(); arqs["script.sh"] = b"echo oi"
        with self.assertRaises(template_escrita.TemplateInvalido):
            template_escrita.criar_rascunho("com-shell", arqs)

    def test_recusa_nome_repetido_mesmo_ainda_rascunho(self):
        # criar_rascunho é só-cria, sempre — mesmo pra um rascunho seu que ainda nem foi publicado. "Melhorar"
        # um template é pedir um nome de versão novo (proximo_nome_versao), nunca reescrever o que já existe.
        template_escrita.criar_rascunho("ja-existe", self.arquivos())
        with self.assertRaises(template_escrita.TemplateInvalido):
            template_escrita.criar_rascunho("ja-existe", self.arquivos(descricao="tentativa de sobrescrever"))
        gravado = json.load(open(self.skill / "estilos" / "ja-existe" / "estilo.json"))
        self.assertNotEqual(gravado["descricao"], "tentativa de sobrescrever")  # o original não mudou

    def test_recusa_nome_de_template_ja_publicado(self):
        destino = template_escrita.criar_rascunho("ja-publicado", self.arquivos())
        E = json.load(open(Path(destino) / "estilo.json")); E["publicado"] = True
        json.dump(E, open(Path(destino) / "estilo.json", "w"))
        with self.assertRaises(template_escrita.TemplateInvalido):
            template_escrita.criar_rascunho("ja-publicado", self.arquivos())

    def test_marca_origem_claude(self):
        destino = template_escrita.criar_rascunho("com-origem", self.arquivos())
        gravado = json.load(open(Path(destino) / "estilo.json"))
        self.assertEqual(gravado["origem"], "claude")

    def test_recusa_json_aninhado_demais_sem_derrubar_com_recursionerror(self):
        # sem a checagem de profundidade, isso derruba o parser recursivo do json com RecursionError em vez
        # de um erro tratável — nem precisa ser grande em bytes, só muito aninhado.
        malicioso = (b"[" * 100_000) + (b"]" * 100_000)
        with self.assertRaises(template_escrita.TemplateInvalido):
            template_escrita.criar_rascunho("ataque-aninhamento", {"estilo.json": malicioso})

    def test_aceita_aninhamento_normal_de_um_estilo_json_real(self):
        # a checagem de profundidade não pode ser tão agressiva a ponto de recusar um template real —
        # já confirmado por test_valida_o_estilo_json_real_do_ultradinamico, aqui só confere a função isolada.
        real = open(ROOT / "estilos" / "ultradinamico" / "estilo.json", "rb").read()
        self.assertFalse(template_escrita._profundidade_excessiva(real))

    def test_aninhamento_dentro_de_string_nao_conta(self):
        # colchetes dentro de um texto (ex.: descrição mencionando "[assim]") não são estrutura de verdade.
        E = estilo_valido(descricao="[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[texto com colchetes]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]")
        template_escrita.criar_rascunho("com-colchetes-no-texto", {"estilo.json": json.dumps(E).encode()})  # não levanta

    def test_recusa_nome_com_barra_ou_travessia(self):
        for nome in ("../fora", "a/b", "..", ".oculto", "Maiuscula", "com espaco"):
            with self.assertRaises(template_escrita.TemplateInvalido, msg=nome):
                template_escrita.criar_rascunho(nome, self.arquivos())

    def test_recusa_sem_estilo_json(self):
        with self.assertRaises(template_escrita.TemplateInvalido):
            template_escrita.criar_rascunho("sem-nada", {"LEIA.md": b"oi"})

    def test_recusa_json_invalido(self):
        with self.assertRaises(template_escrita.TemplateInvalido):
            template_escrita.criar_rascunho("json-quebrado", {"estilo.json": b"{nao e json"})

    def test_recusa_sem_letreiro_estilos(self):
        E = estilo_valido(); E["letreiro"] = dict(estilos={}, efeitos={})
        with self.assertRaises(template_escrita.TemplateInvalido):
            template_escrita.criar_rascunho("sem-letreiro", {"estilo.json": json.dumps(E).encode()})

    def test_recusa_cor_fora_do_padrao(self):
        E = estilo_valido(); E["letreiro"]["estilos"]["sans"]["cor"] = [999, 0, 0]
        with self.assertRaises(template_escrita.TemplateInvalido):
            template_escrita.criar_rascunho("cor-invalida", {"estilo.json": json.dumps(E).encode()})

    def test_recusa_efeito_nao_definido(self):
        E = estilo_valido(); E["letreiro"]["estilos"]["sans"]["efeito"] = "brilho_fantasma"
        with self.assertRaises(template_escrita.TemplateInvalido):
            template_escrita.criar_rascunho("efeito-invalido", {"estilo.json": json.dumps(E).encode()})

    def test_recusa_formato_canto_sem_campos_de_legenda(self):
        E = estilo_valido(); E["usa"]["formatos"] = ["canto"]
        with self.assertRaises(template_escrita.TemplateInvalido):
            template_escrita.criar_rascunho("canto-incompleto", {"estilo.json": json.dumps(E).encode()})

    def test_aceita_formato_canto_com_campos_de_legenda(self):
        E = estilo_valido(); E["usa"]["formatos"] = ["canto"]; E["legenda"]["canto_x"] = [0.3, 0.7]; E["legenda"]["canto_dy"] = -0.1
        template_escrita.criar_rascunho("canto-completo", {"estilo.json": json.dumps(E).encode()})  # não levanta

    def test_recusa_fonte_fora_da_pasta_de_fontes(self):
        E = estilo_valido(); E["fontes"] = {"x": {"arquivo": "../../etc/passwd"}}
        with self.assertRaises(template_escrita.TemplateInvalido):
            template_escrita.criar_rascunho("fonte-traversal", {"estilo.json": json.dumps(E).encode()})

    def test_recusa_fonte_inexistente(self):
        E = estilo_valido(); E["fontes"] = {"x": {"arquivo": "fontes/NaoExiste.ttf"}}
        with self.assertRaises(template_escrita.TemplateInvalido):
            template_escrita.criar_rascunho("fonte-inexistente", {"estilo.json": json.dumps(E).encode()})

    def test_recusa_fonte_sem_prefixo_fontes(self):
        # "Avenir.ttf" (sem o "fontes/" na frente) resolveria certinho por fontes_raiz, mas lib/motor.py
        # resolve "arquivo" a partir da raiz do repositório — sem o prefixo, o render não acharia o arquivo.
        E = estilo_valido(); E["fontes"] = {"x": {"arquivo": "Avenir.ttf"}}
        with self.assertRaises(template_escrita.TemplateInvalido):
            template_escrita.criar_rascunho("fonte-sem-prefixo", {"estilo.json": json.dumps(E).encode()})

    def test_aceita_fonte_existente(self):
        E = estilo_valido(); E["fontes"] = {"x": {"arquivo": "fontes/Avenir.ttf"}}
        template_escrita.criar_rascunho("fonte-ok", {"estilo.json": json.dumps(E).encode()})  # não levanta

    def test_recusa_arquivo_grande_demais(self):
        arqs = self.arquivos(); arqs["LEIA.md"] = b"x" * (template_escrita.TAMANHO_MAX["LEIA.md"] + 1)
        with self.assertRaises(template_escrita.TemplateInvalido):
            template_escrita.criar_rascunho("grande-demais", arqs)

    def test_duas_criacoes_do_mesmo_nome_ao_mesmo_tempo_so_uma_vence(self):
        resultados = []
        def tentar(descricao):
            try: template_escrita.criar_rascunho("corrida", self.arquivos(descricao=descricao)); resultados.append("ok")
            except template_escrita.TemplateInvalido: resultados.append("recusado")
        t1 = threading.Thread(target=tentar, args=("versao A",)); t2 = threading.Thread(target=tentar, args=("versao B",))
        t1.start(); t2.start(); t1.join(); t2.join()
        self.assertEqual(sorted(resultados), ["ok", "recusado"])

    def test_valida_o_estilo_json_real_do_ultradinamico(self):
        real = json.load(open(ROOT / "estilos" / "ultradinamico" / "estilo.json"))
        for chave, F in real.get("fontes", {}).items():
            # o "arquivo" do template real fica como está (com o prefixo "fontes/") — só cria o arquivo
            # fake no mesmo caminho relativo, pra provar que o valor de verdade passa sem precisar reescrevê-lo.
            (self.skill / F["arquivo"]).parent.mkdir(parents=True, exist_ok=True)
            (self.skill / F["arquivo"]).write_bytes(b"fake")
        real["nome"] = "Copia de teste"
        template_escrita.criar_rascunho("copia-do-ultradinamico", {"estilo.json": json.dumps(real).encode("utf-8")})  # não levanta


class ProximoNomeVersaoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas-proximo-versao-")
        self.addCleanup(self.tmp.cleanup)
        self.skill = Path(self.tmp.name)
        (self.skill / "estilos").mkdir(parents=True)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(template_escrita.comum, "SKILL", str(self.skill)))

    def test_primeira_vez_devolve_o_proprio_nome_base(self):
        self.assertEqual(template_escrita.proximo_nome_versao("meu-estilo"), "meu-estilo")

    def test_com_o_base_ja_existindo_devolve_v2(self):
        (self.skill / "estilos" / "meu-estilo").mkdir()
        self.assertEqual(template_escrita.proximo_nome_versao("meu-estilo"), "meu-estilo-v2")

    def test_pula_pras_versoes_seguintes_que_ja_existem(self):
        for nome in ("meu-estilo", "meu-estilo-v2", "meu-estilo-v3"):
            (self.skill / "estilos" / nome).mkdir()
        self.assertEqual(template_escrita.proximo_nome_versao("meu-estilo"), "meu-estilo-v4")

    def test_recusa_base_invalido(self):
        with self.assertRaises(template_escrita.TemplateInvalido):
            template_escrita.proximo_nome_versao("../fora")


if __name__ == "__main__":
    unittest.main()
