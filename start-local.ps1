param([switch]$Down, [switch]$ResetData)
$ErrorActionPreference = 'Stop'
$compose = Join-Path $PSScriptRoot 'infra/docker-compose.hejian-local.yml'

if ($Down) {
  if ($ResetData) {
    docker compose -f $compose down -v
  } else {
    docker compose -f $compose down
  }
  exit 0
}

docker compose -f $compose up --build -d
Write-Host 'Local gateway: http://127.0.0.1:8090'
Write-Host 'Health check: http://127.0.0.1:8090/api/v2/health'
Start-Sleep -Seconds 10
Write-Host 'Startup wait finished. Run the health check above to verify the API.'

$lanIps = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
  Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and $_.PrefixOrigin -ne 'WellKnown' } |
  Select-Object -ExpandProperty IPAddress
foreach ($ip in $lanIps) {
  Write-Host ("Device debug gateway: http://{0}:8090" -f $ip)
}
