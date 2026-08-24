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
    [switch]$RecreateOsDisk
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
    $controller = Get-ScsiController -VM $Vm | Where-Object { $_.ExtensionData.BusNumber -eq 1 }
    if (-not $controller) {
        $controller = New-ScsiController -VM $Vm -Type ParaVirtual -BusSharingMode NoSharing
    }
    $desired = @(12, 12, 12, 8)
    $existing = @(Get-HardDisk -VM $Vm | Where-Object { $_.ExtensionData.ControllerKey -eq $controller.ExtensionData.Key })
    for ($index = $existing.Count; $index -lt $desired.Count; $index++) {
        New-HardDisk -VM $Vm -Datastore $Datastore -Controller $controller `
            -CapacityGB $desired[$index] -StorageFormat Thin -Confirm:$false | Out-Null
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
    $datastoreIso = if ($ApplianceIso -or $ReuseUploadedIso) {
        "[$DatastoreName] hoardarr-lab/hoardarr-0.3.11-beta1.iso"
    } else {
        $null
    }
    if ($ApplianceIso) {
        $resolvedIso = (Resolve-Path -LiteralPath $ApplianceIso).Path
        if ($PSCmdlet.ShouldProcess($datastoreIso, 'Upload appliance ISO')) {
            New-PSDrive -Name HoardarrLabDatastore -PSProvider VimDatastore `
                -Root '\' -Location $datastore | Out-Null
            if (-not (Test-Path 'HoardarrLabDatastore:\hoardarr-lab')) {
                New-Item -ItemType Directory -Path 'HoardarrLabDatastore:\hoardarr-lab' | Out-Null
            }
            Copy-DatastoreItem -Item $resolvedIso `
                -Destination 'HoardarrLabDatastore:\hoardarr-lab\hoardarr-0.3.11-beta1.iso' `
                -Force -Confirm:$false
        }
    }

    foreach ($name in @('Hoardarr-A', 'Hoardarr-B')) {
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
        if ($vm -and $PSCmdlet.ShouldProcess($name, 'Reconcile lab network adapter')) {
            Ensure-LabNetwork -Vm $vm -Portgroup $portgroup
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
