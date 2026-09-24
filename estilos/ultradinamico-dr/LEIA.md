# Estilo Ultradinâmico DR

Origem: o Ultradinâmico Criativo, adaptado em 2026-09-23 para Direct Response — **sem letreiro nenhum e sem modo
escuro**. Mesmos formatos de B-roll, mesmo respiro entre um elemento e outro, mesmo mapa de som. Quem carrega o texto
na tela é só a legenda; nada compete com a mensagem falada.

## As regras (conferidas pelo `estudio.py plano` e pelo `estudio.py conferir`)

1. **Sem letreiro.** Nenhum bloco de texto na tela — a legenda sozinha carrega a fala. Sem exceção, inclusive lista de
   ícones (✅/❌): se a fala pede uma lista, ela fica só na legenda.
2. **Sem modo escuro.** Nenhuma frase recebe o holofote/vinheta escura. O fundo nunca escurece.
3. **Formato fica tempo suficiente na tela:**
   - `canto`: no mínimo 3 s;
   - `dividida`: 2 s;
   - `card`: 2 s;
   - `cheia`: 1,2 s.
4. **Respiro entre B-rolls:** depois de um B-roll, pelo menos 1,5 s só com a pessoa antes do próximo.
   - As passagens diretas permitidas são só `dividida` → `cheia` e `cheia` → `cheia` (uma lista de ações).
   - No máximo 3 cenas seguidas sem respiro.
   - Nunca canto → dividida, canto → cheia, cheia → canto, nem dividida → canto sem a pessoa no meio.
5. Famosos em sequência (`celeb`) continuam podendo encadear na tela dividida.
6. **B-roll espalhado e variado:** nunca mais de 10 s sem B-roll de verdade, do começo ao fim do vídeo, e pelo menos
   40% dos B-rolls em `canto` ou `dividida` (não só tela cheia).

## B-roll

- Formatos:
  - `cheia` (com transição whip, zoom, flash ou glitch);
  - `canto` (frase explicativa de 3 s ou mais);
  - `dividida` (famoso, comparação, resultado);
  - `card` (print ou corte horizontal do YouTube).
- B-roll só onde a fala tem imagem literal, a cada ~6–8 s. Entre um e outro, a pessoa só com a legenda.
- Sequências boas:
  - dividida → cheia → pessoa;
  - cheia → cheia (lista) → pessoa;
  - pessoa → canto (3–5 s) → pessoa.
- Nunca o mesmo clipe duas vezes na mesma versão; se repetir o ID, use outro trecho (`ini`).

## Efeitos

- P&B com glitch no "método antigo", em `cheia`. Sem modo escuro em nenhum momento, inclusive no fechamento.

## Som (regras em `sfx_regras.py`, as mesmas do Ultradinâmico)

- whoosh na entrada de B-roll;
- glitch no método antigo;
- sem música.

Sem letreiro nem modo escuro, o mapa de som fica mais enxuto: quase todo o áudio extra vem das entradas de B-roll.

## Movimento e acabamento (herdado do Ultradinâmico Criativo, 2026-09-20)

- **Zoom**: não é um degrau a cada corte. Cada trecho ganha um movimento lento (aproxima, depois afasta), e trecho
  com menos de `zoom.min_movimento` (2,2 s) segue o movimento que já vinha — sem isso o zoom fica indo e voltando em
  frações de segundo.
- **Transições**: entrada e saída usam a mesma lista — `corte`, `fade` (dissolve), `whip`, `slide`, `zoom`, `flash`,
  `glitch`. Saída com movimento (whip/slide) fecha melhor que corte seco; `fade` é o mais discreto.
- **Tela dividida**: a junção não tem risco branco — uma imagem SOME na outra numa faixa larga (`dividida.fusao`,
  100 px; por cena, `emenda_px`). `emenda: "reto"` desliga e deixa o corte seco. `inverte: true` põe a pessoa em cima
  e o B-roll embaixo.
- **Card**: nunca em pé. Clipe vertical entra quadrado (recorte central); o ideal é material horizontal, que é o que
  o YouTube traz.
- **Canto**: a pessoa recortada não tem contorno branco (borda suavizada) e entra enquadrada **da cintura para cima**
  (`canto.enquadra: "busto"`): a máscara acha o topo da cabeça e os ombros e conta ~3,6 cabeças. O recorte é medido
  uma vez por cena (`motor.py <versão> cabecas`) e fica no plano.
