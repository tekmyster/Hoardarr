[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$ApplianceIso,

    [switch]$AttachDataDisks,

    [switch]$PowerOn,

    [switch]$ReuseUploadedIso,

    [switch]$RecreateOsDisk,

    [switch]$BootInstalledOs,

    [ValidateSet('Hoardarr-A', 'Hoardarr-B')]
    [string]$TargetVmName,

    [string]$ConsoleScreenshotDirectory,

    [string]$CredentialRoot = 'C:\Users\dmessana\Desktop\all servers',

    [string]$VCenterHost = 'cptnyc-vcsa01.vcenter.cptnyc.com'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Read-KeyValueFile {
    param([Parameter(Mandatory)][string]$Path)

    $values = @{}
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        if ($line -match '^\s*([^#:=]+)\s*[:=](.*)$') {
            $values[$matches[1].Trim()] = $matches[2].Trim()
        }
    }
    return $values
}

$keepassCli = 'C:\Program Files\KeePassXC\keepassxc-cli.exe'
$keepassEnvPath = Join-Path $CredentialRoot 'keepass.env'
$provisioner = Join-Path $PSScriptRoot 'provision-vmware-lab.ps1'
$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) ("hoardarr-vcenter-{0}" -f [guid]::NewGuid())
$credentialFile = Join-Path $temporaryRoot 'credential.env'

try {
    if ($ApplianceIso -and -not (Test-Path -LiteralPath $ApplianceIso -PathType Leaf)) {
        throw "The appliance ISO does not exist: $ApplianceIso"
    }
    if (-not (Test-Path -LiteralPath $keepassEnvPath -PathType Leaf)) {
        throw 'The local KeePass environment file is unavailable.'
    }
    if (-not (Test-Path -LiteralPath $keepassCli -PathType Leaf)) {
        throw 'KeePassXC CLI is unavailable.'
    }
    $keepassEnv = Read-KeyValueFile -Path $keepassEnvPath
    $vaultPath = [string]$keepassEnv.KEEPASS_VAULT_FILE
    if (-not [IO.Path]::IsPathRooted($vaultPath)) {
        $vaultPath = Join-Path $CredentialRoot $vaultPath
    }
    $processInfo = [Diagnostics.ProcessStartInfo]::new()
    $processInfo.FileName = $keepassCli
    foreach ($argument in @(
        'show', '-q', '-s', '-a', 'Username', '-a', 'Password',
        $vaultPath, 'All Servers/vcenter/Other/vcenter - Other - pass'
    )) {
        $processInfo.ArgumentList.Add($argument)
    }
    $processInfo.UseShellExecute = $false
    $processInfo.RedirectStandardInput = $true
    $processInfo.RedirectStandardOutput = $true
    $processInfo.RedirectStandardError = $true
    $process = [Diagnostics.Process]::Start($processInfo)
    $process.StandardInput.WriteLine([string]$keepassEnv.KEEPASS_MASTER_PASSWORD)
    $process.StandardInput.Close()
    $lines = @($process.StandardOutput.ReadToEnd() -split "`r?`n" | Where-Object { $_ })
    $errorText = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0 -or $lines.Count -ne 2) {
        throw "The exact vCenter credential could not be loaded (exit $($process.ExitCode)): $errorText"
    }
    if ($lines[0] -ne 'administrator@vcenter.cptnyc.com') {
        throw 'The exact credential returned an unexpected vCenter username.'
    }
    [IO.Directory]::CreateDirectory($temporaryRoot) | Out-Null
    [IO.File]::WriteAllLines(
        $credentialFile,
        @("host=$VCenterHost", "user=$($lines[0])", "pass=$($lines[1])")
    )
    $provisionArguments = @{
        CredentialEnvFile = $credentialFile
        AttachDataDisks = $AttachDataDisks
        PowerOn = $PowerOn
        ReuseUploadedIso = $ReuseUploadedIso
        RecreateOsDisk = $RecreateOsDisk
        BootInstalledOs = $BootInstalledOs
        ConsoleScreenshotDirectory = $ConsoleScreenshotDirectory
        Confirm = $false
        WhatIf = $WhatIfPreference
    }
    if ($ApplianceIso) {
        $provisionArguments.ApplianceIso = $ApplianceIso
    }
    if ($TargetVmName) {
        $provisionArguments.TargetVmName = $TargetVmName
    }
    & $provisioner @provisionArguments
} finally {
    if (Test-Path -LiteralPath $temporaryRoot) {
        $resolvedTemporaryRoot = [IO.Path]::GetFullPath($temporaryRoot)
        $resolvedSystemTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        if (
            -not $resolvedTemporaryRoot.StartsWith($resolvedSystemTemp, [StringComparison]::OrdinalIgnoreCase) `
            -or [IO.Path]::GetFileName($resolvedTemporaryRoot) -notmatch '^hoardarr-vcenter-[0-9a-f-]{36}$'
        ) {
            throw 'Refusing to remove an unexpected temporary credential path.'
        }
        # Credential cleanup is mandatory housekeeping, not a simulated lab mutation.
        Remove-Item -LiteralPath $resolvedTemporaryRoot -Recurse -Force -WhatIf:$false
    }
}
