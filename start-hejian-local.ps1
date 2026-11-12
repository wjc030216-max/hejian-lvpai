param([switch]$Down)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$compose = Join-Path $root 'infra/docker-compose.hejian-local.yml'
$composeArgs = @('--project-name', 'hejian-local', '--file', $compose)

if ($Down) {
    & docker compose @composeArgs down
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose down failed: $LASTEXITCODE" }
    exit 0
}

& docker compose @composeArgs up --build -d --wait --wait-timeout 180
if ($LASTEXITCODE -ne 0) { throw "Docker Compose startup failed: $LASTEXITCODE" }

& docker compose @composeArgs build template-import
if ($LASTEXITCODE -ne 0) { throw "Template importer build failed: $LASTEXITCODE" }
& docker compose @composeArgs run --rm --no-deps template-import
if ($LASTEXITCODE -ne 0) { throw "Template import failed: $LASTEXITCODE" }

$health = $null
for ($attempt = 1; $attempt -le 15; $attempt++) {
    try {
        $health = Invoke-RestMethod 'http://127.0.0.1:8090/api/v2/health' -TimeoutSec 5
        break
    } catch {
        if ($attempt -eq 15) { throw 'Hejian local API did not become ready.' }
        Start-Sleep -Seconds 2
    }
}

$catalog = Invoke-RestMethod 'http://127.0.0.1:8090/api/v2/templates?limit=30' -TimeoutSec 10
$template = $catalog.data.items | Where-Object { $_.video_name -eq '建崇的幸福时光' } | Select-Object -First 1
if (-not $template) { throw 'The 30-second test template was not imported.' }
$runtime = Invoke-RestMethod ("http://127.0.0.1:8090/api/v2/templates/{0}/runtime" -f $template.template_code) -TimeoutSec 10
if ($runtime.data.segment_count -ne 2 -or $runtime.data.portrait_group -ne 'B') {
    throw 'The imported template runtime is not the expected A/B dual-segment contract.'
}
$assetUrls = @($template.cover, $template.example, $runtime.data.bgm_url)
foreach ($group in $runtime.data.segments) {
    $assetUrls += $group.storyboard_url
    $assetUrls += $group.actor_url
    $assetUrls += $group.costume_url
}
$assetUrls += $runtime.data.screen_url
foreach ($url in $assetUrls) {
    $response = Invoke-WebRequest -Uri $url -Method Head -TimeoutSec 10
    if ($response.StatusCode -ne 200) { throw "Template asset is unavailable: $url" }
}

Write-Host '贺见旅拍本地服务已启动'
Write-Host 'API: http://127.0.0.1:8090'
Write-Host 'Health: http://127.0.0.1:8090/api/v2/health'
Write-Host ("Template: {0} / {1}" -f $template.video_name, $template.video_specification)
Write-Host ("Segments: {0} / outfit groups: {1}" -f $runtime.data.segment_count,
    ($runtime.data.segments.code -join ','))
Write-Host 'WeChat DevTools project root: G:\贺见旅拍'
