[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [string]$CredentialEnvFile,

    [string]$DatastoreName = '400GB SSDs',
    [string]$PortgroupName = 'Data Vlan 200',
    [string]$FolderName = 'Hoardarr Development',
    [string]$ApplianceIso,
    [switch]$AttachDataDisks,
    [switch]$PowerOn,
    [switch]$ReuseUploadedIso,
    [switch]$RecreateOsDisk,
    [switch]$BootInstalledOs,
    [ValidateSet('Hoardarr-A', 'Hoardarr-B')]
    [string]$TargetVmName,
    [string]$ConsoleScreenshotDirectory
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Read-KeyValueFile {
    param([Parameter(Mandatory)][string]$Path)

    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^[^#][^=]*=') {
            $key, $value = $line -split '=', 2
            $values[$key.Trim()] = $value.Trim().Trim('"').Trim("'")
        }
    }
    foreach ($required in @('host', 'user', 'pass')) {
        if (-not $values.ContainsKey($required) -or [string]::IsNullOrWhiteSpace($values[$required])) {
            throw "Credential file is missing required key: $required"
        }
    }
    return $values
}

function Ensure-VirtualDataDisks {
    param(
        [Parameter(Mandatory)]$Vm,
        [Parameter(Mandatory)]$Datastore
    )

    # These are ordinary thin VMDKs. This helper deliberately has no RDM/device-path option.
    # SCSI 0:0 is the appliance OS disk; lab data starts on a separate controller.
    # The first four disks retain the accepted mergerFS/ZFS/landing topology. The
    # three additional 6-GiB disks are the bounded provider-validation set used
    # for real Linux MD on B and SnapRAID on A. Existing disks are validated in
    # exact controller/unit order and are never resized or replaced here.
    $desired = @(12, 12, 12, 8, 6, 6, 6)
    $controller = Get-ScsiController -VM $Vm | Where-Object { $_.ExtensionData.BusNumber -eq 1 }
    if (-not $controller) {
        $expectedOsFilename = "[$($Datastore.Name)] $($Vm.Name)/$($Vm.Name).vmdk"
        $unexpectedDisks = @(
            Get-HardDisk -VM $Vm | Where-Object { [string]$_.Filename -ne $expectedOsFilename }
        )
        if ($unexpectedDisks.Count -ne 0) {
            throw "$($Vm.Name) has data disks without the dedicated lab SCSI controller."
        }

        # Current PowerCLI creates a controller from an existing disk rather than
        # accepting -VM. Create the first bounded thin VMDK on the default controller,
        # then atomically move that disk onto a new PVSCSI controller while powered off.
        $firstDisk = New-HardDisk -VM $Vm -Datastore $Datastore `
            -CapacityGB $desired[0] -StorageFormat Thin -Confirm:$false
        $controller = New-ScsiController -HardDisk $firstDisk -Type ParaVirtual `
            -BusSharingMode NoSharing -Confirm:$false
    }
    $existing = @(Get-HardDisk -VM $Vm | Where-Object { $_.ExtensionData.ControllerKey -eq $controller.ExtensionData.Key })
    if ($existing.Count -gt $desired.Count) {
        throw "$($Vm.Name) has more data disks than the bounded lab topology permits."
    }
    $existing = @($existing | Sort-Object { $_.ExtensionData.UnitNumber })
    for ($index = 0; $index -lt $existing.Count; $index++) {
        if (
            [math]::Abs([double]$existing[$index].CapacityGB - $desired[$index]) -gt 0.01 `
            -or [string]$existing[$index].StorageFormat -ne 'Thin' `
            -or [string]$existing[$index].DiskType -ne 'Flat'
        ) {
            throw "$($Vm.Name) data disk $index does not match the bounded thin-VMDK topology."
        }
    }
    for ($index = $existing.Count; $index -lt $desired.Count; $index++) {
        New-HardDisk -VM $Vm -Datastore $Datastore -Controller $controller `
            -CapacityGB $desired[$index] -StorageFormat Thin -Confirm:$false | Out-Null
    }
}

function Ensure-VirtualDiskIdentity {
    param([Parameter(Mandatory)]$Vm)

    # VMware only exposes VMDK UUIDs to the Linux guest when disk.EnableUUID is
    # enabled. Hoardarr must never manage lab storage by /dev/sdX alone.
    $setting = Get-AdvancedSetting -Entity $Vm -Name 'disk.EnableUUID' `
        -ErrorAction SilentlyContinue
    if ($setting -and [string]$setting.Value -eq 'TRUE') {
        return
    }
    if ($Vm.PowerState -ne 'PoweredOff') {
        throw "$($Vm.Name) must be powered off before enabling stable virtual-disk identity."
    }
    if ($setting) {
        Set-AdvancedSetting -AdvancedSetting $setting -Value 'TRUE' `
            -Confirm:$false | Out-Null
    } else {
        New-AdvancedSetting -Entity $Vm -Name 'disk.EnableUUID' -Value 'TRUE' `
            -Confirm:$false | Out-Null
    }
}

function Ensure-LabNetwork {
    param(
        [Parameter(Mandatory)]$Vm,
        [Parameter(Mandatory)]$Portgroup
    )

    $adapters = @(Get-NetworkAdapter -VM $Vm)
    $target = $adapters | Where-Object { $_.NetworkName -eq $Portgroup.Name } | Select-Object -First 1
    if ((-not $target -or $adapters.Count -ne 1) -and $Vm.PowerState -ne 'PoweredOff') {
        throw "$($Vm.Name) must be powered off before reconciling its lab network adapter."
    }
    if (-not $target -and $adapters.Count -gt 0) {
        $target = Set-NetworkAdapter -NetworkAdapter $adapters[0] -Portgroup $Portgroup `
            -Type Vmxnet3 -StartConnected $true -Confirm:$false
    }
    if (-not $target) {
        $target = New-NetworkAdapter -VM $Vm -Portgroup $Portgroup -Type Vmxnet3 `
            -StartConnected -Confirm:$false
    }
    foreach ($adapter in $adapters) {
        if ($adapter.Id -ne $target.Id) {
            Remove-NetworkAdapter -NetworkAdapter $adapter -Confirm:$false
        }
    }
}

function Reset-VirtualOsDisk {
    param(
        [Parameter(Mandatory)]$Vm,
        [Parameter(Mandatory)]$Datastore
    )

    $disks = @(Get-HardDisk -VM $Vm)
    $expectedFilename = "[$($Datastore.Name)] $($Vm.Name)/$($Vm.Name).vmdk"
    if (
        $disks.Count -ne 1 `
        -or [math]::Abs([double]$disks[0].CapacityGB - 24.0) -gt 0.01 `
        -or [string]$disks[0].StorageFormat -ne 'Thin' `
        -or [string]$disks[0].DiskType -ne 'Flat' `
        -or [string]$disks[0].Filename -ne $expectedFilename
    ) {
        throw "$($Vm.Name) does not have the exact disposable one-disk lab OS topology."
    }
    if ($Vm.PowerState -ne 'PoweredOff') {
        Stop-VM -VM $Vm -Kill -Confirm:$false | Out-Null
        $Vm = Get-VM -Name $Vm.Name
    }
    Remove-HardDisk -HardDisk $disks[0] -DeletePermanently -Confirm:$false
    New-HardDisk -VM $Vm -Datastore $Datastore -CapacityGB 24 `
        -StorageFormat Thin -Confirm:$false | Out-Null
}

function Assert-ExactLabOsTopology {
    param(
        [Parameter(Mandatory)]$Vm,
        [Parameter(Mandatory)]$Datastore
    )

    $disks = @(Get-HardDisk -VM $Vm)
    $expectedFilename = "[$($Datastore.Name)] $($Vm.Name)/$($Vm.Name).vmdk"
    if (
        $disks.Count -ne 1 `
        -or [math]::Abs([double]$disks[0].CapacityGB - 24.0) -gt 0.01 `
        -or [string]$disks[0].StorageFormat -ne 'Thin' `
        -or [string]$disks[0].DiskType -ne 'Flat' `
        -or [string]$disks[0].Filename -ne $expectedFilename
    ) {
        throw "$($Vm.Name) does not have the exact disposable one-disk lab OS topology."
    }
}

function Wait-VimTaskResult {
    param(
        [Parameter(Mandatory)]$TaskReference,
        [int]$TimeoutSeconds = 60
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $task = Get-View -Id $TaskReference
    while ([string]$task.Info.State -in @('queued', 'running')) {
        if ([DateTime]::UtcNow -ge $deadline) {
            throw 'VMware console screenshot timed out.'
        }
        Start-Sleep -Milliseconds 500
        $task.UpdateViewData('Info.State', 'Info.Error', 'Info.Result')
    }
    if ([string]$task.Info.State -ne 'success') {
        throw "VMware console screenshot failed: $($task.Info.Error.LocalizedMessage)"
    }
    return $task.Info.Result
}

$credentials = Read-KeyValueFile -Path $CredentialEnvFile
$connection = $null
try {
    $connection = Connect-VIServer -Server $credentials.host -User $credentials.user `
        -Password $credentials.pass -Force -WarningAction SilentlyContinue
    $vmHost = Get-VMHost | Where-Object { $_.ConnectionState -eq 'Connected' } | Select-Object -First 1
    if (-not $vmHost) {
        throw 'No connected VMware host is available.'
    }
    if (($vmHost.MemoryTotalGB - $vmHost.MemoryUsageGB) -lt 4) {
        throw 'At least 4 GiB of host memory must be free before provisioning the two-node lab.'
    }
    $datastore = Get-Datastore -Name $DatastoreName
    if ($datastore.FreeSpaceGB -lt 100) {
        throw 'At least 100 GiB of datastore capacity must be free for the bounded virtual lab.'
    }
    $portgroup = Get-VDPortgroup -Name $PortgroupName
    $rootFolder = Get-Folder -Name 'vm' | Select-Object -First 1
    $folder = Get-Folder -Name $FolderName -ErrorAction SilentlyContinue
    if (-not $folder -and $PSCmdlet.ShouldProcess($FolderName, 'Create VM folder')) {
        $folder = New-Folder -Name $FolderName -Location $rootFolder
    }

    if ($RecreateOsDisk -and -not ($ApplianceIso -or $ReuseUploadedIso)) {
        throw 'Recreating a lab OS disk requires the appliance ISO to be supplied or explicitly reused.'
    }
    if ($BootInstalledOs -and ($RecreateOsDisk -or $ApplianceIso -or $ReuseUploadedIso)) {
        throw 'BootInstalledOs cannot be combined with ISO attachment or OS-disk recreation.'
    }
    $datastoreIso = if ($ReuseUploadedIso) {
        "[$DatastoreName] hoardarr-lab/hoardarr-0.3.11-beta1.iso"
    } else {
        $null
    }
    if ($ApplianceIso) {
        $resolvedIso = (Resolve-Path -LiteralPath $ApplianceIso).Path
        $isoDigest = (Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedIso).Hash.ToLowerInvariant()
        $isoLeaf = "hoardarr-0.3.11-beta1-$($isoDigest.Substring(0, 12)).iso"
        $datastoreIso = "[$DatastoreName] hoardarr-lab/$isoLeaf"
        if (-not $ReuseUploadedIso -and $PSCmdlet.ShouldProcess($datastoreIso, 'Upload appliance ISO')) {
            New-PSDrive -Name HoardarrLabDatastore -PSProvider VimDatastore `
                -Root '\' -Location $datastore | Out-Null
            if (-not (Test-Path 'HoardarrLabDatastore:\hoardarr-lab')) {
                New-Item -ItemType Directory -Path 'HoardarrLabDatastore:\hoardarr-lab' | Out-Null
            }
            Copy-DatastoreItem -Item $resolvedIso `
                -Destination "HoardarrLabDatastore:\hoardarr-lab\$isoLeaf" `
                -Force -Confirm:$false
        }
    }

    $targetNames = if ($TargetVmName) { @($TargetVmName) } else { @('Hoardarr-A', 'Hoardarr-B') }
    foreach ($name in $targetNames) {
        $vm = Get-VM -Name $name -ErrorAction SilentlyContinue
        if (-not $vm -and $PSCmdlet.ShouldProcess($name, 'Create persistent lab VM')) {
            $vm = New-VM -Name $name -VMHost $vmHost -Datastore $datastore -DiskGB 24 `
                -DiskStorageFormat Thin -MemoryGB 2 -NumCpu 2 -GuestId ubuntu64Guest `
                -Location $folder -Notes (
                    'Persistent Hoardarr 0.3.11 Beta 1 development lab; virtual storage only; ' +
                    'protected physical SSDs excluded'
                ) -Confirm:$false
            Set-VM -VM $vm -MemoryHotAddEnabled $true -Confirm:$false | Out-Null
        }
        if ($RecreateOsDisk -and $vm -and $PSCmdlet.ShouldProcess($name, 'Recreate exact disposable virtual OS disk')) {
            Reset-VirtualOsDisk -Vm $vm -Datastore $datastore
            $vm = Get-VM -Name $name
        }
        if ($BootInstalledOs -and $vm -and $PSCmdlet.ShouldProcess($name, 'Detach installer media and boot exact virtual OS disk')) {
            Assert-ExactLabOsTopology -Vm $vm -Datastore $datastore
            if ($vm.PowerState -ne 'PoweredOff') {
                Stop-VM -VM $vm -Kill -Confirm:$false | Out-Null
                $vm = Get-VM -Name $name
            }
            foreach ($cd in @(Get-CDDrive -VM $vm -ErrorAction SilentlyContinue)) {
                Set-CDDrive -CD $cd -NoMedia -StartConnected:$false -Confirm:$false | Out-Null
            }
            Start-VM -VM $vm -Confirm:$false | Out-Null
            $vm = Get-VM -Name $name
        }
        if ($vm -and $PSCmdlet.ShouldProcess($name, 'Reconcile lab network adapter')) {
            Ensure-LabNetwork -Vm $vm -Portgroup $portgroup
        }
        if ($vm -and $PSCmdlet.ShouldProcess($name, 'Enable stable virtual-disk identity')) {
            Ensure-VirtualDiskIdentity -Vm $vm
        }
        if ($datastoreIso -and $vm -and $PSCmdlet.ShouldProcess($name, 'Attach appliance ISO')) {
            $cd = Get-CDDrive -VM $vm -ErrorAction SilentlyContinue
            if (-not $cd) {
                New-CDDrive -VM $vm -IsoPath $datastoreIso -StartConnected `
                    -Confirm:$false | Out-Null
            } else {
                Set-CDDrive -CD $cd -IsoPath $datastoreIso -StartConnected `
                    $true -Confirm:$false | Out-Null
            }
        }
        if ($AttachDataDisks -and $vm -and $PSCmdlet.ShouldProcess($name, 'Attach thin lab VMDKs')) {
            if ($vm.PowerState -ne 'PoweredOff') {
                throw "$name must be powered off before attaching lab data disks."
            }
            Ensure-VirtualDataDisks -Vm $vm -Datastore $datastore
        }
        if ($PowerOn -and $vm -and $vm.PowerState -ne 'PoweredOn' -and $PSCmdlet.ShouldProcess($name, 'Power on persistent lab VM')) {
            Start-VM -VM $vm -Confirm:$false | Out-Null
        }
        if ($ConsoleScreenshotDirectory -and $vm -and $PSCmdlet.ShouldProcess($name, 'Capture bounded console screenshot')) {
            $localDirectory = [IO.Path]::GetFullPath($ConsoleScreenshotDirectory)
            [IO.Directory]::CreateDirectory($localDirectory) | Out-Null
            $remoteScreenshot = [string](Wait-VimTaskResult -TaskReference $vm.ExtensionData.CreateScreenshot_Task())
            $expectedPrefix = "[$DatastoreName] $name/"
            if (-not $remoteScreenshot.StartsWith($expectedPrefix, [StringComparison]::Ordinal)) {
                throw "$name returned an unexpected console screenshot path."
            }
            if (-not (Get-PSDrive -Name HoardarrLabDatastore -ErrorAction SilentlyContinue)) {
                New-PSDrive -Name HoardarrLabDatastore -PSProvider VimDatastore `
                    -Root '\' -Location $datastore | Out-Null
            }
            $relativeScreenshot = $remoteScreenshot.Substring("[$DatastoreName] ".Length)
            $remoteProviderPath = "HoardarrLabDatastore:\$relativeScreenshot"
            Copy-DatastoreItem -Item $remoteProviderPath `
                -Destination (Join-Path $localDirectory "$name-console.png") -Force -Confirm:$false
            Remove-Item -LiteralPath $remoteProviderPath -Force -WhatIf:$false
        }
    }

    $inventory = foreach ($vm in Get-VM -Location $folder | Where-Object { $_.Name -in @('Hoardarr-A', 'Hoardarr-B') }) {
        [ordered]@{
            name = $vm.Name
            power_state = [string]$vm.PowerState
            cpu_count = $vm.NumCpu
            memory_gib = $vm.MemoryGB
            boot_time = if ($vm.ExtensionData.Runtime.BootTime) { $vm.ExtensionData.Runtime.BootTime.ToUniversalTime().ToString('o') } else { $null }
            guest_state = [string]$vm.ExtensionData.Guest.GuestState
            tools_status = [string]$vm.ExtensionData.Guest.ToolsRunningStatus
            guest_ips = @($vm.Guest.IPAddress | Where-Object { $_ })
            disks = @(Get-HardDisk -VM $vm | ForEach-Object {
                [ordered]@{
                    name = $_.Name
                    capacity_gib = $_.CapacityGB
                    storage_format = [string]$_.StorageFormat
                    filename = $_.Filename
                }
            })
        }
    }
    $inventory | ConvertTo-Json -Depth 6
} finally {
    if (Get-PSDrive -Name HoardarrLabDatastore -ErrorAction SilentlyContinue) {
        Remove-PSDrive -Name HoardarrLabDatastore
    }
    if ($connection) {
        Disconnect-VIServer -Server $connection -Confirm:$false | Out-Null
    }
}
