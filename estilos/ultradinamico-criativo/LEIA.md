# Estilo Ultradinâmico Criativo

Origem: o Ultradinâmico (VSL de referência, refinado na VSL TSN), adaptado em 2026-09-19 a pedido do cliente para criativo
(anúncio curto). Mesmo visual, mesmos letreiros, mesmos formatos de B-roll e mesmo mapa de som, mas **sem empilhar**.

O cliente viu o que cansa:
- sair da pessoa recortada no canto, ir para a tela dividida e dali para outra coisa em sequência;
- a pessoa ficar 1 s no canto e logo aparecer de outro jeito com um monte de letra em cima.

No criativo, cada elemento tem o seu momento, e a pessoa respira entre um e outro.

## As regras (conferidas pelo `estudio.py plano` e pelo `estudio.py conferir`)

1. **Um elemento por vez.** Letreiro não fica em cima de B-roll, e o modo escuro também não. Um letreiro de cada vez,
   com até 2 linhas.
   - Exceção: lista de ícones (✅ um, ✅ dois, ✅ três, ou ❌ na negação), que pode empilhar linhas e ficar em cima de B-roll.
2. **Formato fica tempo suficiente na tela:**
   - `canto`: no mínimo 3 s;
   - `dividida`: 2 s;
   - `card`: 2 s;
   - `cheia`: 1,2 s.
3. **Respiro entre B-rolls:** depois de um B-roll, pelo menos 1,5 s só com a pessoa antes do próximo.
   - As passagens diretas permitidas são só `dividida` → `cheia` e `cheia` → `cheia` (uma lista de ações).
   - No máximo 3 cenas seguidas sem respiro.
   - Nunca canto → dividida, canto → cheia, cheia → canto, nem dividida → canto sem a pessoa no meio.
4. **O letreiro espera a pessoa voltar:** 0,6 s depois que o B-roll sai.
5. Famosos em sequência (`celeb`) continuam podendo encadear na tela dividida.
6. **Letreiro curto:** no máximo 5 palavras somando as linhas e 4 s na tela (a lista de checks e o letreiro da tela
   dividida podem mais). É a palavra-chave, não a frase: "corpo de cavala", não "toda mulher deveria saber".
7. **B-roll espalhado e variado:** nunca mais de 10 s sem B-roll de verdade (o modo escuro não conta), do começo ao fim
   do vídeo, e pelo menos 40% dos B-rolls em `canto` ou `dividida` (não só tela cheia).

## Letreiro (igual ao Ultradinâmico, com menos densidade)

- Linha branca pequena (`sans`) dá o contexto. A palavra-chave vem em serifada itálica azul com brilho (`azul`), **no
  instante em que é falada**, com punch-in de 12%.
- Número forte: `num` + `azul` embaixo ("+8 / anos", "100% / natural").
- Afirmação dramática em modo escuro: `branca` sozinha. É a única coisa na tela nesse momento.
- Checks: `branca` com ✅ em cada linha (ding em cada uma). É a exceção da regra 1.
- "Atrás da cabeça" (`atras=True`) só na marca ou produto.
- Densidade: um letreiro a cada 4–6 s de fala, só nas palavras que valem. Frase sem palavra forte fica só com a legenda.

## B-roll

- Formatos:
  - `cheia` (com transição whip, zoom, flash ou glitch);
  - `canto` (frase explicativa de 3 s ou mais);
  - `dividida` (famoso, comparação, resultado);
  - `card` (print ou corte horizontal do YouTube).
- B-roll só onde a fala tem imagem literal, a cada ~6–8 s. Entre um e outro, a pessoa com um letreiro ou só a legenda.
- Sequências boas:
  - dividida → cheia → pessoa;
  - cheia → cheia (lista) → pessoa;
  - pessoa → canto (3–5 s) → pessoa.
- Nunca o mesmo clipe duas vezes na mesma versão; se repetir o ID, use outro trecho (`ini`).

## Efeitos

- Modo escuro (holofote) em 2–3 frases fortes e no fechamento, com a pessoa e no máximo um letreiro `branca`.
- P&B com glitch no "método antigo", em `cheia`.

## Som (regras em `sfx_regras.py`, as mesmas do Ultradinâmico)

- whoosh na entrada de texto e de B-roll;
- pop na palavra-chave;
- ding no ✅;
- boom grave no modo escuro e em famosos;
- glitch no método antigo;
- sem música.

Como há menos elementos na tela, sai menos som.

## Movimento e acabamento (2026-09-20)

- **Zoom**: não é mais um degrau a cada corte. Cada trecho ganha um movimento lento (aproxima, depois afasta), e
  trecho com menos de `zoom.min_movimento` (2,2 s) segue o movimento que já vinha — sem isso o zoom fica indo e
  voltando em frações de segundo. O punch do letreiro sobe em 0,16 s (empurrão, não pulo) e só entra se o anterior
  terminou há mais de 1,2 s.
- **Transições**: entrada e saída usam a mesma lista — `corte`, `fade` (dissolve), `whip`, `slide`, `zoom`, `flash`,
  `glitch`. Saída com movimento (whip/slide) fecha melhor que corte seco; `fade` é o mais discreto.
- **Tela dividida**: a junção não tem mais risco branco — uma imagem SOME na outra numa faixa larga (`dividida.fusao`,
  100 px; por cena, `emenda_px`). `emenda: "reto"` desliga e deixa o corte seco. `inverte: true` põe a pessoa em cima
  e o B-roll embaixo (o letreiro desce junto).
- **Card**: nunca em pé. Clipe vertical entra quadrado (recorte central); o ideal é material horizontal, que é o que
  o YouTube traz.
- **Canto**: a pessoa recortada não tem mais contorno branco (borda suavizada) e entra enquadrada **da cintura para
  cima** (`canto.enquadra: "busto"`): a máscara acha o topo da cabeça e os ombros e conta ~3,6 cabeças. Ela aparece
  perto, em vez de um bonequinho de corpo inteiro — muda tudo quando o expert está sentado. O recorte é medido uma vez
  por cena (`motor.py <versão> cabecas`) e fica no plano.
