# Atlas Editor

Editor de anúncios e VSLs verticais com IA: transcrição, revisão de cortes, B-rolls,
letreiros, efeitos sonoros, prévia, render, Kanban e edição por chat.

Você usa sua própria chave da API da Anthropic (Claude) — o custo de cada edição é
seu, direto com a Anthropic, sem intermediário.

## Instalar no seu computador

Um comando só. Ele confere o que falta, baixa o projeto, monta o ambiente Python,
baixa os modelos de IA, cria um ícone/atalho na área de trabalho e abre o editor
no navegador.

**Mac ou Linux** — cole no Terminal:

```sh
curl -fsSL https://raw.githubusercontent.com/dankeeps/atlas-editor/main/install.sh | bash
```

Requisitos: Python 3.12 e FFmpeg (o instalador tenta instalar sozinho via Homebrew
no Mac; no Linux, `sudo apt-get install -y python3.12 python3.12-venv ffmpeg`).

**Windows** — cole no PowerShell (não precisa WSL nem nada extra, o PowerShell já
vem com o Windows):

```powershell
irm https://raw.githubusercontent.com/dankeeps/atlas-editor/main/install.ps1 | iex
```

Requisitos: Git, Python 3.12 e FFmpeg (o instalador tenta instalar sozinho via
`winget`, o instalador de pacotes do próprio Windows — se a máquina não tiver o
`winget`, ele avisa e pede pra instalar pela Microsoft Store).

Nos dois casos, isso já cria um ícone **"Atlas Editor"** na área de trabalho — da
próxima vez é só dar dois cliques nele, não precisa abrir o terminal de novo. (Se
quiser atualizar para uma versão mais nova, aí sim rode o comando acima outra vez.)

Nada do que você edita se perde entre uma abertura e outra: projetos e chaves ficam
fora do repositório (no Windows, em `%USERPROFILE%\Edições` etc.; no Mac/Linux, em
`~/Edições`, `~/B-rolls` e `~/.config/estudio-edicao/`).

## Rodar na sua própria VPS

Se preferir acessar de qualquer lugar em vez de só localmente, dá para rodar num
servidor próprio com Docker — veja [deploy/README.md](deploy/README.md).

## O que tem aqui

- `app/`: servidor e telas (editor, cortes, chat, Kanban).
- `lib/`: transcrição, recorte, remoção de fundo, pose, motor de render, integrações.
- `estilos/`, `prompts/`, `sfx/`, `fontes/`: templates de letreiro e recursos de edição.
- `tests/`: suíte de testes (`*.venv/bin/python -m unittest discover -s tests`).
- `deploy/`: Docker e Compose para auto-hospedagem.
- `.claude/skills/criar-template/`: skill do Claude Code para criar templates de
  letreiro novos — baixável também de dentro do próprio editor, na aba Templates.

## Testes

```sh
.venv/bin/python -m unittest discover -s tests
```

Os testes usam dados sintéticos e não chamam nenhuma API paga.
