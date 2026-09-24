#!/usr/bin/env python3
"""CLI da skill `.claude/skills/criar-template/`: o único jeito pelo qual ela toca em estilos/ ou fontes/.

Não fala com nenhum servidor — importa lib/template_escrita.py e app/servidor.py direto, no mesmo processo,
e escreve no disco deste repositório. Pensado pra rodar local, na máquina de quem está criando o template;
nunca é chamado pelo servidor publicado (não faz parte da imagem: veja .dockerignore).

Comandos:
  estudar <video> --quadros PASTA [--maximo-quadros N]
      Mede ritmo de corte, posição de legenda e som com ffmpeg (lib/estudo_video.py) e extrai os quadros
      representativos em PASTA. Imprime um JSON com os números e a lista de quadros — leia cada quadro com a
      ferramenta de imagem antes de decidir qualquer coisa sobre o template.

  criar NOME --estilo estilo.json [--leia LEIA.md] [--busca BUSCA.md] [--exemplo exemplo.md]
        [--exemplo-fonte exemplo_fonte.txt] [--amostra amostra.jpg] [--fonte arquivo.ttf ...]
      Grava estilos/NOME/ a partir dos arquivos dados, com a mesma validação de sempre
      (lib/template_escrita.criar_rascunho) — nunca sobrescreve nada. Cada --fonte é copiado para fontes/
      antes (sem sobrescrever uma fonte já existente com o mesmo nome); o "arquivo" que o estilo.json referencia
      tem que ser "fontes/<nome-do-arquivo>", igual todo template real.

  publicar NOME
      Libera um rascunho pra Kanban/Novo Vídeo (falha só se não existir ou já estiver publicado).

  proximo-nome BASE
      Nome livre pra "melhorar" um template existente sem reescrever nada (BASE, BASE-v2, BASE-v3...).
"""
import argparse
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "lib"))
sys.path.insert(0, os.path.join(RAIZ, "app"))


def _ler(caminho):
    with open(caminho, "rb") as f:
        return f.read()


def cmd_estudar(args):
    import estudo_video
    os.makedirs(args.quadros, exist_ok=True)
    medidas = estudo_video.estudar(args.video, args.quadros, maximo_quadros=args.maximo_quadros)
    print(json.dumps(medidas, ensure_ascii=False))


def cmd_criar(args):
    import template_escrita as te

    arquivos = {}
    for campo, caminho in (("estilo.json", args.estilo), ("LEIA.md", args.leia), ("BUSCA.md", args.busca),
                            ("exemplo.md", args.exemplo), ("exemplo_fonte.txt", args.exemplo_fonte),
                            ("amostra.jpg", args.amostra)):
        if caminho:
            arquivos[campo] = _ler(caminho)

    fontes_raiz = te._fontes_raiz()
    copiadas = []
    for origem in args.fonte or []:
        nome_arq = os.path.basename(origem)
        destino = os.path.join(fontes_raiz, nome_arq)
        if os.path.exists(destino):
            copiadas.append(f"fontes/{nome_arq} (já existia, não mexi)")
        else:
            os.makedirs(fontes_raiz, exist_ok=True)
            with open(destino, "wb") as saida:
                saida.write(_ler(origem))
            copiadas.append(f"fontes/{nome_arq} (copiada agora)")

    destino = te.criar_rascunho(args.nome, arquivos)
    print(json.dumps(dict(ok=True, destino=destino, fontes=copiadas), ensure_ascii=False))


def cmd_publicar(args):
    import servidor
    servidor.publicar_template(args.nome)
    print(json.dumps(dict(ok=True)))


def cmd_proximo_nome(args):
    import template_escrita as te
    print(te.proximo_nome_versao(args.base))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("estudar", help="mede ritmo/legenda/som do vídeo de referência e extrai os quadros")
    p.add_argument("video")
    p.add_argument("--quadros", required=True, help="pasta onde salvar os quadros extraídos")
    p.add_argument("--maximo-quadros", type=int, default=8, dest="maximo_quadros")
    p.set_defaults(func=cmd_estudar)

    p = sub.add_parser("criar", help="grava um rascunho novo em estilos/<nome>/")
    p.add_argument("nome")
    p.add_argument("--estilo", required=True, help="caminho do estilo.json")
    p.add_argument("--leia", help="caminho do LEIA.md")
    p.add_argument("--busca", help="caminho do BUSCA.md")
    p.add_argument("--exemplo")
    p.add_argument("--exemplo-fonte", dest="exemplo_fonte")
    p.add_argument("--amostra")
    p.add_argument("--fonte", action="append", help="arquivo .ttf/.otf a copiar para fontes/ antes de validar; pode repetir")
    p.set_defaults(func=cmd_criar)

    p = sub.add_parser("publicar", help="libera um rascunho pra Kanban/Novo Vídeo")
    p.add_argument("nome")
    p.set_defaults(func=cmd_publicar)

    p = sub.add_parser("proximo-nome", help="próximo nome de versão livre pra melhorar um template (ex.: base-v2)")
    p.add_argument("base")
    p.set_defaults(func=cmd_proximo_nome)

    args = ap.parse_args()
    try:
        args.func(args)
    except Exception as e:
        print(json.dumps(dict(ok=False, erro=str(e)), ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
