# Encerra o Atlas Editor que o iniciar.ps1 deixou rodando em segundo plano.
$ErrorActionPreference = "Stop"
$Porta = if ($env:PORTA) { $env:PORTA } else { "4123" }

$conexoes = Get-NetTCPConnection -LocalPort $Porta -State Listen -ErrorAction SilentlyContinue
if (-not $conexoes) {
  Write-Host "Atlas Editor nao esta rodando."
} else {
  $conexoes | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
    Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
  }
  Write-Host "Atlas Editor encerrado."
}
