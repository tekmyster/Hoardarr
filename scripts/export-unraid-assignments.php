<?php
declare(strict_types=1);

/*
 * Read-only Unraid assignment exporter for Hoardarr.
 *
 * This script reads emhttp's cached runtime state and sysfs-backed lsblk
 * identity. It does not query SMART, start/stop the array, mount a disk, or
 * write Unraid configuration. Run it on the old Unraid server while the
 * assignment state is still available:
 *
 *   php export-unraid-assignments.php > hoardarr-unraid-assignments.json
 */

function fail_export(string $message): never
{
    fwrite(STDERR, "Hoardarr Unraid export failed: {$message}\n");
    exit(1);
}

function option_value(array $arguments, string $name): ?string
{
    $index = array_search($name, $arguments, true);
    if ($index === false) {
        return null;
    }
    $value = $arguments[$index + 1] ?? null;
    return is_string($value) && $value !== '' ? $value : null;
}

$statePath = option_value($argv, '--state');
if ($statePath === null) {
    foreach (['/usr/local/emhttp/state/disks.ini', '/var/local/emhttp/disks.ini'] as $candidate) {
        if (is_readable($candidate)) {
            $statePath = $candidate;
            break;
        }
    }
}
if ($statePath === null || !is_readable($statePath)) {
    fail_export('the cached emhttp disks.ini file is unavailable or unreadable');
}
if (filesize($statePath) === false || filesize($statePath) > 1048576) {
    fail_export('the emhttp state file exceeds the 1 MiB safety limit');
}
$state = parse_ini_file($statePath, true, INI_SCANNER_RAW);
if (!is_array($state)) {
    fail_export('the emhttp state file is malformed');
}

$lsblkPath = option_value($argv, '--lsblk-json');
if ($lsblkPath !== null) {
    if (!is_readable($lsblkPath) || filesize($lsblkPath) === false || filesize($lsblkPath) > 1048576) {
        fail_export('the supplied lsblk fixture is unavailable or oversized');
    }
    $lsblkText = file_get_contents($lsblkPath);
} else {
    $lsblkText = shell_exec('lsblk -b -d -J -o NAME,SERIAL,WWN,SIZE 2>/dev/null');
}
if (!is_string($lsblkText) || strlen($lsblkText) > 1048576) {
    fail_export('lsblk identity output is unavailable or oversized');
}
try {
    $lsblk = json_decode($lsblkText, true, 32, JSON_THROW_ON_ERROR);
} catch (JsonException $error) {
    fail_export('lsblk identity output is malformed JSON');
}
$devices = [];
foreach (($lsblk['blockdevices'] ?? []) as $device) {
    if (!is_array($device) || !is_string($device['name'] ?? null)) {
        continue;
    }
    $devices[basename($device['name'])] = $device;
}

$assignments = [];
foreach ($state as $section => $values) {
    if (!is_string($section) || !is_array($values)) {
        continue;
    }
    $slot = strtolower((string)($values['name'] ?? $section));
    if (!preg_match('/^(?:parity2?|disk(?:[1-9]|1[0-9]|2[0-8]))$/', $slot)) {
        continue;
    }
    $role = str_starts_with($slot, 'parity') ? 'parity' : 'data';
    $reportedType = strtolower((string)($values['type'] ?? ''));
    if (($role === 'parity' && $reportedType !== 'parity') ||
        ($role === 'data' && $reportedType !== 'data')) {
        fail_export("slot {$slot} has conflicting role metadata");
    }
    $kernelName = basename((string)($values['device'] ?? ''));
    $identity = $devices[$kernelName] ?? null;
    $serial = is_array($identity) ? trim((string)($identity['serial'] ?? '')) : '';
    if ($kernelName === '' || $serial === '') {
        fail_export("slot {$slot} does not have a stable serial in cached lsblk data");
    }
    $wwn = trim((string)($identity['wwn'] ?? ''));
    $size = $identity['size'] ?? null;
    $assignments[] = [
        'slot' => $slot,
        'role' => $role,
        'serial' => $serial,
        'wwn' => $wwn !== '' ? $wwn : null,
        'eui64' => null,
        'nguid' => null,
        'capacity_bytes' => is_numeric($size) && (int)$size > 0 ? (int)$size : null,
        'filesystem_type' => $role === 'data' && trim((string)($values['fsType'] ?? '')) !== ''
            ? strtolower(trim((string)$values['fsType']))
            : null,
    ];
}
if (count($assignments) < 1 || count($assignments) > 30) {
    fail_export('the runtime state did not contain between 1 and 30 array assignments');
}
usort($assignments, fn(array $left, array $right): int => strcmp($left['slot'], $right['slot']));

$versionPath = option_value($argv, '--version-file') ?? '/etc/unraid-version';
$version = null;
if (is_readable($versionPath) && filesize($versionPath) !== false && filesize($versionPath) <= 65536) {
    $versionDocument = parse_ini_file($versionPath, false, INI_SCANNER_RAW);
    if (is_array($versionDocument) && is_string($versionDocument['version'] ?? null)) {
        $version = substr(trim($versionDocument['version']), 0, 64);
    }
}

$document = [
    'schema_version' => 1,
    'source' => 'unraid_runtime_state',
    'captured_at' => gmdate('Y-m-d\TH:i:s\Z'),
    'unraid_version' => $version !== '' ? $version : null,
    'assignments' => $assignments,
];
fwrite(STDOUT, json_encode($document, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR));
fwrite(STDOUT, "\n");
