# Abre o Atlas Editor: sobe o servidor se ainda nao estiver rodando, e abre o
# navegador. E isto que o atalho da area de trabalho chama - pode rodar direto
# tambem, sempre que quiser (idempotente: clicar de novo com o servidor ja
# aberto so reabre o navegador).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Porta = if ($env:PORTA) { $env:PORTA } else { "4123" }
$Log = Join-Path $env:USERPROFILE ".atlas-editor.log"

function Servidor-Respondendo {
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Porta/api/health" -UseBasicParsing -TimeoutSec 2
    return $r.StatusCode -eq 200
  } catch {
    return $false
  }
}

if (Servidor-Respondendo) {
  Add-Content -Path $Log -Value "Ja estava rodando, so reabrindo o navegador."
} else {
  Add-Content -Path $Log -Value "Iniciando o Atlas Editor..."
  $VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
  Start-Process -FilePath $VenvPython -ArgumentList "app\servidor.py" -WorkingDirectory $PSScriptRoot `
    -WindowStyle Hidden -RedirectStandardOutput $Log -RedirectStandardError "$Log.err"
  for ($i = 0; $i -lt 30; $i++) {
    if (Servidor-Respondendo) { break }
    Start-Sleep -Seconds 1
  }
}

Start-Process "http://localhost:$Porta"
