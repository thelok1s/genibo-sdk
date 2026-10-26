#!/bin/sh
# Reproduce the CI Linux build inside a plain Ubuntu container (local testing).
#   docker run --rm [-e PACKAGE_VERSION=0.1.0~dev1] -v "$PWD:/src:ro" -v "$PWD/dist/release:/out" ubuntu:24.04 \
#       sh /src/packaging/linux/build-in-container.sh
set -eu
PYINSTALLER_VERSION="${PYINSTALLER_VERSION:-6.22.3}"
sh /src/packaging/linux/install-build-deps.sh
for attempt in 1 2 3 4 5; do  # the installer's own downloads do not retry
  if curl -LsSf --retry 5 --retry-all-errors https://astral.sh/uv/install.sh \
      | env UV_INSTALL_DIR=/usr/local/bin sh >/dev/null; then break; fi
  echo "uv install attempt $attempt failed; retrying" >&2
  sleep 5
done
command -v uv >/dev/null
mkdir -p /work && cd /src && tar --exclude=./.venv --exclude=./build --exclude=./dist -cf - . | tar -xf - -C /work
cd /work
uv run --locked --python 3.13 --extra studio --with "pyinstaller==${PYINSTALLER_VERSION}" \
  python packaging/build.py ${PACKAGE_VERSION:+--version "$PACKAGE_VERSION"} --self-test
cp dist/release/* /out/
