# Windows PowerShell 5.1 compatible; ASCII source avoids console encoding issues.
param(
    [switch]$Init,
    [switch]$Down,
    [switch]$CheckOnly,
    [switch]$Offline,
    [switch]$NoBuild,
    [string]$ProjectName = 'aitravel-local-real',
    [int]$GatewayPort = 8080,
    [int]$MediaPort = 8081,
    [int]$MysqlPort = 3307,
    [int]$RedisPort = 6380
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$compose = Join-Path $root 'infra/docker-compose.local-real.yml'
$configFile = Join-Path $root '.env.local.real'
$example = Join-Path $root '.env.local.real.example'
function New-LocalSecret {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}
if ($Init) {
    if (-not (Test-Path -LiteralPath $configFile)) { Copy-Item -LiteralPath $example -Destination $configFile }
    $content = [IO.File]::ReadAllText($configFile)
    foreach ($key in @('AITRAVEL_JWT_SECRET', 'LOCAL_MEDIA_TOKEN')) {
        if ($content -match ('(?m)^' + $key + '=[ \t]*\r?$')) {
            $content = [regex]::Replace($content, '(?m)^' + $key + '=[ \t]*\r?$', $key + '=' + (New-LocalSecret))
        }
    }
    [IO.File]::WriteAllText($configFile, $content, (New-Object Text.UTF8Encoding($false)))
    Write-Host 'Local configuration initialized. Existing credentials were preserved.'
    Write-Host "Edit: $configFile"
    exit 0
}
if (-not (Test-Path -LiteralPath $configFile)) { throw 'Run .\start-local-real.ps1 -Init first.' }
$settings = @{}
$allowed = @('PHASE2_PAYMENT_MODE','PHASE2_AUTH_MODE','WECHAT_APP_ID','WECHAT_APP_SECRET','LOCAL_PUBLIC_BASE_URL','LOCAL_MEDIA_TOKEN','AITRAVEL_JWT_SECRET','LOCAL_CLOUD_RESOURCES',
    'ALIBABA_CLOUD_ACCESS_KEY_ID','ALIBABA_CLOUD_ACCESS_KEY_SECRET','ALIBABA_CLOUD_SECURITY_TOKEN',
    'SEEDREAM_API_KEY','SEEDANCE_API_KEY','SEEDANCE_ASSET_ACCESS_KEY_ID','SEEDANCE_ASSET_ACCESS_KEY_SECRET','SEEDANCE_ASSET_GROUP_ID','SEEDANCE_ASSET_PROJECT_NAME','TOS_ACCESS_KEY_ID','TOS_ACCESS_KEY_SECRET','TOS_SECURITY_TOKEN','TOS_BUCKET','TOS_REGION','TOS_ENDPOINT','TOS_KEY_PREFIX')
foreach ($line in [IO.File]::ReadAllLines($configFile)) {
    if (-not $line.Trim() -or $line.Trim().StartsWith('#')) { continue }
    if ($line -notmatch '^([A-Z0-9_]+)=(.*)$') { throw 'Invalid local config line. Use KEY=value without shell syntax.' }
    $key = $Matches[1]; $value = $Matches[2].Trim()
    if ($allowed -notcontains $key) { throw "Unsupported local setting: $key. Only dedicated cloud credentials and local media settings are accepted." }
    if ($settings.ContainsKey($key)) { throw "Duplicate setting: $key" }
    if ($value -match '[\x00-\x1f$"''`]' ) { throw "Unsupported characters in setting: $key" }
    $settings[$key] = $value
}
foreach ($key in @('LOCAL_MEDIA_TOKEN','AITRAVEL_JWT_SECRET')) {
    if ($settings[$key] -notmatch '^[a-f0-9]{64}$') { throw "Run -Init to initialize $key (64 hex characters)." }
}
if (-not $Offline -and -not $Down) {
    foreach ($key in @('ALIBABA_CLOUD_ACCESS_KEY_ID','ALIBABA_CLOUD_ACCESS_KEY_SECRET','SEEDREAM_API_KEY','SEEDANCE_API_KEY','SEEDANCE_ASSET_ACCESS_KEY_ID','SEEDANCE_ASSET_ACCESS_KEY_SECRET','SEEDANCE_ASSET_GROUP_ID','TOS_ACCESS_KEY_ID','TOS_ACCESS_KEY_SECRET','TOS_BUCKET','TOS_REGION','TOS_ENDPOINT','TOS_KEY_PREFIX')) {
        if (-not $settings[$key] -or $settings[$key] -match '(?i)dummy|change.this|placeholder|local-only|your[-_]') { throw "Missing real local credential: $key" }
    }
    if ($settings['SEEDREAM_API_KEY'] -ceq $settings['SEEDANCE_API_KEY']) { throw 'Image and video generation must use different API Keys: SEEDREAM_API_KEY / SEEDANCE_API_KEY.' }
    if ($settings['SEEDANCE_ASSET_ACCESS_KEY_ID'] -ceq $settings['TOS_ACCESS_KEY_ID'] -or $settings['SEEDANCE_ASSET_ACCESS_KEY_SECRET'] -ceq $settings['TOS_ACCESS_KEY_SECRET']) { throw 'Asset management and TOS must use separate IAM identities and keys.' }
    if ($settings['TOS_REGION'] -notmatch '^[a-z0-9]+(?:-[a-z0-9]+)+$' -or $settings['TOS_ENDPOINT'] -cne ('https://tos-' + $settings['TOS_REGION'] + '.volces.com')) { throw 'TOS_ENDPOINT must be the public HTTPS endpoint matching TOS_REGION.' }
    if ($settings['TOS_KEY_PREFIX'] -notmatch '^local-real/(?:[a-zA-Z0-9_-]+/)*$') { throw 'TOS_KEY_PREFIX must stay under local-real/ and end with /.' }
    # An explicitly configured existing asset group may be reused by user choice.
    $uri = $null
    if (-not [Uri]::TryCreate($settings['LOCAL_PUBLIC_BASE_URL'], [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -ne 'https' -or $uri.AbsolutePath -ne '/' -or $uri.Query -or $uri.Fragment -or $uri.UserInfo) { throw 'LOCAL_PUBLIC_BASE_URL must be a public HTTPS origin, without path, query or credentials.' }
    if ($uri.Host -match 'guizhilv|juxiaobai|termdev' -or [Uri]::CheckHostName($uri.Host) -eq 'IPv4' -or $uri.Host -eq 'localhost') { throw 'Production/test hosts and IP addresses are forbidden as local tunnel origins.' }
    $settings['LOCAL_PUBLIC_BASE_URL'] = $settings['LOCAL_PUBLIC_BASE_URL'].TrimEnd('/')
} elseif (-not $settings['LOCAL_PUBLIC_BASE_URL']) {
    $settings['LOCAL_PUBLIC_BASE_URL'] = "http://127.0.0.1:$MediaPort"
}
# Compose normally prioritizes shell variables over --env-file. Pin every accepted value
# for this invocation so old production shell variables cannot override local settings.
$settings['LOCAL_CLOUD_ENABLED'] = 'false'
$settings['HEJIAN_LOCAL_REAL_NAME'] = $ProjectName
$settings['HEJIAN_GATEWAY_PORT'] = [string]$GatewayPort
$settings['HEJIAN_MEDIA_PORT'] = [string]$MediaPort
$settings['HEJIAN_MYSQL_PORT'] = [string]$MysqlPort
$settings['HEJIAN_REDIS_PORT'] = [string]$RedisPort
$saved = @{}
$composeOverrideKeys = @(
    'LOCAL_CLOUD_ENABLED',
    'HEJIAN_LOCAL_REAL_NAME',
    'HEJIAN_GATEWAY_PORT',
    'HEJIAN_MEDIA_PORT',
    'HEJIAN_MYSQL_PORT',
    'HEJIAN_REDIS_PORT'
)
foreach ($key in $allowed + $composeOverrideKeys) {
    $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
    [Environment]::SetEnvironmentVariable($key, [string]$settings[$key], 'Process')
}
function Invoke-LocalCompose([string[]]$ComposeArgs) {
    & docker compose --project-name $ProjectName --env-file $configFile -f $compose @ComposeArgs
    if ($LASTEXITCODE -ne 0) { throw "Local Docker command failed (exit $LASTEXITCODE)." }
}
function Wait-LocalApi([bool]$CloudEnabled) {
    # Recreated containers may be healthy before the gateway refreshes Docker DNS.
    for ($attempt = 1; $attempt -le 10; $attempt++) {
        try {
            $response = Invoke-RestMethod ("http://127.0.0.1:{0}/api/v2/health" -f $GatewayPort) -TimeoutSec 3
            if ($response.data.environment -eq 'local-real' -and $response.data.redis -and
                $response.data.cloud_calls_enabled -eq $CloudEnabled) { return $response }
        } catch {
            # A brief 502 during container replacement is retried, not treated as success.
        }
        if ($attempt -lt 10) { Start-Sleep -Seconds 2 }
    }
    throw "Local API did not become ready at http://127.0.0.1:$GatewayPort. Check phase2-api and gateway logs."
}
try {
    if ($Down) { Invoke-LocalCompose @('down'); exit 0 }
    Invoke-LocalCompose @('config','--quiet')
    if ($CheckOnly) { Write-Host 'Local configuration checks passed. No cloud requests made.'; exit 0 }
    if ($ProjectName -eq 'aitravel-local-real') {
        # Preserve the original script behavior only for the original project identity.
        & docker compose --project-name aitravel-local -f (Join-Path $root 'infra/docker-compose.local.yml') down
        if ($LASTEXITCODE -ne 0) { throw 'Could not stop the previous local stack.' }
    }
    if ($NoBuild) { Invoke-LocalCompose @('up','--no-build','-d','--wait','--wait-timeout','180') }
    else { Invoke-LocalCompose @('up','--build','-d','--wait','--wait-timeout','180') }
    $null = Wait-LocalApi $false
    if (-not $Offline) {
        $probe = $settings['LOCAL_PUBLIC_BASE_URL'] + '/media/' + $settings['LOCAL_MEDIA_TOKEN'] + '/ready'
        try { $proof = Invoke-RestMethod -Uri $probe -TimeoutSec 20 } catch { throw 'Media tunnel probe failed. Point the HTTPS tunnel at http://127.0.0.1:8081 and retry. Cloud calls remain disabled.' }
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $expected = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($settings['LOCAL_MEDIA_TOKEN'])))).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
        if ($proof.installation -ne $expected) { throw 'HTTPS tunnel is not connected to this local installation. Cloud calls remain disabled.' }
        [Environment]::SetEnvironmentVariable('LOCAL_CLOUD_ENABLED', 'true', 'Process')
        Invoke-LocalCompose @('up','-d','--wait','--wait-timeout','180')
    }
    $health = Wait-LocalApi (-not [bool]$Offline)
    Write-Host ("WeChat developer tools API: http://127.0.0.1:{0}" -f $GatewayPort)
    Write-Host ("Media-only tunnel target: http://127.0.0.1:{0} (NEVER {1})" -f $MediaPort, $GatewayPort)
    Write-Host ('Real cloud calls enabled: ' + $health.data.cloud_calls_enabled)
} finally {
    foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process') }
}
