#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

[[ "${GITHUB_ACTIONS:-}" == "true" ]] || {
    echo "two-node VM validation is restricted to a disposable GitHub Actions runner" >&2
    exit 2
}
[[ -f /.hoardarr-disposable-runner ]] || {
    echo "disposable runner marker is missing" >&2
    exit 2
}

ROOT="$(git rev-parse --show-toplevel)"
readonly ROOT
readonly OUTPUT="${ROOT}/dist/validation/two-node-storage"
readonly RUN_ROOT="${RUNNER_TEMP:?}/hoardarr-two-node-${GITHUB_RUN_ID:?}"
readonly BASE_URL="${HOARDARR_UBUNTU_CLOUD_URL:-https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img}"
readonly BASE_NAME="$(basename "${BASE_URL}")"
readonly SHARED_WWID="36000000000000011"
readonly MAX_TOTAL_WRITES="${HOARDARR_TEST_MAX_TOTAL_WRITES:-100663296}"
readonly MAX_DEVICE_WRITES="${HOARDARR_TEST_MAX_DEVICE_WRITES:-67108864}"
readonly READ_SOAK_SECONDS="${HOARDARR_TEST_READ_SOAK_SECONDS:-45}"
readonly WORKLOAD_CONCURRENCY="${HOARDARR_TEST_WORKLOAD_CONCURRENCY:-2}"
readonly WHEEL="${HOARDARR_WHEEL:?HOARDARR_WHEEL must name the built backend wheel}"
readonly WHEEL_NAME="$(basename "${WHEEL}")"

case "${RUN_ROOT}" in
    "${RUNNER_TEMP}"/hoardarr-two-node-*) ;;
    *) echo "unsafe temporary test path" >&2; exit 2 ;;
esac
mkdir -p "${RUN_ROOT}" "${OUTPUT}"

PIDS=()
cleanup() {
    local pid
    for pid in "${PIDS[@]:-}"; do
        [[ -n "${pid}" ]] && kill "${pid}" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    rm -rf --one-file-system -- "${RUN_ROOT}"
}
trap cleanup EXIT

for command in cloud-localds curl fio jq qemu-img qemu-system-x86_64 scp sha256sum ssh; do
    command -v "${command}" >/dev/null || { echo "required command missing: ${command}" >&2; exit 2; }
done

planned_total=0
declare -A planned_by_device=()
charge_write() {
    local device="$1" bytes="$2" next_device
    next_device=$(( ${planned_by_device[${device}]:-0} + bytes ))
    planned_total=$((planned_total + bytes))
    ((next_device <= MAX_DEVICE_WRITES)) || {
        echo "planned writes exceed per-device budget for ${device}" >&2
        exit 2
    }
    ((planned_total <= MAX_TOTAL_WRITES)) || {
        echo "planned writes exceed total SSD-safe budget" >&2
        exit 2
    }
    planned_by_device["${device}"]="${next_device}"
}

# One small deterministic dataset per local virtual SSD, one shared dataset, and
# one short controlled write phase. Repeated stress phases are read-only.
for device in A1 A2 B1 B2; do charge_write "${device}" $((9 * 1024 * 1024)); done
charge_write SHARED $((36 * 1024 * 1024))

curl --fail --location --proto '=https' --tlsv1.2 \
    --output "${RUN_ROOT}/${BASE_NAME}" "${BASE_URL}"
curl --fail --location --proto '=https' --tlsv1.2 \
    --output "${RUN_ROOT}/SHA256SUMS" "${BASE_URL%/*}/SHA256SUMS"
expected="$(awk -v image="${BASE_NAME}" '$2 == image || $2 == "*" image {print $1}' "${RUN_ROOT}/SHA256SUMS")"
[[ "${expected}" =~ ^[0-9a-f]{64}$ ]] || { echo "official cloud-image digest missing" >&2; exit 2; }
printf '%s  %s\n' "${expected}" "${RUN_ROOT}/${BASE_NAME}" | sha256sum --check --strict

ssh-keygen -q -t ed25519 -N '' -f "${RUN_ROOT}/id_ed25519"
public_key="$(cat "${RUN_ROOT}/id_ed25519.pub")"
accel="tcg,thread=multi"
cpu="max"
if [[ -r /dev/kvm && -w /dev/kvm ]]; then accel="kvm"; cpu="host"; fi

create_node() {
    local node="$1" port="$2" api_port="$3" lower hostname
    lower="${node,,}"
    hostname="hoardarr-node-${lower}"
    qemu-img create -q -f qcow2 -F qcow2 -b "${RUN_ROOT}/${BASE_NAME}" "${RUN_ROOT}/${lower}-os.qcow2"
    qemu-img create -q -f raw "${RUN_ROOT}/${lower}-ssd1.raw" 768M
    qemu-img create -q -f raw "${RUN_ROOT}/${lower}-ssd2.raw" 768M
    cat >"${RUN_ROOT}/${lower}-user-data" <<EOF
#cloud-config
hostname: ${hostname}
manage_etc_hosts: true
users:
  - name: hoardarr
    groups: [adm, sudo]
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - ${public_key}
package_update: true
packages:
  - fio
  - mergerfs
  - multipath-tools
  - python3.12-venv
  - smartmontools
  - sysstat
runcmd:
  - [sh, -c, 'touch /run/hoardarr-two-node-disposable']
EOF
    cat >"${RUN_ROOT}/${lower}-meta-data" <<EOF
instance-id: hoardarr-${lower}-${GITHUB_RUN_ID}
local-hostname: ${hostname}
EOF
    cloud-localds "${RUN_ROOT}/${lower}-seed.img" \
        "${RUN_ROOT}/${lower}-user-data" "${RUN_ROOT}/${lower}-meta-data"
    qemu-system-x86_64 \
        -name "hoardarr-node-${node}" -machine q35 -accel "${accel}" -cpu "${cpu}" -m 2048 -smp 2 \
        -daemonize -pidfile "${RUN_ROOT}/${lower}.pid" \
        -D "${OUTPUT}/${lower}-qemu.log" -d guest_errors -no-reboot -monitor none \
        -serial "file:${OUTPUT}/${lower}-serial.log" -display none \
        -drive "file=${RUN_ROOT}/${lower}-os.qcow2,if=none,id=${lower}os,format=qcow2" \
        -device "virtio-blk-pci,drive=${lower}os,bootindex=1" \
        -drive "file=${RUN_ROOT}/${lower}-seed.img,if=none,id=${lower}seed,format=raw,readonly=on" \
        -device "virtio-blk-pci,drive=${lower}seed,bootindex=2" \
        -drive "file=${RUN_ROOT}/${lower}-ssd1.raw,if=none,id=${lower}local1,format=raw,cache=none" \
        -device "virtio-blk-pci,drive=${lower}local1,serial=HOARDARR_${node}_SSD1" \
        -drive "file=${RUN_ROOT}/${lower}-ssd2.raw,if=none,id=${lower}local2,format=raw,cache=none" \
        -device "virtio-blk-pci,drive=${lower}local2,serial=HOARDARR_${node}_SSD2" \
        -device "virtio-scsi-pci,id=${lower}scsi0" \
        -device "virtio-scsi-pci,id=${lower}scsi1" \
        -blockdev "driver=file,node-name=${lower}sharedfile0,filename=${RUN_ROOT}/shared.raw,locking=off" \
        -blockdev "driver=raw,node-name=${lower}shared0,file=${lower}sharedfile0" \
        -device "scsi-hd,drive=${lower}shared0,bus=${lower}scsi0.0,serial=HOARDARR_SHARED,wwn=0x6000000000000011,share-rw=on" \
        -blockdev "driver=file,node-name=${lower}sharedfile1,filename=${RUN_ROOT}/shared.raw,locking=off" \
        -blockdev "driver=raw,node-name=${lower}shared1,file=${lower}sharedfile1" \
        -device "scsi-hd,drive=${lower}shared1,bus=${lower}scsi1.0,serial=HOARDARR_SHARED,wwn=0x6000000000000011,share-rw=on" \
        -netdev "user,id=${lower}net,hostfwd=tcp:127.0.0.1:${port}-:22,hostfwd=tcp:127.0.0.1:${api_port}-:7877" \
        -device "virtio-net-pci,netdev=${lower}net"
    PIDS+=("$(cat "${RUN_ROOT}/${lower}.pid")")
}

qemu-img create -q -f raw "${RUN_ROOT}/shared.raw" 768M
create_node A 2222 8080
create_node B 2223 8081

SSH_OPTIONS=(-i "${RUN_ROOT}/id_ed25519" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5)
remote() { local port="$1"; shift; ssh "${SSH_OPTIONS[@]}" -p "${port}" hoardarr@127.0.0.1 "$@"; }
copy_to() { local port="$1" source="$2" target="$3"; scp "${SSH_OPTIONS[@]}" -P "${port}" "${source}" "hoardarr@127.0.0.1:${target}"; }

wait_node() {
    local port="$1" node="$2" attempt pid
    pid="$(cat "${RUN_ROOT}/${node}.pid")"
    for attempt in $(seq 1 120); do
        if ! kill -0 "${pid}" 2>/dev/null; then
            echo "QEMU node ${node} exited before SSH became ready" >&2
            tail -n 200 "${OUTPUT}/${node}-qemu.log" >&2 || true
            tail -n 200 "${OUTPUT}/${node}-serial.log" >&2 || true
            return 1
        fi
        if remote "${port}" 'cloud-init status --wait >/dev/null 2>&1'; then return 0; fi
        sleep 5
    done
    echo "QEMU node ${node} did not become SSH-ready" >&2
    tail -n 200 "${OUTPUT}/${node}-qemu.log" >&2 || true
    tail -n 200 "${OUTPUT}/${node}-serial.log" >&2 || true
    return 1
}
wait_node 2222 a
wait_node 2223 b

install_node() {
    local port="$1"
    copy_to "${port}" "${WHEEL}" "/tmp/${WHEEL_NAME}"
    copy_to "${port}" "${ROOT}/tests/integration/two_node_evidence.py" /tmp/two_node_evidence.py
    copy_to "${port}" "${ROOT}/tests/integration/two-node/prepare-node.sh" /tmp/prepare-node.sh
    for unit in hoardarr-migrate.service hoardarr-api.service hoardarr-worker.service; do
        copy_to "${port}" "${ROOT}/packaging/systemd/${unit}" "/tmp/${unit}"
    done
    remote "${port}" "set -Eeuo pipefail; sudo install -d -m 0755 /usr/lib/hoardarr /usr/local/libexec /etc/hoardarr; sudo install -d -m 0700 /var/lib/hoardarr; sudo python3 -m venv /usr/lib/hoardarr/venv; sudo /usr/lib/hoardarr/venv/bin/pip install --disable-pip-version-check /tmp/${WHEEL_NAME}; sudo install -m 0755 /tmp/two_node_evidence.py /usr/local/libexec/hoardarr-two-node-evidence; sudo install -m 0755 /tmp/prepare-node.sh /usr/local/libexec/hoardarr-prepare-two-node; sudo install -m 0644 /tmp/hoardarr-*.service /etc/systemd/system/"
    tar -C "${ROOT}/frontend/dist" -cf "${RUN_ROOT}/frontend.tar" .
    copy_to "${port}" "${RUN_ROOT}/frontend.tar" /tmp/hoardarr-frontend.tar
    remote "${port}" 'sudo install -d -m 0755 /usr/lib/hoardarr/current/frontend; sudo tar -C /usr/lib/hoardarr/current/frontend -xf /tmp/hoardarr-frontend.tar; sudo find /usr/lib/hoardarr/current/frontend -type d -exec chmod 0755 {} +; sudo find /usr/lib/hoardarr/current/frontend -type f -exec chmod 0644 {} +'
    remote "${port}" "sudo tee /etc/hoardarr/hoardarr.env >/dev/null <<'EOF'
HOARDARR_ENVIRONMENT=production
HOARDARR_DATABASE_URL=sqlite:////var/lib/hoardarr/hoardarr.db
HOARDARR_SECRET_KEY_FILE=/var/lib/hoardarr/secret.key
# The guest interface is reachable only through QEMU user networking. Host
# forwards remain bound to host 127.0.0.1 for isolated browser evidence.
HOARDARR_BIND_HOST=0.0.0.0
HOARDARR_BIND_PORT=7877
HOARDARR_SECURE_COOKIES=false
HOARDARR_TELEMETRY_FAST_INTERVAL_SECONDS=2
HOARDARR_TELEMETRY_DEVICE_INTERVAL_SECONDS=60
HOARDARR_TELEMETRY_HARDWARE_INTERVAL_SECONDS=300
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now hoardarr-migrate.service hoardarr-worker.service hoardarr-api.service
sudo systemctl is-active --quiet hoardarr-worker.service hoardarr-api.service
sudo /usr/lib/hoardarr/venv/bin/python /usr/local/libexec/hoardarr-two-node-evidence provision-ui --node isolated-node --password Hoardarr-Isolated-Validation-Only-11!"
}
install_node 2222
install_node 2223

remote 2222 "sudo /usr/local/libexec/hoardarr-prepare-two-node A ${SHARED_WWID}" | tee "${OUTPUT}/node-a-topology.txt"
remote 2223 "sudo /usr/local/libexec/hoardarr-prepare-two-node B ${SHARED_WWID}" | tee "${OUTPUT}/node-b-topology.txt"

declare -A topology=()
while IFS='=' read -r key value; do topology["A_${key}"]="${value}"; done <"${OUTPUT}/node-a-topology.txt"
while IFS='=' read -r key value; do topology["B_${key}"]="${value}"; done <"${OUTPUT}/node-b-topology.txt"
[[ "${topology[A_SHARED_UUID]}" == "${topology[B_SHARED_UUID]}" ]] || {
    echo "the nodes do not report the same shared filesystem UUID" >&2
    exit 2
}

write_sectors() {
    local port="$1" device="$2"
    remote "${port}" "cat /sys/class/block/\$(basename \$(readlink -f ${device}))/stat | awk '{print \$7 * 512}'"
}

# Capture Linux block-layer counters before any test dataset is written. These
# counters include filesystem metadata and therefore remain separate from the
# planned payload budget.
for node in A B; do
    port=2222; [[ "${node}" == B ]] && port=2223
    for member in ONE TWO; do
        device="${topology[${node}_LOCAL_${member}]}"
        write_sectors "${port}" "${device}" >"${OUTPUT}/${node,,}-${member,,}-writes-before.txt"
    done
    write_sectors "${port}" /dev/mapper/hoardarr-shared >"${OUTPUT}/${node,,}-shared-writes-before.txt"
done

for node in A B; do
    port=2222; [[ "${node}" == B ]] && port=2223
    for member in 1 2; do
        remote "${port}" "sudo install -d /srv/hoardarr/${node,,}/member-${member}/dataset; sudo dd if=/dev/zero of=/srv/hoardarr/${node,,}/member-${member}/dataset/large.bin bs=1M count=8 conv=fsync status=none; for i in \$(seq 1 256); do printf '%08d\\n' \"\$i\" | sudo tee /srv/hoardarr/${node,,}/member-${member}/dataset/small-\$i >/dev/null; done"
    done
done
remote 2222 'sudo dd if=/dev/urandom of=/srv/hoardarr/shared/media-dataset.bin bs=1M count=32 conv=fsync status=none; sudo sha256sum /srv/hoardarr/shared/media-dataset.bin' | tee "${OUTPUT}/shared-hash-before.txt"

phase_file="${OUTPUT}/workload-phases.jsonl"
phase() { printf '{"phase":"%s","timestamp":"%s"}\n' "$1" "$(date --utc +%FT%TZ)" | tee -a "${phase_file}"; }

phase idle
sleep 8
remote 2222 'sudo /usr/lib/hoardarr/venv/bin/python /usr/local/libexec/hoardarr-two-node-evidence collect'
phase sequential_read
remote 2222 'sudo fio --name=sequential-read --filename=/srv/hoardarr/shared/media-dataset.bin --rw=read --bs=1M --direct=1 --time_based=1 --runtime=12 --iodepth=4 --output-format=json' >"${OUTPUT}/fio-sequential-read.json"
phase random_read
remote 2222 "sudo fio --name=random-read --filename=/srv/hoardarr/shared/media-dataset.bin --rw=randread --bs=4k --direct=1 --time_based=1 --runtime=12 --iodepth=16 --numjobs=${WORKLOAD_CONCURRENCY} --output-format=json" >"${OUTPUT}/fio-random-read.json"
phase limited_write
remote 2222 'sudo fio --name=limited-write --filename=/srv/hoardarr/a/local/limited-write.bin --rw=write --bs=1M --size=4M --io_size=4M --direct=0 --fsync=1 --output-format=json' >"${OUTPUT}/fio-limited-write.json"
phase mixed_read_metadata
remote 2222 'sudo fio --name=mixed-read-shape --filename=/srv/hoardarr/shared/media-dataset.bin --rw=read --bsrange=4k-1M --direct=1 --time_based=1 --runtime=10 --iodepth=8 --output-format=json' >"${OUTPUT}/fio-mixed-read.json"

first_path="${topology[A_SHARED_PATH_ONE]}"
phase path_a_failover_start
remote 2222 'sudo fio --name=failover-read --filename=/srv/hoardarr/shared/media-dataset.bin --rw=read --bs=128k --direct=1 --time_based=1 --runtime=20 --iodepth=8 --output=/tmp/failover-fio.json --output-format=json >/dev/null 2>&1 & echo $!' >"${OUTPUT}/failover-fio.pid"
sleep 4
remote 2222 "sudo multipathd fail path \$(basename ${first_path})"
remote 2222 'sudo /usr/lib/hoardarr/venv/bin/python /usr/local/libexec/hoardarr-two-node-evidence collect'
phase path_a_failed
sleep 6
remote 2222 "sudo multipathd reinstate path \$(basename ${first_path})"
remote 2222 'sudo /usr/lib/hoardarr/venv/bin/python /usr/local/libexec/hoardarr-two-node-evidence collect'
phase path_a_recovered
sleep 12
remote 2222 'sudo cat /tmp/failover-fio.json' >"${OUTPUT}/fio-path-failover.json"

phase api_disconnected_workload
remote 2222 'sudo systemctl stop hoardarr-api.service; sudo systemctl is-active --quiet hoardarr-worker.service'
remote 2222 'sudo fio --name=api-down-read --filename=/srv/hoardarr/shared/media-dataset.bin --rw=randread --bs=64k --direct=1 --time_based=1 --runtime=10 --iodepth=8 --output-format=json' >"${OUTPUT}/fio-api-down.json"
remote 2222 'sudo systemctl start hoardarr-api.service; sudo systemctl is-active --quiet hoardarr-api.service'
phase api_reconnected

phase node_handoff_start
remote 2222 "sudo /usr/lib/hoardarr/venv/bin/python /usr/local/libexec/hoardarr-two-node-evidence event --node 'Node A' --peer-node 'Node B' --wwid ${SHARED_WWID} --event-type node_storage_unavailable --previous-state serving --resulting-state standby; sudo umount /srv/hoardarr/shared"
remote 2223 "sudo blockdev --flushbufs /dev/mapper/hoardarr-shared; sudo install -d -m 0750 /srv/hoardarr/shared; sudo mount -o noatime /dev/mapper/hoardarr-shared /srv/hoardarr/shared; sudo /usr/lib/hoardarr/venv/bin/python /usr/local/libexec/hoardarr-two-node-evidence event --node 'Node B' --peer-node 'Node A' --wwid ${SHARED_WWID} --event-type storage_transitioned --previous-state standby --resulting-state serving"
phase node_b_serving
remote 2223 'sudo sha256sum /srv/hoardarr/shared/media-dataset.bin' | tee "${OUTPUT}/shared-hash-after-handoff.txt"
remote 2223 "sudo fio --name=post-handoff-read --filename=/srv/hoardarr/shared/media-dataset.bin --rw=read --bs=256k --direct=1 --time_based=1 --runtime=${READ_SOAK_SECONDS} --iodepth=8 --output-format=json" >"${OUTPUT}/fio-post-handoff-read.json"
phase node_b_worker_restart
remote 2223 'sudo systemctl restart hoardarr-worker.service; sudo systemctl is-active --quiet hoardarr-worker.service'
sleep 8
remote 2223 "sudo /usr/lib/hoardarr/venv/bin/python /usr/local/libexec/hoardarr-two-node-evidence event --node 'Node B' --peer-node 'Node A' --wwid ${SHARED_WWID} --event-type node_recovered --previous-state restarting --resulting-state serving; sudo /usr/lib/hoardarr/venv/bin/python /usr/local/libexec/hoardarr-two-node-evidence collect"
phase final_idle
sleep 8

for node in A B; do
    port=2222; [[ "${node}" == B ]] && port=2223
    for member in ONE TWO; do
        device="${topology[${node}_LOCAL_${member}]}"
        write_sectors "${port}" "${device}" >"${OUTPUT}/${node,,}-${member,,}-writes-after.txt"
    done
    write_sectors "${port}" /dev/mapper/hoardarr-shared >"${OUTPUT}/${node,,}-shared-writes-after.txt"
    remote "${port}" "sudo /usr/lib/hoardarr/venv/bin/python /usr/local/libexec/hoardarr-two-node-evidence export --node 'Node ${node}' --output /tmp/node-${node,,}-evidence.json; sudo cat /proc/\$(systemctl show --property=MainPID --value hoardarr-worker)/status | grep -E '^(VmRSS|VmHWM|Threads):'" >"${OUTPUT}/node-${node,,}-worker-memory.txt"
    remote "${port}" "sudo cat /tmp/node-${node,,}-evidence.json" >"${OUTPUT}/node-${node,,}-evidence.json"
    remote "${port}" 'sudo multipath -ll' >"${OUTPUT}/node-${node,,}-multipath.txt"
    remote "${port}" 'sudo iostat -x -o JSON 1 2' >"${OUTPUT}/node-${node,,}-iostat-final.json"
done

# No browser or API metrics client was present for the workload above. Reconnect
# only after export so the UI must reconstruct history from each node's database.
node "${ROOT}/tests/integration/two-node/capture-two-node-ui.mjs" \
    http://127.0.0.1:8080 "${OUTPUT}" node-a
node "${ROOT}/tests/integration/two-node/capture-two-node-ui.mjs" \
    http://127.0.0.1:8081 "${OUTPUT}" node-b

diff -u "${OUTPUT}/shared-hash-before.txt" "${OUTPUT}/shared-hash-after-handoff.txt"

python3 - "${OUTPUT}" "${MAX_TOTAL_WRITES}" "${MAX_DEVICE_WRITES}" "${planned_total}" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
node_a = json.loads((root / "node-a-evidence.json").read_text())
node_b = json.loads((root / "node-b-evidence.json").read_text())
phases = [json.loads(line) for line in (root / "workload-phases.jsonl").read_text().splitlines()]
metrics = {
    sample["metric_id"]
    for node in (node_a, node_b)
    for sample in node["telemetry"]
}
required = {
    "cpu.utilization",
    "memory.utilization",
    "io.read.bytes_per_second",
    "io.write.bytes_per_second",
    "io.read.iops",
    "io.write.iops",
    "io.read.latency",
    "io.write.latency",
    "io.utilization",
    "io.queue.depth",
    "io.write.today",
    "storage.path.state",
    "storage.paths.healthy",
    "storage.paths.failed",
}
missing = sorted(required - metrics)
if missing:
    raise SystemExit(f"required telemetry missing: {missing}")
events = [event["event_type"] for node in (node_a, node_b) for event in node["events"]]
for expected in ("path_failed", "path_recovered", "storage_transitioned"):
    if expected not in events:
        raise SystemExit(f"required event missing: {expected}")

actual_writes = {}
for node in "ab":
    for member in ("one", "two"):
        before = int((root / f"{node}-{member}-writes-before.txt").read_text().strip())
        after = int((root / f"{node}-{member}-writes-after.txt").read_text().strip())
        actual_writes[f"Node {node.upper()} SSD {1 if member == 'one' else 2}"] = max(0, after - before)
    before = int((root / f"{node}-shared-writes-before.txt").read_text().strip())
    after = int((root / f"{node}-shared-writes-after.txt").read_text().strip())
    actual_writes[f"Node {node.upper()} shared LUN path stack"] = max(0, after - before)

summary = {
    "environment": "two Ubuntu 24.04 QEMU VMs with systemd",
    "device_type": "test-created virtual block devices",
    "node_a": node_a,
    "node_b": node_b,
    "phases": phases,
    "write_budget": {
        "maximum_total_bytes": int(sys.argv[2]),
        "maximum_per_device_bytes": int(sys.argv[3]),
        "planned_payload_bytes": int(sys.argv[4]),
        "observed_local_os_write_bytes": actual_writes,
    },
    "browser_connected_during_workload": False,
    "data_integrity": "shared SHA-256 unchanged across controlled ownership handoff",
    "physical_ssd_validation": "pending; no physical device was mutated",
}
(root / "two-node-storage-graph-stress.json").write_text(json.dumps(summary, indent=2))
PY

jq -e '.browser_connected_during_workload == false' "${OUTPUT}/two-node-storage-graph-stress.json" >/dev/null
jq -e '.physical_ssd_validation | startswith("pending")' "${OUTPUT}/two-node-storage-graph-stress.json" >/dev/null
echo "two-node virtual storage and persistent telemetry validation completed"
