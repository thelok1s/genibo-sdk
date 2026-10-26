# Packaging and release artifacts

Genibo Studio is frozen with PyInstaller (one-folder bundles) and packaged per
platform. CI: `.github/workflows/build-artifacts.yml` at the repository root.

| Target | Runner | Artifacts | Minimum OS |
|---|---|---|---|
| Windows x64 | `windows-2025` | `Genibo-Studio-<v>-windows-x64-setup.exe` (Inno Setup), `.zip` | Windows 10/11 x64 |
| macOS Apple silicon | `macos-26` | `Genibo-Studio-<v>-macos-arm64.dmg` | macOS 14 |
| macOS Intel | `macos-15-intel` | `Genibo-Studio-<v>-macos-x86_64.dmg` | macOS 14 |
| Linux x86_64 | `ubuntu-22.04` | `genibo-studio_<v>_amd64.deb`, `genibo-studio-<v>-1.x86_64.rpm`, `.tar.gz` | glibc floor measured at build time (built on glibc 2.35) |
| Linux arm64 | `ubuntu-24.04-arm` | `genibo-studio_<v>_arm64.deb`, `genibo-studio-<v>-1.aarch64.rpm`, `.tar.gz` | glibc ≥ 2.38: Ubuntu 24.04+, Debian 13+, Fedora 39+, RHEL/Alma 10 |
| Python | `ubuntu-24.04` | wheel + sdist | Python 3.11+ |

The OS floors come from the PySide6 6.12 wheels: macOS 14 universal2, glibc
2.34 on Linux x86_64 and 2.39 on Linux aarch64. The x86_64 Linux build runs on
Ubuntu 22.04 for the widest reach.

## Icons

The designer sources live in `packaging/icons/` (see its README).

* Windows: `genibo.ico` goes into the exe and the installer.
* macOS:
  * 14/15 get `genibo.icns`, laid out on Apple's icon grid;
  * 26+ get the Liquid Glass icon, compiled from `genibo.icon` by `actool`
    (Xcode 26+) into `Assets.car`.
* Linux: a hicolor PNG set from 16 to 512 px.
* The running app: a 512 px window icon.

`verify-dmg.sh` and `test-package.sh` check that every one of these is present.

## What the packages contain

* Windows: `%ProgramFiles%\Genibo Studio` (or per-user), a Start-menu entry, an
  optional desktop icon, and an uninstaller.
* macOS: `Genibo Studio.app` (bundle id `org.genibo.studio`), with an
  `Applications` link in the DMG.
* Linux: `/opt/genibo-studio/`, `/usr/bin/genibo-studio`, and `/usr/bin/genibo`
  (the CLI: the binary dispatches on its name), plus a desktop entry and
  hicolor icons from 16 to 512 px. Dependencies on the Qt platform-plugin libraries (xcb, wayland,
  EGL, fontconfig) are declared per format, and `libc6 (>= X)` / `glibc >= X`
  uses the glibc floor **measured** from the bundled binaries
  (`objdump -T`, see `packaging/build.py`).
* The GTK3 platform-theme plugin is left out so packages do not pull in GTK;
  the studio uses Qt's Fusion style.

## Verification in CI

1. **Tests**: ruff, pyright, the full pytest suite including regenerating the
   golden data from the original C# (`setup-dotnet` 6.0) and offscreen studio tests.
2. **Every frozen app** runs `genibo-studio --self-test` headless
   (`QT_QPA_PLATFORM=offscreen`). It creates a project, builds it, simulates
   it, connects the simulator and takes a screenshot. Log and screenshot are
   uploaded with the artifacts. This catches missing hidden imports and Qt plugins.
3. **As shipped**:
   * the DMG is mounted, its signature verified and the app inside self-tested;
   * the Windows installer is run silently per user, the installed app is
     self-tested, then it is uninstalled;
   * each `.deb`/`.rpm` is installed into a clean container (Ubuntu 22.04/24.04,
     Debian 12/13, Fedora 42, AlmaLinux 9/10). The test then:
     * resolves the declared dependencies with apt/dnf;
     * runs `ldd` on every bundled ELF file (`packaging/check_linux_deps.py`);
     * checks the desktop entry and icon;
     * runs the self-test.

     On a distribution older than the glibc floor, the test instead asserts
     that the package manager **refuses** the install.

## Versions

* Tag `v<version>` matching `[project].version` → release build. A mismatch fails the run.
* Other pushes: `<version>.dev<run_number>`. deb/rpm get `~devN`, so they sort
  before the final release. PEP 440 pre-releases (`0.2.0rc1`) become `0.2.0~rc1`.
* `workflow_dispatch` accepts a version override.

On tags, the `release` job attaches every artifact and `SHA256SUMS.txt` to a
GitHub release (pre-release for `~` versions).

## Building locally

```bash
cd genibo-sdk
# current OS (macOS: .dmg, Windows: .exe + .zip, Linux: .deb/.rpm/.tar.gz; Linux needs nfpm on PATH)
uv run --extra studio --with pyinstaller==6.22.3 python packaging/build.py --self-test
bash packaging/macos/verify-dmg.sh dist/release/*.dmg

# Linux packages from any Docker host (native architecture of the Docker VM)
docker run --rm -v "$PWD:/src:ro" -v "$PWD/dist/release:/out" ubuntu:24.04 \
    sh /src/packaging/linux/build-in-container.sh
docker run --rm -v "$PWD/dist/release:/pkgs:ro" -v "$PWD/packaging:/packaging:ro" fedora:42 \
    sh /packaging/linux/test-package.sh /pkgs/genibo-studio-0.1.0-1.aarch64.rpm
```

Platform icons are derived from the designer sources by `packaging/make_icons.py` and committed.

## Signing (not configured)

Builds are **unsigned**. macOS apps carry only an ad-hoc signature, so
Gatekeeper asks users to approve the first launch (System Settings → Privacy &
Security → Open Anyway). Windows SmartScreen warns about the unsigned installer.
To sign:

* macOS: build with a Developer ID identity (`codesign --options runtime
  --timestamp --sign "Developer ID Application: …"`; PyInstaller needs the
  `com.apple.security.cs.allow-unsigned-executable-memory` and
  `disable-library-validation` entitlements), then `xcrun notarytool submit
  --wait` and `xcrun stapler staple` on the DMG. Store the certificate and API
  key as repository secrets.
* Windows: `signtool sign /fd sha256 /tr <timestamp-url> /td sha256` on
  `genibo-studio.exe` before Inno Setup, and on the setup `.exe` after it
  (Inno's `SignTool=` directive can do both).

## Status of verification (at the time of writing)

| Target | Status |
|---|---|
| macOS arm64 | Built locally; frozen app and DMG self-tested |
| Linux arm64 | Built in an Ubuntu 24.04 container. Installs and passes on Ubuntu 24.04, Debian 13, Fedora 42 and AlmaLinux 10; correctly refused on Ubuntu 22.04 and AlmaLinux 9. `~dev` versions verified |
| Windows x64, macOS x86_64, Linux x86_64 | **Not run yet.** No such host was available. Docker's QEMU emulation crashed on `uv`. The first CI run is the first real check |
| The workflow itself | Validated with actionlint 1.7.12 and shellcheck 0.11.0; not yet executed on GitHub |
