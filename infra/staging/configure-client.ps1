param(
  [ValidateSet('local','staging','production')][string]$Environment = 'local',
  [string]$Domain,
  [string]$AppId
)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
if ($Environment -in @('staging','production')) {
    if (-not $Domain) { throw 'Provide the new Hejian API domain, without https:// or paths.' }
    if ($Domain -notmatch '^[a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$') { throw 'Provide only the API domain, without https:// or paths.' }
    if ($Domain -match '(?i)guizhilv|juxiaobai') { throw 'Refusing a legacy Guizhilv domain for the Hejian project.' }
    if ($AppId -notmatch '^wx[a-zA-Z0-9]{16}$' -or $AppId -eq 'wxaa62b76ca42a9db5') {
        throw 'Provide the new Hejian mini program AppID.'
    }
    $target = @{ environment=$Environment; apiBaseUrl=('https://' + $Domain) }
} else {
    $target = @{ environment='local'; apiBaseUrl='http://127.0.0.1:8092' }
    $AppId = 'touristappid'
}
$utf8 = New-Object Text.UTF8Encoding($false)
$targetPath = Join-Path $repoRoot 'client/config/target.js'
[IO.File]::WriteAllText($targetPath, ('module.exports = ' + ($target | ConvertTo-Json -Compress) + "`n"), $utf8)
foreach ($relative in @('project.config.json','client/project.config.json')) {
    $path = Join-Path $repoRoot $relative
    $config = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    $config.appid = $AppId
    $config.projectname = if ($Environment -eq 'production') { 'hejian-lvpai' } elseif ($Environment -eq 'staging') { 'hejian-lvpai-test' } else { '贺见旅拍' }
    [IO.File]::WriteAllText($path, ($config | ConvertTo-Json -Depth 30), $utf8)
}
Write-Host ('Client configured for ' + $Environment + '.')
