#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "usage: $0 --wheel /absolute/path/to/hoardarr.whl" >&2
}

wheel=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --wheel)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            wheel="$2"
            shift 2
            ;;
        *)
            usage
            exit 2
            ;;
    esac
done

[[ $EUID -eq 0 ]] || { echo "fleet receiver installation must run as root" >&2; exit 1; }
[[ $wheel = /* && -f $wheel ]] || {
    echo "--wheel must identify an existing absolute wheel path" >&2
    exit 1
}
[[ -f /etc/hoardarr/fleet-ingestion.env ]] || {
    echo "create /etc/hoardarr/fleet-ingestion.env from the example before installing" >&2
    exit 1
}
grep -Eq '^HOARDARR_FLEET_DATABASE_URL=postgresql\+psycopg://' \
    /etc/hoardarr/fleet-ingestion.env || {
        echo "central fleet deployment requires a PostgreSQL psycopg database URL" >&2
        exit 1
    }
if grep -q 'change-me' /etc/hoardarr/fleet-ingestion.env; then
    echo "replace every change-me placeholder before installing" >&2
    exit 1
fi

getent group hoardarr-fleet >/dev/null || groupadd --system hoardarr-fleet
id -u hoardarr-fleet >/dev/null 2>&1 || useradd --system --gid hoardarr-fleet \
    --home-dir /var/lib/hoardarr-fleet --shell /usr/sbin/nologin hoardarr-fleet
install -d -o root -g root -m 0755 /usr/lib/hoardarr-fleet
install -d -o root -g root -m 0755 /usr/lib/hoardarr-fleet/releases
install -d -o hoardarr-fleet -g hoardarr-fleet -m 0700 /var/lib/hoardarr-fleet
chown root:hoardarr-fleet /etc/hoardarr/fleet-ingestion.env
chmod 0640 /etc/hoardarr/fleet-ingestion.env

release_id="$(sha256sum -- "$wheel" | awk '{print substr($1, 1, 16)}')"
release="/usr/lib/hoardarr-fleet/releases/$release_id"
if [[ ! -f "$release/.ready" ]]; then
    # The venv must be created at its final path because its console-script
    # shebangs contain absolute paths.  An interrupted, unmarked release is
    # safe to replace before it has ever become active.
    rm -rf -- "$release"
    python3.12 -m venv "$release"
    "$release/bin/python" -m pip install --disable-pip-version-check \
        "${wheel}[fleet-central]"
    "$release/bin/hoardarr-fleet-ingestion" --help >/dev/null
    touch "$release/.ready"
fi

previous=""
if [[ -L /usr/lib/hoardarr-fleet/venv ]]; then
    previous="$(readlink -f /usr/lib/hoardarr-fleet/venv)"
elif [[ -e /usr/lib/hoardarr-fleet/venv ]]; then
    mv /usr/lib/hoardarr-fleet/venv \
        "/usr/lib/hoardarr-fleet/venv.legacy.$(date -u +%Y%m%dT%H%M%SZ)"
fi
rm -f -- /usr/lib/hoardarr-fleet/.venv.next
ln -s "$release" /usr/lib/hoardarr-fleet/.venv.next
mv -Tf /usr/lib/hoardarr-fleet/.venv.next /usr/lib/hoardarr-fleet/venv

install -o root -g root -m 0644 \
    "$(dirname "$0")/systemd/hoardarr-fleet-ingestion.service" \
    /etc/systemd/system/hoardarr-fleet-ingestion.service
systemctl daemon-reload
systemctl enable hoardarr-fleet-ingestion.service
if ! systemctl restart hoardarr-fleet-ingestion.service; then
    if [[ -n "$previous" && -f "$previous/.ready" ]]; then
        rm -f -- /usr/lib/hoardarr-fleet/.venv.rollback
        ln -s "$previous" /usr/lib/hoardarr-fleet/.venv.rollback
        mv -Tf /usr/lib/hoardarr-fleet/.venv.rollback /usr/lib/hoardarr-fleet/venv
        systemctl restart hoardarr-fleet-ingestion.service || true
    fi
    echo "fleet receiver failed to start; the previous release was restored when available" >&2
    exit 1
fi
systemctl is-active --quiet hoardarr-fleet-ingestion.service
if [[ -n "$previous" && "$previous" != "$release" ]]; then
    ln -sfn "$previous" /usr/lib/hoardarr-fleet/venv.previous
fi
