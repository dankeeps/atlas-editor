---
name: estudio-edicao
description: Estúdio de edição de VSL e anúncio em vídeo vertical com modelos de edição salvos (Ultradinâmico, Ultradinâmico Criativo, Edição orgânica), feito por mim no chat ou sozinho pela tela do Estúdio (API do Claude + Gemini assistindo os vídeos + Apify): corta silêncios e erros comparando a fala com o roteiro, gera versões por lead, põe letreiros, legenda, B-roll (tela cheia com transição, recorte no canto, tela dividida, card), modo escuro, P&B e efeitos sonoros, entrega tudo num editor de timeline no navegador com tela inicial dos vídeos, e renderiza em paralelo. Use quando o usuário pedir para editar um vídeo/VSL/anúncio/criativo "no modelo X" ou "igual ao anterior", tirar silêncios e erros, trocar o B-roll N, mexer em letreiro/legenda/efeito sonoro de um vídeo já editado, renderizar de novo, abrir o editor ou a tela inicial dos vídeos, ou salvar um estilo de edição novo. A busca de B-roll (TikTok e YouTube) é feita aqui mesmo e tudo que vem fica na biblioteca de B-rolls (~/B-rolls/biblioteca). Não use para escrever copy (obsidian-copy).
---

# Estúdio de Edição

Sistema fixo: a edição é decidida por mim no chat **ou** pela edição automática da tela inicial, as duas no mesmo
processo (seção "Edição automática"). Os scripts fazem o resto: corte, máscara, prévia, som, render e o editor. O editor
serve para o usuário revisar, assistir e trocar B-roll; tudo que ele mexe lá fica salvo no `plano.json` da versão e é
respeitado pelo render.

O pedido típico: **"edita esse criativo no modelo X"**. Modelos em `estilos/` (seção Modelos de edição).

- Projetos: `~/Edições/<Projeto>/` · Estilos: `estilos/<nome>/` · Sons: `sfx/` (catálogo `sfx.json`)
- CLI: `/usr/bin/python3 ~/.claude/skills/estudio-edicao/lib/estudio.py <comando>` (chame de `E` abaixo)
- Tela inicial + editor: `E abrir` (porta 8790). No app, `preview_start` com `estudio` (`~/.claude/launch.json`).
- O usuário abre sozinho com clique duplo em `~/Edições/Abrir Estúdio.command`.

## Vídeo novo — 5 etapas, direto

Sem desvio: não precisa de outra skill nem de cadastro em Obsidian. Pedido: "edita o criativo X no modelo Y".

1. **Saber qual é e transcrever.** `E novo --nome "<nome>" --video <arquivo> --estilo <modelo> [--roteiro R.docx]`
   (transcrição palavra por palavra, ~1 min para 10 min de vídeo). Vídeo bruto com roteiro: `E cortes` e
   `E cortes --aplicar` tiram silêncio e erro (última tomada boa de cada frase, leads viram versões A, B…; confira
   `versoes/<V>/verificacao.md`). Vídeo já cortado ("CORTADO"): uma faixa só em `fonte/cortes.json` e `--aplicar`
   tira só os silêncios. Depois `E preparar` (recorte da pessoa, prévia), que pode rodar enquanto você planeja.
2. **Planejar.** Leia `estilos/<modelo>/LEIA.md`. Em cima da fala (`E rascunho` põe a fala com o tempo em
   `edicao.py`), decida onde entra cada letreiro (palavra-chave), cada B-roll (momento com imagem literal, formato) e o
   modo escuro. Daí sai a lista do que buscar: uma busca por tipo de imagem (3 a 5 buscas num anúncio de 1 a 2 min).
3. **Buscar os B-rolls desses momentos: biblioteca primeiro.** Biblioteca = `~/B-rolls/biblioteca/` (aba B-rolls do
   Estúdio; `lib/biblioteca.py`). Tudo que qualquer busca trouxe fica lá com o que o Gemini viu (descrição, tags,
   trechos limpos, onde aparece texto). **Disponível** pode entrar; **usado** (já entrou num vídeo renderizado) não volta.
   - `biblioteca.py procurar "mulher agachando com barra" --nicho Glúteo` → os disponíveis que combinam.
   - Faltou: `tiktok.py buscar "<projeto>" "<nome>" "termo em inglês" "termo" ...` (Apify, EUA, vertical). Baixa TODOS os
     candidatos para a biblioteca e o Gemini estuda cada um (se houver chave). Mostra `contato-N.jpg` com o ID de cada
     um. `tiktok.py quadros "<projeto>" <busca> 3,7` → 5 quadros de cada, com o segundo, para escolher o trecho limpo.
   - Corte horizontal (famoso, filme, lugar, card): `lib/youtube.py` (grátis) `buscar` → `quadros` → `baixar_para`.
   - Escolhidos: `E acervo "<projeto>" --biblioteca B0012 B0015 …` (entram no acervo do editor). O render marca os
     que entraram como usados.
   - **Nunca** pegue B-roll solto do computador (Downloads etc.), a não ser pasta que o usuário indicar:
     `E acervo "<projeto>" --pasta <pasta>`. Prints e imagens do cliente vão em `<projeto>/assets/`.
4. **Montar.** Escreva `editar(E)` no `edicao.py` com âncoras em palavras (sintaxe no topo de `lib/dsl.py`; um arquivo
   para todas as versões, `if E.v == "A":` só no lead). Use `ini` (segundo do clipe) no trecho limpo que você viu nos
   quadros, e `crop=[x, y, w, h]` para tirar texto miúdo ou enquadrar a tela dividida. `E plano "<projeto>"` → plano +
   legendas; se o modelo tem regras, ele lista cada "regra:" quebrada: corrija até sair limpo. Então
   `motor.py <versão> preview t1 t2 …` em ~20 pontos e **olhe os quadros** (texto em cima de rosto, legenda queimada
   do clipe, recorte ruim). **Nunca** `plano --forcar` depois que o usuário editou no editor, a não ser que ele peça.
5. **Renderizar e entregar.** `E render "<projeto>"` (paralelo) → `versoes/<V>/renders/<nome> revN.mp4`, aparece na
   tela inicial. Entregue o caminho e o que foi feito; ajustes vêm pelo chat ou pelo editor (`E conferir` depois).

## Chat do Estúdio (💬 Editar por chat)

O mesmo Claude Code desta máquina rodando dentro do Estúdio, pago pela chave da API do ⚙: é o **Claude Agent SDK**
(`claude-agent-sdk` na `.venv`, traz o próprio motor do Claude Code). Mesmo modelo, esforço, system prompt, ferramentas,
skills, memórias (`~/.claude/projects/<pasta-do-seu-projeto>/memory/`) e configurações do usuário: `cwd` = home e
`setting_sources` user/project/local. Quem pede edição ali recebe o mesmo trabalho feito aqui — esta skill vale igual.

- `app/chat.html` (tela) · `lib/chat.py` (um processo por conversa, Python da `.venv`) · conversas em `~/Edições/.chats/<id>/`
  (`meta.json` título/sessão/modo/custo, `eventos.jsonl` o que a tela mostra, `entrada/` comandos da tela, `anexos/`, `img/`
  imagens que o Claude leu, `motor.log`). Ocioso 30 min, o processo fecha; a próxima mensagem retoma a sessão (`resume`).
- Modos: Automático (classificador, como aqui) · Perguntar antes · Aceitar edições · Sem perguntar · Só planejar.
  Permissão, pergunta (AskUserQuestion) e aprovação de plano viram cartões na tela.
- Na resposta, caminho completo de vídeo/imagem vira player/miniatura: ao entregar render no chat, escreva o caminho.
- Custo: o motor informa o acumulado da sessão; entra na aba Custo como "Conversas no chat".
- O servidor tira do ambiente as variáveis `CLAUDE*`/`ANTHROPIC*` (o app do Claude as coloca) antes de abrir o processo,
  para usar só a chave da API.
- Teste sem gastar: `ESTUDIO_CHAT_API=http://127.0.0.1:<porta>` aponta o motor para uma API falsa (ver `referencias/tecnico.md`).

## Edição automática (tela inicial, sem chat)

**⚙ Configurações**: chave do Claude (decide), **chave da OpenRouter** (o Gemini estuda cada B-roll por ela; a chave do
Google é alternativa), token da Apify (TikTok), modelo do Claude e **quem estuda cada B-roll** (Gemini padrão; Claude
Opus/Sonnet pela folha de quadros quando não há chave do Gemini). **+ Novo vídeo**: vídeo, roteiro opcional, modelo de edição, nicho, busca no
TikTok/YouTube, "conferir quadro a quadro" (desligado). O servidor grava `<projeto>/auto/pedido.json` e roda `lib/auto.py`
na `.venv`. Fluxo (2026-09-19, pedido do cliente): **transcreve → planeja → biblioteca → busca o que falta → monta**; o
que não ficar bom ele troca no editor.

1. transcrição · 2. cortes (Claude escolhe as tomadas) · 3. recorte da pessoa, em paralelo
4. **plano** (`prompts/edicao.md`): a DISTRIBUIÇÃO sai daqui — letreiros, efeitos e MOMENTOS de B-roll pela fala, com a
   edição aprovada do modelo como régua (`estilos/<modelo>/exemplo.md`, gerado por `lib/exemplo.py "<projeto>" A`) →
   `ops.construir` + `regras.py` conferem as regras do modelo (inclusive letreiro curto, B-roll a cada ≤10 s, mistura de
   formatos) → Claude corrige
5. **B-roll** (`prompts/broll.md`): cada clipe tem uma FICHA feita uma vez (`lib/ficha.py`: o olhar de editor — linha do
   tempo, melhor momento, notas de luz/nitidez/movimento/assunto/natural, formatos com lado livre do canto e centro da
   dividida, texto queimado com a faixa da altura, o que ilustra, pontos fracos; Gemini assistindo o vídeo, ou Claude pela
   folha de quadros em `lib/estudo.py`) — os sem ficha do nicho são estudados no começo; biblioteca primeiro → o que faltar vai
   para o TikTok (tudo que vem fica na biblioteca, com ficha) e YouTube (trecho achado pelos quadros) → Claude escolhe
   clipe + segundo lendo as fichas; `ajusta_escolha` põe `ini` em trecho limpo e recorta legenda no topo/base
6. **montagem**: edição final (`edicao_ia.json`) → conferência automática das regras → corrige
7. **conferência quadro a quadro**: só com "conferir" marcado (mais cara)
8. **render** → os B-rolls que entraram viram "usados" na biblioteca.

- Chaves: `~/.config/estudio-edicao/chaves.json` (600). Nunca escreva chave em outro arquivo nem no chat.
- Estado e registro: `auto/estado.json`, `auto/conversas/` (o que o Claude recebeu e respondeu), `auto/erro.txt`.
- Aba **Custo**: Claude, Gemini e Apify por vídeo (`custos.json`). Aba **B-rolls**: disponíveis/usados, busca por
  texto/tag, "Estudar N clipes" (processo na `.venv`, progresso em `~/B-rolls/biblioteca/estudo_progresso.json`).
- Teste sem gastar: `ESTUDIO_RAIZ`, `ESTUDIO_BIBLIOTECA`, `ESTUDIO_CHAVES` e Claude/Apify simulados (exemplo em
  `referencias/tecnico.md`).
- Para avaliar a edição automática, use um criativo que NÃO é o do exemplo aprovado (o do exemplo ela copiaria).
- **Ultradinâmico Criativo = "B-roll primeiro"** (`"modo": "broll_primeiro"` no estilo.json; etapas `encaixe`, `revisao`,
  `letreiros` no lugar de plano/broll/montagem): o Claude recebe a fala + as fichas dos clipes que combinam com o anúncio
  (`fichas_para`) e encaixa cada B-roll onde ele serve melhor (sem forçar; o que faltar vira `faltas`) → revisão (duplo
  check): trechos longos sem B-roll + faltas → clipe não usado, busca específica no TikTok (até 3; o Gemini estuda na hora)
  ou deixar → letreiros e efeitos nos intervalos → regras. Os outros modelos seguem "plano primeiro".
- Aba **Busca de B-roll** (`lib/leva.py`, pasta `~/B-rolls/<Expert>/<Oferta>/.levas/<data-hora>/`): sobe 1 ou vários anúncios +
  escolhe expert e oferta (pastas de `~/B-rolls`) → transcreve todos (`lib/transcrever.py`, no Mac) → o Claude lê as falas
  juntas no TEMPLATE escolhido (guia, regras e edição aprovada) e faz o plano de B-roll de cada anúncio (quantos B-rolls
  pela duração e ritmo, que imagem em cada momento), confere o que a pasta já tem e só busca o que falta (máx. 18) → Apify →
  Gemini estuda → cada clipe vai para `<Oferta>/<Categoria>/` (descartados em `.descartados`) com a etiqueta dos anúncios
  (`anuncios`, `levas` no índice). Na edição, `auto.anuncio_da_leva` reconhece o anúncio pela fala e os clipes com a
  etiqueta dele vêm primeiro (★ nas fichas), depois o resto da oferta.
- **Procura de B-roll em 3 níveis, sem sair da pasta** (`auto.filtro`): o "+ Novo vídeo" escolhe a pasta de B-roll (Expert ›
  Oferta) e o anúncio (etiqueta da Busca de B-roll; "automático" reconhece pela fala entre os anúncios dessa pasta, "nenhum"
  pula). 1º clipes com a etiqueta do anúncio, 2º o resto da pasta, 3º busca no TikTok só se faltar (salva na mesma pasta).
  Projeto antigo sem expert/oferta cai no nicho.
- Os dois Ultradinâmicos editam em "B-roll primeiro" (transcreve → cortes → B-rolls do anúncio e da oferta → montagem no
  formato + duplo check → letreiros e efeitos sonoros). A Edição orgânica segue "plano primeiro".
- Aba **Templates** (tela inicial): nome, descrição, sequência (`"processo"` no estilo.json), regras em frases
  (`regras.texto`), exemplo aprovado e guia (LEIA.md) de cada modelo. Mudou a sequência de um modelo, mude o `processo`.

## Pedidos depois da entrega

- **"Troca o B-roll 2 da versão A"**: os B-rolls são numerados na ordem do tempo (a timeline mostra "B-roll N").
  Ache um melhor no acervo ou na biblioteca (ou busque com `lib/tiktok.py`; `E acervo --biblioteca ID`), e
  `E broll "<projeto>" A 2 --id TSN-BR0xx [--ini 3.0] [--tipo canto]`. Depois `E render --versao A`.
- **Letreiro, legenda, efeito, som**: edite o `plano.json` da versão (ou peça para o usuário usar o editor).
  Som: `sfx_edicoes[chave] = {f, g, dt, off}` e `sfx_extras` (a chave aparece em `sfx.txt` depois do render).
- **Sempre** preserve o que o usuário mudou no editor; mude só o que ele pediu.

## Modelos de edição

`estilos/<nome>/estilo.json` (fontes, tamanhos, cores, sombras, animação, legenda, zoom, transições, recorte,
card, modo escuro) + `sfx_regras.py` (qual som entra em qual elemento) + `LEIA.md` (como aplicar: ritmo,
quando usar cada formato). O motor (`lib/motor.py`), a prévia (`app/editor.html`) e a mixagem (`lib/mix.py`)
leem daí. **Estilo novo** a partir de uma referência: copie a pasta de um estilo parecido, ajuste os números
e as regras de som olhando a referência quadro a quadro, e escreva o `LEIA.md`. O projeto escolhe o modelo em
`projeto.json` → `estilo` (`E novo --estilo <nome>`). Regras de ritmo opcionais em `estilo.json` → `regras`
(`lib/regras.py`).

- **Ultradinâmico** (`ultradinamico`): letreiro serifado azul na palavra-chave, B-roll em 4 formatos, modo escuro, som
  em toda entrada. Exemplo: TSN VSL Parte 1. O usuário disse que este está perfeito: não mexa.
- **Ultradinâmico Criativo** (`ultradinamico-criativo`): o mesmo visual para criativo, **sem empilhar**: um elemento por
  vez (letreiro fora do B-roll, a não ser a lista de ✅), canto ≥ 3 s, 1,5 s só com a pessoa entre um B-roll e outro
  (direto só dividida → cheia e cheia → cheia), letreiro espera 0,6 s depois que o B-roll sai. Conferido pelo `plano`.
- **Edição orgânica** (`organico`): sem letreiro e sem figura (o cliente não gostou das figuras de IA), legenda nativa
  pequena, tudo em B-roll na palavra certa alternando só a pessoa / `canto` / `cheia` com transição / `dividida`,
  whoosh leve só nas transições, zoom que muda a cada corte. Exemplo: AM29 orgânico (rev2); a v1 com figuras ficou
  em `historico/edicao_v1_figuras.py`.

## Onde fica cada coisa

| O quê | Onde |
|---|---|
| Projeto (vídeo original, transcrição, cortes) | `~/Edições/<Projeto>/fonte/` |
| Cada versão (jump cut, plano, legendas) | `~/Edições/<Projeto>/versoes/<V>/` |
| Vídeo final | `~/Edições/<Projeto>/versoes/<V>/renders/<nome> revN.mp4` (e na tela inicial) |
| Buscas do vídeo (folhas de contato, quadros) | `~/Edições/<Projeto>/busca/<busca>/` |
| Print, imagem do cliente, pasta indicada | `~/Edições/<Projeto>/assets/` |
| Acervo antigo (obsidian-broll, importado para a biblioteca) | `~/B-rolls/<Expert>/…` + fichas em `~/Keeps/03 Edição/…` |
| Conversas do chat do Estúdio | `~/Edições/.chats/<id>/` (anexos em `anexos/`) |
| Todos os B-rolls (aba B-rolls navega por essas pastas) | `~/B-rolls/<Expert>/<Oferta>/<Categoria>/`; descartados em `<Oferta>/.descartados/`; levas em `<Oferta>/.levas/`; índice em `~/B-rolls/biblioteca/biblioteca.json` |
| Modelos de edição | `~/.claude/skills/estudio-edicao/estilos/<modelo>/` |
| Efeitos sonoros | `~/.claude/skills/estudio-edicao/sfx/` |

## Regras que valem sempre

- O motor e a prévia são espelhos: mudou um, mude o outro e compare lado a lado (`/api/png` + `motor.py preview`).
- **B-roll**: biblioteca primeiro (só disponíveis; usado não volta), depois busca nova; do computador só da pasta que o
  usuário indicar. Buscas em paralelo podem; downloads da obsidian-broll em paralelo não (IDs colidem).
- Processo iniciado pelo app **não lê ~/Downloads** (privacidade do macOS): tudo que o editor precisa mora dentro
  de `~/Edições/<projeto>/` ou `~/B-rolls/`. Copie prints e vídeos do cliente para `assets/`.
- Não prometa o que não ouviu: eu não escuto áudio; confiro tempo e volume por medição.
- Detalhes técnicos, armadilhas e o formato dos arquivos: `referencias/tecnico.md`.

## Kanban (aba do Estúdio)

O quadro onde os anúncios andam pelas fases, com uma fila só: `lib/kanban.py` + `~/Edições/.kanban/quadro.json`.
Colunas: **novas → broll → cortes (revisar) → edicao (revisar) → render → pronto**. O card entra na coluna, roda
sozinho e fica lá esperando você arrastar. Um trabalho pesado por vez, tudo com `nice -n 10` (o editor tem que
continuar liso) e dá para pausar a fila.

- **broll** roda por LOTE: uma leva (`lib/leva.py`) com todos os anúncios da pasta — a mesma busca de sempre.
- **cortes/edicao/render** rodam por card: `auto.py <projeto> --ate preparar|letreiros|render`. O projeto é criado
  na hora, reaproveitando a transcrição que a leva já fez (`estudio.py novo --whisper <json>`).
- **Revisão dos cortes**: `/cortes?p=<projeto>&v=A&k=<card>` (`app/cortes.html`) — onda do áudio, trechos removidos
  em azul, arrastar as bordas, ✕ devolve o trecho, prévia pulando o que está em azul. Salvar grava `segs` em
  `fonte/cortes.json` e refaz o jump cut (~20 s num vídeo de 20 s; ~1 min num de 1:20).
