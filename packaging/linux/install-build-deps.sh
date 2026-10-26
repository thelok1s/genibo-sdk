#!/bin/sh
# Install what a Linux build host needs: Qt runtime libraries (for the headless self-test and so
# PyInstaller can resolve plugin dependencies), binutils (objdump for the glibc scan) and nfpm.
# Run as root (CI: sudo). NFPM_VERSION may be overridden.
set -eu
NFPM_VERSION="${NFPM_VERSION:-2.48.0}"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends curl ca-certificates binutils tar \
  libfontconfig1 libfreetype6 libdbus-1-3 libegl1 libgl1 libx11-6 libx11-xcb1 libxkbcommon0 libxkbcommon-x11-0 \
  libxcb1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \
  libxcb-render0 libxcb-shape0 libxcb-shm0 libxcb-sync1 libxcb-xfixes0 libxcb-xkb1 libxrender1 \
  libsm6 libice6 libwayland-client0 libwayland-cursor0 libwayland-egl1 libglib2.0-0 >/dev/null
case "$(uname -m)" in x86_64) a=x86_64 ;; aarch64) a=arm64 ;; *) echo "unsupported arch $(uname -m)" >&2; exit 2 ;; esac
# Download with retries, verify against the release's checksums.txt, then extract.
base="https://github.com/goreleaser/nfpm/releases/download/v${NFPM_VERSION}"
file="nfpm_${NFPM_VERSION}_Linux_${a}.tar.gz"
tmp="$(mktemp -d)"
curl -fsSL --retry 5 --retry-all-errors --retry-delay 3 -o "$tmp/$file" "$base/$file"
curl -fsSL --retry 5 --retry-all-errors --retry-delay 3 -o "$tmp/checksums.txt" "$base/checksums.txt"
(cd "$tmp" && grep " ${file}\$" checksums.txt | sha256sum -c -)
tar -xzf "$tmp/$file" -C /usr/local/bin nfpm
rm -rf "$tmp"
nfpm --version | head -n 3
