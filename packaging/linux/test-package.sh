#!/bin/sh
# Install a Genibo Studio .deb or .rpm in a CLEAN container and prove it works.
#
#   docker run --rm -v "$PWD/dist/release:/pkgs:ro" -v "$PWD/packaging:/packaging:ro" ubuntu:24.04 \
#       sh /packaging/linux/test-package.sh /pkgs/genibo-studio_0.1.0_arm64.deb
#
# If the distribution's glibc is older than the package requires, the test instead checks that
# the package manager REFUSES the install (the declared libc dependency is the guard).
set -eu
pkg="$1"
# shellcheck disable=SC1091
. /etc/os-release
distro_glibc="$(ldd --version 2>&1 | head -n 1 | awk '{print $NF}')"
echo "== ${PRETTY_NAME} · $(uname -m) · glibc ${distro_glibc} · $(basename "$pkg")"

case "$pkg" in
  *.deb)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    need="$(dpkg-deb -f "$pkg" Depends | tr ',' '\n' | sed -n 's/.*libc6 (>= *\([0-9.]*\)).*/\1/p' | head -n 1)"
    install() { apt-get install -y -qq "$pkg" python3 >/dev/null; }
    ;;
  *.rpm)
    need="$(rpm -qp --requires "$pkg" 2>/dev/null | sed -n 's/^glibc >= *\([0-9.]*\).*/\1/p' | head -n 1)"
    install() { dnf install -y -q "$pkg" python3 >/dev/null; }
    ;;
  *) echo "unknown package type: $pkg" >&2; exit 2 ;;
esac
echo "package requires glibc >= ${need:-?}"

if [ -n "$need" ] && [ "$(printf '%s\n%s\n' "$need" "$distro_glibc" | sort -V | head -n 1)" != "$need" ]; then
  if install 2>/dev/null; then
    echo "ERROR: installed although glibc ${distro_glibc} < ${need}" >&2
    exit 1
  fi
  echo "== REFUSED as expected (glibc ${distro_glibc} < ${need}); this distribution is not supported"
  exit 0
fi

install
python3 "$(dirname "$0")/../check_linux_deps.py" /opt/genibo-studio
test -f /usr/share/applications/genibo-studio.desktop
for size in 16 24 32 48 64 128 256 512; do
  test -s "/usr/share/icons/hicolor/${size}x${size}/apps/genibo-studio.png"
done
genibo --version
LANG=C.UTF-8 QT_QPA_PLATFORM=offscreen genibo-studio --self-test
echo "== PACKAGE OK"
