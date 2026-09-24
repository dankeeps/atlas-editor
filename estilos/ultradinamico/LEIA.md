# Estilo Ultradinâmico

Origem: VSL 01 [PARTE 17] de referência (Treino Trinca), refinado na VSL do Daniel Gutierrez (TSN) em 2026-09-18/19.
Objetivo: nada fica parado mais que 2–3 s; cada ideia forte ganha uma palavra na tela; B-roll só onde a fala
tem imagem literal; som em toda entrada, cada um com a sua função.

## Letreiro (a assinatura)

- Linha branca pequena (`sans`) dá o contexto; a palavra-chave vem em serifada itálica azul com brilho (`azul`)
  **no instante em que é falada**, com punch-in automático de 12% (zoom).
- Número forte: `num` (branco gigante) + `azul` embaixo ("+8 / anos", "100% / natural", "+50 mil / alunos").
- Lista: `lista` (azul menor) uma linha por item, cada uma na sua palavra (Bruce Lee / Jackie Chan / Brad Pitt).
- Afirmação dramática em modo escuro: `branca` (serifada branca) sozinha ("perigoso", "metodologia", "tirou de você").
- Checks: `branca` com emoji ✅ por linha (ding em cada uma). Negação: `sans` com ❌.
- Emoji explicativo antes da linha `sans`: 📋 personalizado (caneta), ⏰ tempo (tique-taque), 😂 cômico (risada),
  📱, 💉, 🏛️, 🪨, 🛡️, 👀, ⚡.
- "atrás da cabeça" (`atras=True`, topo ~0,15) na marca/produto ("treino Shape Natural") e em 1–2 números.
- Letreiro dentro de tela dividida: `topo=0.05` (em cima do B-roll).
- Densidade: ~1 letreiro a cada 3–4 s de fala. Blocos duram até a próxima ideia (ate="palavra seguinte").

## B-roll

- Formatos, alternando: `cheia` (cobre a pessoa; entrada whip/zoom/flash/glitch), `canto` (pessoa recortada no
  canto com o B-roll atrás; bom para frases longas de 3–6 s), `dividida` (B-roll em cima e rosto embaixo; bom para
  pessoa citada, antes/depois, comparação), `card` (print/imagem ou corte horizontal do YouTube no topo, pessoa na frente).
- Famosos em sequência: `dividida` com `trans="corte"` entre eles e `celeb=True` (boom).
- "Método antigo / inimigo comum": `cheia` P&B com entrada `glitch`.
- Nunca o mesmo clipe duas vezes na mesma versão; se repetir o ID, use outro trecho (`ini`).
- Intervalo: nunca mais de ~8 s sem B-roll ou efeito; nunca B-roll em cima de outro.

## Efeitos

- Modo escuro (holofote) nas 3–5 frases mais fortes e no fechamento (junto com `branca` ou com a marca).
- P&B com glitch para "planilha genérica", "método velho", "fisiculturistas".
- Flash no nome da marca.

## Som (regras em `sfx_regras.py`)

whoosh na entrada de texto e de B-roll · pop na palavra-chave · ding no ✅ · zap na marca · power-up em
testosterona/ganho · boom grave em famosos, modo escuro e revelação · glitch de VHS no método antigo ·
caneta no 📋 · tique-taque no ⏰ · bipes em "metodologia / 3 fatores / estímulo" · risada no 😂 · sem música.

## Movimento e acabamento (2026-09-20)

Vale igual ao Ultradinâmico Criativo: zoom com movimento lento por trecho (sem degrau a cada corte, punch com
subida de 0,16 s e intervalo mínimo de 1,2 s), entrada e saída com a mesma lista de transições (inclui `fade`),
tela dividida sem risco branco e com `inverte` para pôr a pessoa em cima, card sempre deitado (ou quadrado) e
pessoa no canto sem contorno branco.
