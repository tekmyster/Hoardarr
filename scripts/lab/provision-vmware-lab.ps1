[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [string]$CredentialEnvFile,

    [string]$DatastoreName = '400GB SSDs',
    [string]$PortgroupName = 'Data Vlan 200',
    [string]$FolderName = 'Hoardarr Development',
    [string]$ApplianceIso,
    [switch]$AttachDataDisks
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

    $datastoreIso = $null
    if ($ApplianceIso) {
        $resolvedIso = (Resolve-Path -LiteralPath $ApplianceIso).Path
        $datastoreIso = "[$DatastoreName] hoardarr-lab/hoardarr-0.3.11-beta1.iso"
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
            New-NetworkAdapter -VM $vm -Portgroup $portgroup -Type Vmxnet3 `
                -StartConnected -Confirm:$false | Out-Null
            Set-VM -VM $vm -MemoryHotAddEnabled $true -Confirm:$false | Out-Null
        }
        if ($datastoreIso -and $vm -and $PSCmdlet.ShouldProcess($name, 'Attach appliance ISO')) {
            $cd = Get-CDDrive -VM $vm -ErrorAction SilentlyContinue
            if (-not $cd) {
                New-CDDrive -VM $vm -IsoPath $datastoreIso -StartConnected `
                    -Confirm:$false | Out-Null
            } else {
                Set-CDDrive -CD $cd -IsoPath $datastoreIso -StartConnected `
                    -Confirm:$false | Out-Null
            }
        }
        if ($AttachDataDisks -and $vm -and $PSCmdlet.ShouldProcess($name, 'Attach thin lab VMDKs')) {
            if ($vm.PowerState -ne 'PoweredOff') {
                throw "$name must be powered off before attaching lab data disks."
            }
            Ensure-VirtualDataDisks -Vm $vm -Datastore $datastore
        }
    }

    $inventory = foreach ($vm in Get-VM -Location $folder | Where-Object { $_.Name -in @('Hoardarr-A', 'Hoardarr-B') }) {
        [ordered]@{
            name = $vm.Name
            power_state = [string]$vm.PowerState
            cpu_count = $vm.NumCpu
            memory_gib = $vm.MemoryGB
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
