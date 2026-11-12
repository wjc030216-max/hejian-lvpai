# Run after start-local-real.ps1 -Offline. Keep this terminal open.
param(
    [string]$ProjectName = 'hejian-local-real'
)
$ErrorActionPreference = 'Stop'
$Network = $ProjectName + '_default'
$ContainerName = $ProjectName + '-media-tunnel'
& docker network inspect $Network --format '{{.Name}}'
if ($LASTEXITCODE -ne 0) { throw "Start the local stack first; Docker network $Network was not found." }
Write-Host 'Copy the HTTPS trycloudflare.com origin below to LOCAL_PUBLIC_BASE_URL in .env.local.real.'
Write-Host 'Only the media gateway is exposed. Closing this terminal disconnects the tunnel.'
& docker run --rm --name $ContainerName --network $Network cloudflare/cloudflared:latest tunnel --no-autoupdate --protocol http2 --url http://gateway:8081
if ($LASTEXITCODE -ne 0) { throw 'Local media tunnel stopped or failed. Check the Docker output above.' }
