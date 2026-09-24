# Referência técnica do Estúdio

## Estrutura de um projeto (`~/Edições/<Projeto>/`)

```
projeto.json            nome, estilo (modelo), expert, oferta, sigla, fonte_video, roteiro, parte, versoes[{id, nome}],
                        buscas[] (IDs das buscas de B-roll deste vídeo; sem a lista = projeto antigo, todas as fichas da sigla)
fonte/                  voz16k.wav, voz48k.wav, whisper.json (fonte inteira), roteiro.txt, cortes.json, cortes.md
edicao.py               roteiro de edição (âncoras em palavras) -> plano de cada versão
assets/                 B-rolls baixados para o vídeo (tt-*.mp4 do TikTok, yt-*.mp4 do YouTube) + prints/imagens/vídeos do cliente
busca/<busca>/          buscas do TikTok deste vídeo: busca.json, capas/, contato-N.jpg, videos/ (cópias para olhar), quadros.jpg
acervo/                 acervo.json (fichas do Obsidian das buscas do projeto + assets/), uploads.json (enviados pelo editor), proxy/, thumbs/
uploads/                arquivos enviados pelo botão "Enviar do computador"
versoes/<V>/            jc.mov (jump cut 1440x2560), voz.wav, voz16k.wav, whisper.json, mapa.json, masks/ (PNG por quadro),
                        pm.mp4 (proxy: apresentador | máscara lado a lado, com a voz), ganhos.json, plano.json,
                        historico/ (planos anteriores), renders/ (<nome> revN.mp4), capa.jpg, mix.wav, sfx.txt, verificacao.md
```

## assets/ (arquivos do projeto que entram no acervo)

- `assets/*.mp4|mov` -> `AS-<nome>`, `assets/*.jpg|png` -> `IMG-<nome>` (categoria "Assets do projeto").
- `assets/tiktok.json` (formato antigo, ainda lido): B-roll do TikTok baixado direto no projeto -> ID `TT-<nome>`.
- `assets/youtube.json` {arquivo: {url, titulo, canal, ini, fim, descricao, quando}}: cortes do YouTube -> ID `YT-<nome>`,
  categoria "YouTube", descrição + canal, `origem` = link. Subpasta de `assets/` não entra no acervo.
- Arquivo vazio (0 byte, corte ainda baixando) é ignorado. `E acervo --pasta DIR` copia para cá a pasta que o usuário indicar.

## Biblioteca de B-rolls (`~/B-rolls/biblioteca/`, `lib/biblioteca.py`)

- `biblioteca.json` = `{itens: {B0001: {...}}, proximo}`; trava em `.trava` (servidor, edição automática e chat mexem juntos).
- Item: `id, arquivo, fonte (tiktok|youtube|arquivo), url, autor, views, termo, busca, nicho, titulo, tiktok_id | video_id+yt_ini,
  chave (evita duplicar), dur, w, h, capa, baixado, estado (disponivel|usado), usos [{projeto, versao, quando}]`
  + o estudo do Gemini: `descricao, tags, nota, serve, estudo {trechos_limpos [{ini,fim,o_que}], texto_na_tela [{ini,fim,onde,
  tamanho,texto}], fala_para_camera, cortes_de_cena, posicao_assunto, marca_dagua, problemas}, estudado_por, estudado_em`.
- Vídeos em `videos/<tiktok_id>.mp4` (TikTok) e `videos/yt-<id>-<ini>.mp4` (trecho do YouTube); os importados do Obsidian
  continuam onde estavam. `procurar()` só devolve disponíveis já estudados. `render.py` (sem `--rascunho`) marca usados.
- Projeto: `projeto.json -> biblioteca` = IDs separados para o vídeo; `estudio.py acervo` põe esses no acervo do editor
  (o servidor ainda mostra no editor os disponíveis do mesmo nicho, categoria "Biblioteca · disponíveis").

## Edição automática (`lib/auto.py`)

- `auto/pedido.json` (nome, estilo, video, roteiro, nicho, buscar_tiktok, buscar_youtube, renderizar), `auto/estado.json`,
  `auto/plano.json` (plano: letreiros, momentos, efeitos), `auto/escolhas.json` (momento -> broll, ini, recorte, enquadra),
  `edicao_ia.json` (montagem), `auto/relatorio_edicao.json`, `auto/conferencia_R_N.jpg`, `auto/rascunho_<V>.mp4` +
  `auto/rascunho.json` (hash dos planos: se nada mudou depois da conferência, o rascunho vira o final), `auto/conversas/`.
- `edicao_ia.json`: letreiros `{linhas [{estilo, texto, em, emoji}], ate, topo, atras, tam}`, brolls `{formato, em, ate, broll,
  ini, trans, saida, lado, pb, celeb, fila, recorte, enquadra}`, efeitos `{tipo, em, ate}`; `em/ate` = número da palavra em
  `fonte/whisper.json` (vale para todas as versões). `ops.recorte()` transforma recorte/enquadra em `crop` (px do arquivo).
- `custos.json`: `{itens: [{servico: claude|gemini|apify, etapa, rotulo, quando, usd, ...}], notas}`. Claude e Gemini: tokens
  da resposta × `custos.PRECOS` / `gemini.PRECOS`; Apify: `usageTotalUsd` da corrida. Estudo da biblioteca pela aba B-rolls
  vai para `~/B-rolls/biblioteca/custos.json`.
- Gemini (`lib/gemini.py`): Interactions API `POST v1beta/interactions`, vídeo inline em base64 (`resolution: "high"`;
  `media_resolution` dá 400 "Unknown parameter"), `response_format` com JSON Schema; texto em `steps[].content[].text` do `model_output`;
  uso em `usage.total_input_tokens / total_output_tokens / total_thought_tokens`. Vídeo > 18 MB vira cópia leve antes.
- Gemini pela OpenRouter (`gemini.py`, preferida quando há chave `openrouter`): `POST openrouter.ai/api/v1/chat/completions`,
  modelo `google/<modelo_gemini>`, vídeo `{"type":"video_url","video_url":{"url":"data:video/mp4;base64,…"}}` (cópia leve
  acima de 8 MB), `response_format` json_schema estrito + `provider.require_parameters`; se recusar o schema, repete com
  `json_object` e o schema no texto. Custo = `usage.cost` (o que a OpenRouter cobra). `testar` mostra o crédito (`GET /key`).
  Teste sem gastar: `ESTUDIO_OPENROUTER_API` apontando para um servidor falso.
- Ficha (`lib/ficha.py`): schema único (estrito para o Claude), `ajustar` (tempos, tags, centro da dividida 0,25–0,75,
  y do texto), `resumo` (a linha que o Claude lê para escolher). `ops.recorte_fino` recorta pela faixa exata do texto no
  trecho usado (no máximo ~38% da altura em cheia/canto) e centra a dividida pelo `formatos.dividida.centro`.
- Estudo pelo Claude (`lib/estudo.py`): folha 4 colunas de 12–16 quadros (1 a cada ~2 s, 300×533, segundo embaixo;
  `folhas.py` no Python do sistema) → `ia.Claude(modelo=modelo_estudo)` esforço low, schema igual ao do Gemini. ~2 mil
  tokens de imagem; Opus ~US$ 0,03 e Sonnet ~US$ 0,012 por clipe. `biblioteca.estudar_pendentes` escolhe o motor pelo ⚙
  (`modelo_estudo`); `MOTOR_FORCADO` troca para o Claude se o Gemini não responder. No Python do sistema (sem SDK) o
  `tiktok.py buscar` só baixa e avisa.
- Card recortado: a ficha traz `formatos.card` (serve + x_ini/x_fim/y_ini/y_fim + `trechos`, os intervalos em que essa
  região fica limpa). `ops.recorte_fino` avisa quando um texto entra no card no trecho usado ou quando o `ini` cai fora
  desses trechos (o aviso volta para o Claude corrigir).
  `ops.recorte_fino` devolve esse recorte quando o formato é card, e `motor.tam_card` já tira a proporção do crop — card
  quadrado ou vertical funciona (limite `card.altura_max` = 0,46 nos dois Ultradinâmicos; o editor usa a mesma conta).
  É a saída para clipe bom com texto atravessando o quadro.
- `biblioteca.py revisar-descartados` passa a regra de descarte de novo nos descartados, sem reestudar (de graça); o botão
  "Reestudar os descartados" (aba B-rolls › Descartados) refaz a ficha com o Gemini (`estudar_pendentes(refazer=True)`) e
  `gravar_estudo` devolve para disponível quem passar.
- Descarte (`biblioteca.motivo_descarte`, ao gravar a ficha): manda o que a ficha diz — serve em algum formato (inclusive
  card recortado), fica; texto grande no meio só derruba quando não há card nem trecho limpo; ficha antiga sem `formatos`
  cai na regra velha → estado `descartado` (aba B-rolls ›
  Descartados, com o motivo; "Voltar para disponíveis" desfaz). Legenda só numa faixa do topo/base fica: a edição recorta.
- Exemplo aprovado ≠ histórico: `exemplo.py` grava também `exemplo_fonte.txt` (a fala do vídeo do exemplo); se o vídeo
  novo tem mais de 30% dos trechos de 3 palavras iguais (`auto.mesmo_video_do_exemplo`), o exemplo fica de fora.
- Regras novas do Ultradinâmico Criativo (`regras.py`): `letreiro_dur_max` 4, `palavras_max` 5, `max_sem_broll` 10 (modo
  escuro não conta), `min_fora_da_cheia` 0,4. Calibradas em 2026-09-19: a edição do AM29 feita no chat passa com zero, a
  automática antiga dá 9 problemas (frases longas, 6,6 s de letreiro, 19 s sem B-roll, 6 de 9 em tela cheia).
- Teste sem gastar: pasta isolada com `ESTUDIO_RAIZ`, `ESTUDIO_BIBLIOTECA`, `ESTUDIO_CHAVES`; troque `auto.ia.Claude`,
  `auto.tiktok.buscar` e `auto.gemini.assistir_montagem` por versões falsas e chame `auto.main()` (foi assim o teste de
  2026-09-19: 8 etapas, rascunho renderizado, usados marcados).

## Chat do Estúdio (`lib/chat.py`, `app/chat.html`)

- Servidor: `POST /api/chat/novo` → id (12 hex); `POST /api/chat/enviar?id=` `{texto, anexos}` grava `entrada/<ns>.json`
  e abre o processo se não estiver vivo (`estado.json` pid); `GET /api/chat/eventos?id=&pos=` devolve eventos a partir do
  byte `pos`; `parar`, `responder {id, r}`, `modo {modo}`, `renomear`, `apagar`; `POST /api/chat/arquivo?id=&nome=`
  (anexo, corpo cru); `GET /api/chat/mencoes` (@: projetos, renders, Downloads/Mesa/Filmes); `POST /api/revelar {p}`.
- Eventos: `usuario`, `init`, `rasc_ini`/`rasc` (texto chegando), `texto`, `pensando`/`pensou`, `ferr {id,nome,entrada,pai}`,
  `res {id,txt,erro,imgs}`, `permissao`/`pergunta` → `respondido`, `negado` (modo automático bloqueou), `tarefa`,
  `aviso`, `erro`, `modo`, `fim {custo,total,dur,erro}`. `pai` = id do Agent que chamou (subagente).
- Opções do SDK: `system_prompt` preset `claude_code` + `append` (instruções do chat), `setting_sources` user/project/local,
  `skills="all"`, `include_partial_messages`, `can_use_tool` (cartões; AskUserQuestion volta com `answers`),
  env `CLAUDE_CODE_ENABLE_ASK_USER_QUESTION_TOOL=true` (sem ele o AskUserQuestion não aparece). Mensagem com anexo de
  imagem vai como bloco `image` (igual colar print).
- Custo: `ResultMessage.total_cost_usd` é o acumulado da sessão e, ao retomar, já vem com o das vezes anteriores;
  `chat.py` confere na primeira resposta de cada processo se veio (compara com o custo só da rodada) antes de somar.
- `max_buffer_size=256 MB`: o padrão do SDK (1 MB por mensagem) derruba o chat quando o Claude lê uma folha de contato
  (a imagem volta em base64 duas vezes na mesma linha) — "JSON message exceeded maximum buffer size" (2026-09-19).
- Sessão perdida (`No conversation found`): começa sessão nova e avisa na tela; o histórico visual continua.
- Teste sem gastar (2026-09-19): API falsa em Python respondendo SSE (`message_start` … `message_stop`) por roteiro,
  `ESTUDIO_CHAT_API` + `ESTUDIO_CHAVES` com chave falsa. As sessões de teste caem em `~/.claude/projects/<pasta-do-seu-projeto>/`:
  tire depois.

## Campos de estilo que o motor lê além do visual

- `legenda`: `contorno` [r,g,b,alfa,px], `segmentacao: "frases"` (frase inteira, 1–2 linhas, `alvo_palavras`,
  `max_palavras`, `max_caracteres`, `quebra_antes`, `nao_termina_em`), `minusculas`, `sem_pontuacao`, `largura_max`,
  `entrelinha`, `canto_dy`, `dividida_dy`.
- `card`: `fila_largura`, `fila_gap` (cards em fileira: plano `fila=[i, n]`), `pessoa_na_frente`, `sombra_alpha` 0 = sem sombra.
- `regras` (opcional): ritmo do modelo conferido por `lib/regras.py` no `plano` e no `conferir` (formato mínimo, respiro entre
  B-rolls, passagens diretas permitidas, letreiro fora do B-roll…). Campos no topo de `lib/regras.py`.
- Plano: `crop` [x, y, w, h] em pixels do arquivo original recorta a cena (render e editor); `y0` [valor, ini, fim] da tela
  dividida é gravado por `motor.py <versão> cabecas`.

## plano.json

`src, masks, whisper, estilo, dur, cortes[]` · `blocos[{uid, linhas[[estilo, texto, t, emoji]], fim, topo, atras, tam?}]` ·
`cenas[{uid, tipo, ini, fim, src, id, src_ini, crop?, trans, saida, lado?, pb?, escurecer?, celeb?}]` ·
`escuro[[a,b]], pb[[a,b]], glitch[[t,d]], flash[t]` · `legendas[{ini, fim, txt, x, y, oculto}]`, `legenda_pos{x,y,tam}` ·
`zoom[[t,z]]` (recalculado ao salvar) · `sfx_edicoes{chave:{f,g,dt,off}}`, `sfx_extras[{t,f,g}]` · `correcoes{palavra:grafia}`.

## Velocidade medida (MacBook, 10 núcleos)

- Transcrição: ~20 s para 10 min de fala (mlx-whisper turbo). Proposta de cortes: ~1 s + refino de pausas (~3 min, 1 whisper por pausa).
- Máscaras: Vision via JXA ~15 quadros/s por processo; 3 em paralelo.
- Render: 1 processo 13 quadros/s; paralelo (6) 29 quadros/s → vídeo de 4 min em ~5 min.

## Armadilhas

- `swiftc` desta máquina está quebrado (SDK incompatível): Vision só via JXA (`lib/pose.js`, usado para achar a cintura).
- Recorte da pessoa: `lib/matte.py` (RobustVideoMatting em ONNX, local). O modelo não vai no git —
  `python3 lib/matte.py --instalar` baixa da release oficial e confere o sha256. Roda em **CPU de propósito**:
  o CoreML só aceita 282 dos 303 nós, em 10 pedaços, e o vaivém deixa 10x mais lento (medido). O `downsample_ratio`
  é calculado para cair perto de 512 px na borda longa, como o modelo pede. Paralelo por faixas de quadros, cada
  uma com 12 quadros de aquecimento porque o estado é recorrente; se a contagem não fechar, refaz em um processo só.
  A Vision (`mascaras.js`) foi aposentada: decidia cada quadro do zero, daí 4,97% de pixels piscando contra 0,71%.
- Processo iniciado pelo app não lê `~/Downloads` (TCC) e não tem `/opt/homebrew/bin` no PATH (`comum.py` corrige o PATH).
- ffmpeg sem `drawtext`: texto sempre via PIL (motor) / canvas (prévia).
- Corte de jump cut: sempre até o FIM da palavra (+0,12–0,2 s), nunca pelo início da próxima.
- A transcrição às vezes engole uma retomada numa pausa ou numa palavra esticada — por isso o refino de pausas e a
  `verificacao.md` depois do jump cut.
- TikTok vem em HEVC: o editor usa os proxies H.264 do acervo; o render usa o original.
- Hard link (`ln`) para pôr um vídeo de Downloads na pasta de renders sem ocupar espaço.
