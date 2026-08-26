#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 1 ]] || {
    echo "usage: $0 PRODUCTION_INSTALL_FRAGMENT" >&2
    exit 2
}
production_fragment="$(realpath -- "$1")"
[[ -f "$production_fragment" && ! -L "$production_fragment" ]] || {
    echo "production install fragment must be a regular file" >&2
    exit 2
}
for required in apt-get dpkg-deb dpkg-query gpg gzip sha256sum; do
    command -v "$required" >/dev/null || {
        echo "missing integration dependency: $required" >&2
        exit 2
    }
done
[[ "$(id -u)" -eq 0 ]] || {
    echo "local-file APT integration must run as root in a disposable runner" >&2
    exit 2
}

work="$(mktemp -d)"
package=hoardarr-local-apt-regression
version=1.0
installed_path="/usr/share/$package/installed"
cleanup() {
    if dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null | grep -qx installed; then
        dpkg --purge "$package" >/dev/null 2>&1 || true
    fi
    rm -rf -- "/usr/share/$package" "$work"
}
trap cleanup EXIT
if dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null | grep -qx installed; then
    echo "disposable integration package is already installed" >&2
    exit 1
fi

package_root="$work/package"
repo="$work/repository"
key_home="$work/gnupg"
keyring="$work/hoardarr-test-keyring.gpg"
source_list="$work/hoardarr-test.list"
lists="$work/lists"
archives="$work/cache/archives"
mkdir -p \
    "$package_root/DEBIAN" \
    "$package_root/usr/share/$package" \
    "$repo/pool/main/h/$package" \
    "$repo/dists/noble/main/binary-amd64" \
    "$key_home" "$lists/partial" "$archives/partial"
chmod 0700 "$key_home"
printf '%s\n' installed-from-signed-local-file-repository \
    >"$package_root$installed_path"
cat >"$package_root/DEBIAN/control" <<EOF
Package: $package
Version: $version
Architecture: all
Maintainer: Hoardarr CI <ci@invalid.hoardarr.local>
Description: disposable signed local-file APT integration fixture
EOF
deb="$repo/pool/main/h/$package/${package}_${version}_all.deb"
dpkg-deb --build --root-owner-group "$package_root" "$deb" >/dev/null
deb_size="$(stat -c %s -- "$deb")"
deb_sha256="$(sha256sum -- "$deb" | awk '{print $1}')"
cat >"$repo/dists/noble/main/binary-amd64/Packages" <<EOF
Package: $package
Version: $version
Architecture: all
Maintainer: Hoardarr CI <ci@invalid.hoardarr.local>
Filename: pool/main/h/$package/${package}_${version}_all.deb
Size: $deb_size
SHA256: $deb_sha256
Description: disposable signed local-file APT integration fixture

EOF
gzip -n -c "$repo/dists/noble/main/binary-amd64/Packages" \
    >"$repo/dists/noble/main/binary-amd64/Packages.gz"

cat >"$work/signing-key" <<'EOF'
Key-Type: RSA
Key-Length: 2048
Name-Real: Hoardarr local APT regression
Name-Email: local-apt-regression@invalid.hoardarr.local
Expire-Date: 1d
%no-protection
%commit
EOF
gpg --batch --homedir "$key_home" --generate-key "$work/signing-key" >/dev/null 2>&1
fingerprint="$(gpg --batch --homedir "$key_home" --with-colons --list-secret-keys |
    awk -F: '$1 == "fpr" { print $10; exit }')"
[[ "$fingerprint" =~ ^[0-9A-F]{40}$ ]]
gpg --batch --homedir "$key_home" --export "$fingerprint" >"$keyring"
[[ -s "$keyring" ]]
chmod 0755 "$work" "$repo" "$repo/pool" "$repo/pool/main" \
    "$repo/pool/main/h" "$repo/pool/main/h/$package" \
    "$repo/dists" "$repo/dists/noble" "$repo/dists/noble/main" \
    "$repo/dists/noble/main/binary-amd64" "$lists" "$archives"
chmod 0644 "$keyring" "$deb" "$repo/dists/noble/main/binary-amd64/Packages" \
    "$repo/dists/noble/main/binary-amd64/Packages.gz"
if getent passwd _apt >/dev/null; then
    chown _apt:root "$lists/partial" "$archives/partial"
fi

packages_rel=main/binary-amd64/Packages
packages_gz_rel=main/binary-amd64/Packages.gz
packages_path="$repo/dists/noble/$packages_rel"
packages_gz_path="$repo/dists/noble/$packages_gz_rel"
cat >"$repo/dists/noble/Release" <<EOF
Origin: Hoardarr local APT regression
Label: Hoardarr local APT regression
Suite: noble
Codename: noble
Date: $(LC_ALL=C date -Ru)
Architectures: amd64
Components: main
Description: disposable signed local-file APT integration repository
SHA256:
 $(sha256sum -- "$packages_path" | awk '{print $1}') $(stat -c %s -- "$packages_path") $packages_rel
 $(sha256sum -- "$packages_gz_path" | awk '{print $1}') $(stat -c %s -- "$packages_gz_path") $packages_gz_rel
EOF
gpg --batch --homedir "$key_home" --local-user "$fingerprint" \
    --clearsign --output "$repo/dists/noble/InRelease" "$repo/dists/noble/Release"
printf 'deb [signed-by=%s] file:%s noble main\n' "$keyring" "$repo" >"$source_list"

apt_options=(
    -o "Dir::Etc::sourcelist=$source_list"
    -o "Dir::Etc::sourceparts=-"
    -o "Dir::State::lists=$lists"
    -o "Dir::Cache::archives=$archives"
    -o "Acquire::Languages=none"
    -o "Acquire::Retries=0"
    -o "Acquire::http::Proxy=false"
    -o "Acquire::https::Proxy=false"
)
target=/
exact_roots=("$package=$version")
chroot() {
    [[ "$1" == / ]]
    shift
    command "$@"
}

apt-get "${apt_options[@]}" update >"$work/update.log" 2>&1
grep -Fq "file:$repo" "$work/update.log"
! grep -Eq 'https?://' "$source_list" "$work/update.log"
[[ -z "$(find "$archives" -maxdepth 1 -type f -name '*.deb' -print -quit)" ]]

old_status=0
chroot "$target" apt-get "${apt_options[@]}" \
    --yes --no-download --no-install-recommends install "${exact_roots[@]}" \
    >"$work/old-no-download.log" 2>&1 || old_status=$?
[[ "$old_status" -ne 0 ]]
grep -Fq "Pathname to install is not absolute" "$work/old-no-download.log"
old_error="$(awk '
    /Pathname to install is not absolute/ { first = $0; count++ }
    END { if (count != 1) exit 1; print first }
' "$work/old-no-download.log")"
! dpkg-query -W -f='${db:Status-Status}\t${Version}\n' "$package" >/dev/null 2>&1

# This is the exact actual-install command extracted from the production payload.
# It must acquire through the only configured, signed file: source.
source "$production_fragment" >"$work/corrected-install.log" 2>&1
grep -Fq "file:$repo" "$work/corrected-install.log"
readback="$(dpkg-query -W -f='${db:Status-Status}\t${Version}\t${Architecture}\n' "$package")"
[[ "$readback" == $'installed\t1.0\tall' ]]
[[ "$(cat "$installed_path")" == installed-from-signed-local-file-repository ]]

printf 'old_no_download_status=%s\n' "$old_status"
printf 'old_no_download_error=%s\n' "$old_error"
printf 'signed_by=%s\n' "$keyring"
printf 'fingerprint=%s\n' "$fingerprint"
printf 'source=file:%s\n' "$repo"
printf 'archive_cache_was_empty=true\n'
printf 'actual_install_file_acquisition=true\n'
printf 'network_sources=0\n'
printf 'package_readback=%s\n' "$readback"
