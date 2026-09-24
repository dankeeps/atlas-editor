---
name: criar-template
description: Estuda um anúncio/VSL de referência e cria um template novo (estilos/<nome>/) para o motor de edição do Atlas — mede ritmo de corte, legenda e som por ffmpeg, olha os quadros do vídeo, escreve estilo.json/LEIA.md/BUSCA.md e grava com scripts/criar_template.py (mesma validação de sempre, nunca sobrescreve nada). Use quando o usuário mandar um anúncio de referência e pedir para estudar ele, criar um template novo, aprender esse estilo de edição, ou replicar essa edição num modelo novo do Atlas — inclusive "melhora o template X" (vira uma versão nova, nunca reescreve a antiga). O rascunho nasce despublicado e some do Kanban/Novo Vídeo; só publique (scripts/criar_template.py publicar) quando o usuário disser explicitamente. Não use isto para editar um vídeo específico de um cliente (esse é o fluxo normal de "edita esse criativo no modelo X", sem esta skill) nem para ajustar um template só reescrevendo o JSON à mão fora do estilos/<nome>/ existente.
---

# Criar template (Atlas Editor)

Substitui o antigo chat Codex: agora quem estuda o vídeo de referência e escreve o template é você mesmo,
aqui, local — sem sandbox de agente nenhum, com acesso normal a terminal e arquivo. A única regra que não
muda: você nunca escreve direto em `estilos/` ou `fontes/` fora do `scripts/criar_template.py` — ele é quem
valida e grava, a mesma trava de segurança de sempre (`lib/template_escrita.py`).

## Antes de começar

- Rode tudo com o Python da `.venv` do repositório (`source .venv/bin/activate` ou `.venv/bin/python3`).
- Você precisa do vídeo de referência (caminho local ou anexado na conversa).
- Nunca edita nenhum vídeo do usuário aqui — só observa o de referência para aprender o estilo.
- Nasce sempre rascunho (`publicado: false`). Você não publica sozinho, mesmo se achar que ficou bom — só
  quando o usuário disser "publica", depois de revisar (ver Passo 6).

## Passo 1 — medir o vídeo

```
.venv/bin/python3 scripts/criar_template.py estudar <video> --quadros /tmp/quadros-<nome>
```

Devolve um JSON com:
- `ritmo`: blocos de corte, trocas/min, duração mediana/mínima/máxima de cada bloco. Use a **mediana** para
  decidir o ritmo do template — nunca "o que pareceu no olho" (um relato de IA sobre ritmo já errou feio
  aqui antes: veja o aviso no topo de `lib/estudo_video.py`).
- `legenda`: posição medida por cor de destaque tipo karaokê (se achar) — centro em % da altura da tela, se
  fica fixa ou muda de lugar, tamanho da letra.
- `som`: ataques graves/agudos e quanto casam com os cortes, se tem ambiência/trilha sob a fala, batida.
- `quadros`: caminhos de imagem, um do MEIO de cada bloco de corte, em ordem cronológica.

**Leia cada quadro com a ferramenta de imagem antes de decidir qualquer coisa.** O JSON dá o "quanto" (ritmo,
posição, som); os quadros dão o "como": onde entra o letreiro, que tipo de B-roll aparece, se tem modo
escuro, que formato de cena é usado.

## Passo 2 — o que o motor de edição sabe fazer (e o que não sabe)

### Formatos de cena (usa.formatos)
- `"cheia"`: o B-roll cobre a tela inteira.
- `"canto"`: a pessoa recortada na frente do B-roll, num dos lados.
- `"dividida"`: B-roll em cima, a pessoa embaixo (sempre nessa ordem).
- `"card"`: retângulo arredondado no topo, a pessoa passa na frente.
Sem cena ativa, aparece só a pessoa — é o "respiro"; em vários estilos é metade do vídeo.

### Texto
- `letreiro`: blocos com 1+ linhas, cada uma com um estilo (`letreiro.estilos.<chave>`: `familia`, `px`,
  `cor` `[r,g,b]`, `tracking`, `efeito`).
- `efeito` de um letreiro **tem** que ser uma chave que existe em `letreiro.efeitos` (lista de camadas
  `[r,g,b,alfa,blur,dy]`) — inclusive `"nenhum"`: se usar esse valor, `letreiro.efeitos.nenhum` precisa
  existir (pode ser uma lista vazia `[]`). Não é palavra mágica, é só mais uma chave.
- `legenda`: automática da transcrição — `legenda.familia/tam/pad/x/y/max_palavras/max_caracteres/sombras`
  são sempre obrigatórios; `legenda.canto_x` + `legenda.canto_dy` só se `"canto"` estiver em
  `usa.formatos`; `legenda.dividida_dy` só se `"dividida"` estiver em `usa.formatos`.

### Efeitos
`escuro` (holofote), `pb` (preto e branco), `glitch`, `flash`, `zoom` (com pontos de "punch" na
palavra-chave), transições de entrada/saída: `whip`, `slide`, `zoom`, `flash`, `glitch`, `fade`, `corte`.

### O que NÃO existe — não prometa isso num template
- Gráficos animados (barra de progresso, medidor, círculo de porcentagem, seta com número).
- Dois B-rolls empilhados sem a pessoa aparecer (a `"dividida"` sempre põe a pessoa numa das metades).
- Trilha de música (a mixagem tem só voz e efeito sonoro).
- Imagem gerada por IA dentro do render.
- Recorte de algo que não seja a pessoa.
- Caixa/retângulo sólido atrás do texto (tipo marca-texto ou "highlight" de fundo): cada camada de `efeito`
  desenha uma CÓPIA do próprio texto (sombra, brilho, contorno) atrás da cor principal — nunca um fundo
  preenchido do tamanho do bloco. Para uma cor de destaque forte, use `cor` na linha ou um efeito com pouco
  blur e alfa alto; não descreva isso como "caixa" ou "fundo colorido" no LEIA.md.

### Efeito sonoro
Você nunca escreve `sfx_regras.py` — não é um dos arquivos aceitos (`scripts/criar_template.py` recusa
qualquer `.py`). Todo template novo usa o som padrão automático (`lib/sfx_padrao.py`) até um humano escrever
uma regra customizada à mão depois.

### Fontes
`ls fontes/*.ttf fontes/*.otf` mostra o que já existe. Em `fontes.<chave>.arquivo` o valor **tem** que ser
`"fontes/<nome-exato-do-arquivo>"`, com esse prefixo e a extensão — nunca só o nome da família (ex.: nunca
`"Heavy"` nem `"AvenirNext-Heavy"`; sempre `"fontes/AvenirNext-Heavy.ttf"`). É `lib/motor.py` quem resolve
esse caminho a partir da raiz do repositório, e a validação (`lib/template_escrita.py`) confere exatamente
essa forma — um nome sem o prefixo passa em testes soltos mas quebra no render de verdade.

Fonte nova: baixe/copie o arquivo e passe `--fonte <caminho>` para `scripts/criar_template.py criar` (copia
para `fontes/` sem sobrescrever nada com o mesmo nome) — nunca invente um arquivo que ainda não existe lá.

## Passo 3 — um exemplo real para adaptar

Leia um `estilo.json` já publicado como referência de estrutura — `estilos/ultradinamico-criativo/estilo.json`
é o mais completo. Adapte o que fizer sentido pro vídeo novo; **nunca copie o texto de LEIA.md/BUSCA.md de
outro template sem reescrever para este** — ele descreve OUTRO estilo, e copiar sem adaptar faz a edição
automática seguir a regra errada.

## Passo 4 — nome do template

Letras minúsculas, números e hífen (ex.: `vsl-gancho-forte`). Se o pedido for "melhora esse template" (uma
versão nova de um que já existe), pegue o próximo nome livre — nunca escreva por cima do que já existe:

```
.venv/bin/python3 scripts/criar_template.py proximo-nome <nome-base>
```

## Passo 5 — escrever e validar

Monte localmente (pode ser em `/tmp`, não precisa estar dentro de `estilos/`):
- `estilo.json` — a estrutura toda (formatos, letreiro, legenda, fontes, efeitos).
- `LEIA.md` — o que ESTE template é, escrito para o vídeo estudado.
- `BUSCA.md` — que tipo de B-roll buscar para ele.

Depois:

```
.venv/bin/python3 scripts/criar_template.py criar <nome> \
  --estilo /tmp/estilo.json --leia /tmp/LEIA.md --busca /tmp/BUSCA.md \
  [--fonte /tmp/FonteNova.ttf ...]
```

Se falhar, a mensagem diz exatamente o campo problemático (mesma validação de `lib/template_escrita.py`) —
ajuste o arquivo local e rode de novo. **Nunca "chute" um valor só para passar na validação** sem entender
o motivo do erro; se sobrar dúvida real sobre o que o motor suporta, registre a ressalva em LEIA.md para um
humano revisar, em vez de inventar.

## Passo 6 — revisar

Avise o usuário: *"criei o rascunho '<nome>'; dá uma olhada no estilo.json (ou experimenta de verdade
escolhendo ele em '+ Novo vídeo', que já lista rascunhos) antes de eu publicar."* Não existe mais um passo
de teste automático (o Laboratório saía do ar — exigia vídeo e B-rolls preparados à parte, fora de qualquer
fluxo normal, e não valia o atrito); quem julga se o template está pronto é o usuário, olhando o resultado
real ou o próprio JSON.

## Passo 7 — publicar (só quando o usuário pedir, explicitamente)

```
.venv/bin/python3 scripts/criar_template.py publicar <nome>
```

Falha só se o template não existir ou já estiver publicado (`publicar_template` em `app/servidor.py`) — sem
trava de teste prévio.

## Depois de publicar

O template já está em `estilos/<nome>/`, dentro deste repositório git. Commit e deploy para a VPS seguem as
regras normais do projeto (ver `AGENTS.md`): nada vai para produção sem um pedido explícito novo de
publicação do usuário.
