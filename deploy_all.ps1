# One-command deploy to the three phase2 test servers.
#   1. Package locally (phase2-main / phase2-ai / phase2-data)
#   2. Upload to data -> main -> ai in order
#   3. Extract and rebuild/restart each node
#
# Usage (run inside PowerShell):
#   .\deploy_all.ps1
#   .\deploy_all.ps1 -SkipPackage   (reuse the existing dist archives)
#
# Notes:
#   - scp and ssh will each prompt for that server's root password.
#   - Server-side .env.<node> and tls are never overwritten by the package.
[CmdletBinding()]
param(
    [switch]$SkipPackage,
    [ValidateSet('data','main','ai')]
    [string]$Only = '',
    [string]$Targets = 'deploy-config/deploy-targets.json'
)

$ErrorActionPreference = 'Stop'

$Repo = $PSScriptRoot
$Dist = Join-Path $Repo 'dist'
$TargetFile = if ([IO.Path]::IsPathRooted($Targets)) { $Targets } else { Join-Path $Repo $Targets }
if (-not (Test-Path -LiteralPath $TargetFile)) {
    throw "New-project deployment targets are not configured. Refusing to deploy. Create $TargetFile from infra/staging/deploy-targets.example.json."
}
$TargetConfig = Get-Content -LiteralPath $TargetFile -Raw -Encoding UTF8 | ConvertFrom-Json
$ForbiddenHosts = @('115.191.23.137', '115.191.26.134', '115.190.53.29')
$Nodes = @($TargetConfig.nodes)
if ($Nodes.Count -eq 0) { throw 'No deployment nodes configured.' }
foreach ($Node in $Nodes) {
    if ($Node.Name -notin @('data','main','ai') -or -not $Node.Host -or -not $Node.User) {
        throw "Each deployment target needs name, host and user: $($Node | ConvertTo-Json -Compress)"
    }
    if ($ForbiddenHosts -contains $Node.Host -or $Node.Host -match '(?i)guizhilv') {
        throw "Refusing legacy Guizhilv deployment target: $($Node.Host)"
    }
    if (-not $Node.install_path -or $Node.install_path -match '(?i)guizhilv') {
        throw "Refusing legacy Guizhilv install path for $($Node.Name)"
    }
    if ($Node.Archive -notin @('phase2-data.tar.gz','phase2-main.tar.gz','phase2-ai.tar.gz')) {
        throw "Invalid deployment archive for $($Node.Name)"
    }
}
if (($Nodes.Name | Sort-Object -Unique).Count -ne 3) {
    throw 'Deployment targets must contain exactly one data, main and ai node.'
}
$KeyPath = if ($TargetConfig.ssh_key) {
    [Environment]::ExpandEnvironmentVariables([string]$TargetConfig.ssh_key)
} else { Join-Path $env:USERPROFILE '.ssh\hejian_deploy' }
$DeployKey = [IO.Path]::GetFullPath($KeyPath)
$KeyArgs = @()
if (Test-Path -LiteralPath $DeployKey) { $KeyArgs = @('-i', $DeployKey) }
$HostKeyOpts = @('-o', 'StrictHostKeyChecking=accept-new')

if (-not $SkipPackage) {
    Write-Host '==> Packaging locally...' -ForegroundColor Cyan
    & python (Join-Path $Repo 'infra\staging\package_release.py')
    if ($LASTEXITCODE -ne 0) { throw 'package_release.py failed' }
}

foreach ($Node in $Nodes) {
    if ($Only -and $Node.Name -ne $Only) { continue }
    $Archive = Join-Path $Dist $Node.Archive
    if (-not (Test-Path -LiteralPath $Archive)) {
        throw "Missing deploy archive: $Archive"
    }

    Write-Host "==> Uploading $($Node.Name) -> root@$($Node.Host)" -ForegroundColor Cyan
    $RemoteRoot = ([string]$Node.install_path).TrimEnd('/')
    & scp @KeyArgs @HostKeyOpts $Archive "$($Node.User)@$($Node.Host):$RemoteRoot/"
    if ($LASTEXITCODE -ne 0) { throw "scp failed for $($Node.Name)" }

    Write-Host "==> Extracting and rebuilding $($Node.Name) node..." -ForegroundColor Cyan
    $Remote = "cd $RemoteRoot && tar -xzf $($Node.Archive) && python3 infra/staging/start_node.py $($Node.Name)"
    & ssh @KeyArgs @HostKeyOpts "$($Node.User)@$($Node.Host)" $Remote
    if ($LASTEXITCODE -ne 0) { throw "rebuild/start failed for $($Node.Name)" }

    Write-Host "==> $($Node.Name) node done" -ForegroundColor Green
}

Write-Host ''
Write-Host 'All three servers uploaded and restarted.' -ForegroundColor Green
Write-Host 'Verify the main node with:' -ForegroundColor Green
Write-Host ('  curl ' + $TargetConfig.public_api_base_url + '/api/v2/health') -ForegroundColor Gray
