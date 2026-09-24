# Atlas Editor - instalacao local no Windows.
#
#   irm https://raw.githubusercontent.com/dankeeps/atlas-editor/main/install.ps1 | iex
#
# Baixa o projeto, monta o ambiente Python, baixa os modelos de IA, cria um
# atalho na area de trabalho e abre o editor no navegador. Pode rodar de novo
# a qualquer momento para atualizar; nada se perde (projetos e chaves ficam
# fora do repositorio). Depois da primeira vez, o atalho ja basta - nao
# precisa abrir o PowerShell de novo.
#
# Sem acento nas mensagens de proposito: o console do Windows PowerShell as
# vezes exibe UTF-8 errado dependendo da configuracao regional da maquina.

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$RepoUrl = "https://github.com/dankeeps/atlas-editor.git"
$Dest = if ($env:ATLAS_DEST) { $env:ATLAS_DEST } else { Join-Path $env:USERPROFILE "Atlas Editor" }
$env:PORTA = if ($env:PORTA) { $env:PORTA } else { "4123" }

Write-Host "== Atlas Editor - instalacao local (Windows) =="
Write-Host ""

function Atualizar-Path {
  $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
  $user = [Environment]::GetEnvironmentVariable("Path", "User")
  $env:Path = "$machine;$user"
}

function Achar-Python312 {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    $exe = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
  }
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) {
    $ver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($ver -eq "3.12") { return $cmd.Source }
  }
  return $null
}

# 1. dependencias: git, python 3.12, ffmpeg
$falta = @()
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { $falta += "Git.Git" }
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { $falta += "Gyan.FFmpeg" }
$PythonExe = Achar-Python312
if (-not $PythonExe) { $falta += "Python.Python.3.12" }

if ($falta.Count -gt 0) {
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    Write-Host "Faltam estas dependencias, instalando com winget: $($falta -join ', ')..."
    foreach ($pacote in $falta) {
      winget install --id $pacote -e --silent --accept-package-agreements --accept-source-agreements
    }
    Atualizar-Path
    if (-not $PythonExe) { $PythonExe = Achar-Python312 }
  } else {
    Write-Host ""
    Write-Host "Faltam estas dependencias e nao encontrei o winget (Instalador de Apps) para instalar sozinho:"
    Write-Host "  $($falta -join ', ')"
    Write-Host "Instale o 'Instalador de Aplicativo' pela Microsoft Store (ele traz o winget) e rode este"
    Write-Host "comando de novo, ou instale cada um manualmente pelo site oficial."
    exit 1
  }
}

if (-not $PythonExe) {
  Write-Host ""
  Write-Host "O Python 3.12 acabou de ser instalado, mas esta janela do PowerShell ainda nao esta enxergando"
  Write-Host "ele. Feche esta janela, abra o PowerShell de novo e rode o comando de instalacao mais uma vez."
  exit 1
}

Write-Host "Usando Python: $(& $PythonExe --version)"
Write-Host ""

# 2. clonar ou atualizar
if (Test-Path (Join-Path $Dest ".git")) {
  Write-Host "Ja existe uma instalacao em `"$Dest`" - atualizando..."
  git -C $Dest pull --ff-only
} else {
  Write-Host "Baixando o Atlas Editor em `"$Dest`"..."
  git clone $RepoUrl $Dest
}
Set-Location $Dest

# 3. ambiente Python
Write-Host ""
Write-Host "Preparando o ambiente Python (so na primeira vez, demora alguns minutos)..."
$VenvPython = Join-Path $Dest ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
  & $PythonExe -m venv .venv
}
& $VenvPython -m pip install --upgrade pip -q
& $VenvPython -m pip install -r requirements.txt -q

Write-Host "Baixando os modelos de IA (remocao de fundo e pose, ~120 MB, so na primeira vez)..."
& $VenvPython lib\matte.py --instalar mobilenetv3
& $VenvPython lib\pose.py --instalar

# 4. atalho na area de trabalho
Write-Host ""
Write-Host "Criando um atalho na area de trabalho para abrir o Atlas Editor depois, sem precisar do PowerShell..."
try {
  $desktop = [Environment]::GetFolderPath("Desktop")
  $shell = New-Object -ComObject WScript.Shell
  $shortcut = $shell.CreateShortcut((Join-Path $desktop "Atlas Editor.lnk"))
  $shortcut.TargetPath = "powershell.exe"
  $shortcut.Arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Dest\iniciar.ps1`""
  $shortcut.WorkingDirectory = $Dest
  $shortcut.IconLocation = "$VenvPython,0"
  $shortcut.Save()
  Write-Host "Pronto: `"Atlas Editor`" na sua area de trabalho. De dois cliques nele da proxima vez."
} catch {
  Write-Host "Nao consegui criar o atalho automaticamente - sem problema, e so rodar este mesmo comando de novo."
}

Write-Host ""
Write-Host "Tudo pronto. Abrindo o Atlas Editor em http://localhost:$($env:PORTA) ..."
Write-Host "(Configure sua propria chave da Claude API em Configuracoes assim que abrir.)"
Write-Host "Da proxima vez, e so clicar no atalho - nao precisa rodar este comando de novo."
Write-Host ""

& (Join-Path $Dest "iniciar.ps1")
