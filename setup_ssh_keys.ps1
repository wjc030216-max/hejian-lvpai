# One-time setup: create a deploy key and install it on the three servers.
# After this, deploy_all.ps1 no longer asks for passwords.
# Run once after configuring deploy-config/deploy-targets.json.
[CmdletBinding()]
param(
    [switch]$Force,
    [string]$Targets = 'deploy-config/deploy-targets.json'
)

$ErrorActionPreference = 'Stop'

$sshDir = Join-Path $env:USERPROFILE '.ssh'
$targetFile = if ([IO.Path]::IsPathRooted($Targets)) { $Targets } else { Join-Path $PSScriptRoot $Targets }
if (-not (Test-Path -LiteralPath $targetFile)) {
    throw "Configure $targetFile first; legacy Guizhilv hosts are not allowed."
}
$config = Get-Content -LiteralPath $targetFile -Raw -Encoding UTF8 | ConvertFrom-Json
$key = if ($config.ssh_key) {
    [Environment]::ExpandEnvironmentVariables([string]$config.ssh_key)
} else { Join-Path $sshDir 'hejian_deploy' }
$key = [IO.Path]::GetFullPath($key)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $key) | Out-Null
New-Item -ItemType Directory -Force -Path $sshDir | Out-Null

if ((Test-Path -LiteralPath $key) -and -not $Force) {
    Write-Host "Deploy key already exists: $key" -ForegroundColor Green
} else {
    Write-Host 'Generating ed25519 deploy key...' -ForegroundColor Cyan
    & ssh-keygen -t ed25519 -f $key -N '""' -C 'hejian-lvpai-deploy'
    if ($LASTEXITCODE -ne 0) { throw 'ssh-keygen failed' }
}

$pub = "$key.pub"
if (-not (Test-Path -LiteralPath $pub)) { throw "Missing public key: $pub" }
$hosts = @($config.nodes)
$legacyHosts = @('115.191.23.137', '115.191.26.134', '115.190.53.29')

foreach ($node in $hosts) {
    if ($legacyHosts -contains $node.host -or $node.host -match '(?i)guizhilv') {
        throw "Refusing legacy Guizhilv host: $($node.host)"
    }
    Write-Host "==> Installing key on $($node.user)@$($node.host) (enter password once)..." -ForegroundColor Cyan
    Get-Content -LiteralPath $pub | & ssh -o StrictHostKeyChecking=accept-new "$($node.user)@$($node.host)" "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
    if ($LASTEXITCODE -ne 0) { throw "Failed to install key on $($node.host)" }
}

Write-Host ''
Write-Host 'Done. Future deploy_all.ps1 runs will use this key (no passwords).' -ForegroundColor Green
