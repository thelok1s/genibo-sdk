"""Build Genibo Studio release artifacts for the current platform.

    uv run --extra studio --with pyinstaller python packaging/build.py [--version V] [--self-test]

Outputs in dist/release/:

* Windows: ``Genibo-Studio-<v>-windows-x64.zip`` and ``...-setup.exe`` (Inno Setup)
* macOS:   ``Genibo-Studio-<v>-macos-<arm64|x86_64>.dmg`` (ad-hoc signed, not notarized)
* Linux:   ``genibo-studio_<v>_<amd64|arm64>.deb``, ``genibo-studio-<v>-1.<x86_64|aarch64>.rpm``,
           ``Genibo-Studio-<v>-linux-<arch>.tar.gz`` (needs nfpm on PATH)

Every artifact gets a ``.sha256`` file next to it. The script never signs with a
real identity; see docs/PACKAGING.md for adding Authenticode / notarization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packaging"
WORK = ROOT / "build" / "pyinstaller"
DIST = WORK / "dist"
OUT = ROOT / "dist" / "release"
APP_NAME = "Genibo Studio"
SLUG = "genibo-studio"

DESCRIPTION = (
    "Genibo Studio: Python-first development environment for the Genibo robot "
    "(editor, compiler to verified command plans, simulator, Bluetooth serial link)."
)

# Runtime libraries the Qt xcb/wayland platform plugins load from the system.
# Checked on clean containers by packaging/check_linux_deps.py.
DEB_DEPENDS = [
    "libfontconfig1",
    "libfreetype6",
    "libdbus-1-3",
    "libegl1",
    "libgl1",
    "libx11-6",
    "libx11-xcb1",
    "libxkbcommon0",
    "libxkbcommon-x11-0",
    "libxcb1",
    "libxcb-cursor0",
    "libxcb-icccm4",
    "libxcb-image0",
    "libxcb-keysyms1",
    "libxcb-randr0",
    "libxcb-render-util0",
    "libxcb-render0",
    "libxcb-shape0",
    "libxcb-shm0",
    "libxcb-sync1",
    "libxcb-xfixes0",
    "libxcb-xkb1",
    "libxrender1",
    "libsm6",
    "libice6",
    "libwayland-client0",
    "libwayland-cursor0",
    "libwayland-egl1",
]
RPM_DEPENDS = [
    "fontconfig",
    "freetype",
    "dbus-libs",
    "libglvnd-egl",
    "libglvnd-glx",
    "libX11",
    "libX11-xcb",
    "libxkbcommon",
    "libxkbcommon-x11",
    "libxcb",
    "xcb-util-cursor",
    "xcb-util-wm",
    "xcb-util-image",
    "xcb-util-keysyms",
    "xcb-util-renderutil",
    "libXrender",
    "libSM",
    "libICE",
    "libwayland-client",
    "libwayland-cursor",
    "libwayland-egl",
]


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def run(cmd: list[str], **kw: object) -> None:
    log("$ " + " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)  # type: ignore[call-overload]


def project_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def arch() -> str:
    m = platform.machine().lower()
    return {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(m, m)


def file_version(version: str) -> str:
    """Version safe for file names ('~' is valid in deb/rpm versions, awkward in URLs)."""
    return version.replace("~", "-")


def sha256_sidecar(path: Path) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    log(f"artifact {path.name} ({path.stat().st_size / 1e6:.1f} MB) sha256 {digest[:16]}")


# --------------------------------------------------------------------------- pyinstaller


def pyinstaller() -> None:
    shutil.rmtree(WORK, ignore_errors=True)
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--log-level",
            "WARN",
            "--distpath",
            str(DIST),
            "--workpath",
            str(WORK / "work"),
            str(PKG / "genibo-studio.spec"),
        ]
    )


def frozen_executable() -> Path:
    if sys.platform == "darwin":
        return DIST / f"{APP_NAME}.app" / "Contents" / "MacOS" / SLUG
    return DIST / SLUG / (SLUG + (".exe" if sys.platform == "win32" else ""))


def self_test(executable: Path) -> None:
    """Run the frozen app headless. Output also goes to a log file: windowed Windows builds have no stdout."""
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    tag = f"selftest-{sys.platform}-{arch()}"
    shot, logf = OUT / f"{tag}.png", OUT / f"{tag}.log"
    cmd = [str(executable), "--self-test", "--screenshot", str(shot), "--log", str(logf)]
    log("$ " + " ".join(cmd))
    proc = subprocess.run(cmd, env=env, timeout=300, check=False)
    if logf.exists():
        print(logf.read_text(encoding="utf-8"), flush=True)
    if proc.returncode != 0:
        raise SystemExit(f"self-test failed with exit code {proc.returncode}")
    run([str(executable), "--cli", "--version"], env=env, timeout=120)


# --------------------------------------------------------------------------- windows


def find_iscc() -> str | None:
    found = shutil.which("iscc") or shutil.which("ISCC")
    if found:
        return found
    for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES")):
        if base and (Path(base) / "Inno Setup 6" / "ISCC.exe").exists():
            return str(Path(base) / "Inno Setup 6" / "ISCC.exe")
    return None


def package_windows(version: str) -> None:
    src = DIST / SLUG
    base = f"Genibo-Studio-{file_version(version)}-windows-x64"
    zpath = OUT / f"{base}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                z.write(f, Path(APP_NAME) / f.relative_to(src))
    sha256_sidecar(zpath)
    iscc = find_iscc()
    if iscc is None:
        raise SystemExit(
            "Inno Setup (ISCC.exe) not found; install it (choco install innosetup) for the installer"
        )
    numeric = re.match(r"\d+(\.\d+){0,3}", version)
    run(
        [
            iscc,
            "/Qp",
            f"/DAppVersion={version}",
            f"/DAppNumericVersion={numeric.group(0) if numeric else '0.0.0'}",
            f"/DSourceDir={src}",
            f"/DOutputDir={OUT}",
            f"/DOutputBaseFilename={base}-setup",
            f"/DIconFile={PKG / 'icons' / 'genibo.ico'}",
            str(PKG / "windows" / "genibo-studio.iss"),
        ]
    )
    sha256_sidecar(OUT / f"{base}-setup.exe")


# --------------------------------------------------------------------------- macos

ICON_COMPOSER = PKG / "icons" / "genibo.icon"


def install_liquid_glass_icon(app: Path, *, allow_legacy: bool) -> None:
    """Compile genibo.icon (Icon Composer) with actool and add it to the bundle.

    macOS 26+ shows the Liquid Glass icon from Assets.car (CFBundleIconName);
    macOS 14/15 keep using genibo.icns (CFBundleIconFile, set by PyInstaller).
    Needs Xcode 26 or newer (select it with DEVELOPER_DIR).
    """
    out = WORK / "actool"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    cmd = [
        "xcrun", "actool", str(ICON_COMPOSER), "--compile", str(out),
        "--output-format", "human-readable-text", "--notices", "--warnings", "--errors",
        "--output-partial-info-plist", str(out / "partial.plist"),
        "--app-icon", ICON_COMPOSER.stem, "--include-all-app-icons",
        "--enable-on-demand-resources", "NO", "--development-region", "en",
        "--target-device", "mac", "--minimum-deployment-target", "14.0", "--platform", "macosx",
    ]  # fmt: skip
    log("$ " + " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    car = out / "Assets.car"
    if proc.returncode != 0 or not car.exists():
        msg = (
            f"actool could not compile {ICON_COMPOSER.name} (Xcode 26+ required):\n{proc.stdout}{proc.stderr}"
        )
        if allow_legacy:
            log("WARNING: " + msg + "\nkeeping the .icns icon only")
            return
        raise SystemExit(msg)
    shutil.copy2(car, app / "Contents" / "Resources" / "Assets.car")
    info_path = app / "Contents" / "Info.plist"
    with info_path.open("rb") as fh:
        info = plistlib.load(fh)
    with (out / "partial.plist").open("rb") as fh:
        info["CFBundleIconName"] = plistlib.load(fh)["CFBundleIconName"]
    with info_path.open("wb") as fh:
        plistlib.dump(info, fh)
    log(
        f"Liquid Glass icon: Assets.car ({car.stat().st_size / 1e6:.1f} MB), CFBundleIconName={info['CFBundleIconName']}"
    )
    # Info.plist changed: re-seal the bundle (ad hoc) so the self-test runs the final layout.
    run(["codesign", "--force", "--deep", "--sign", "-", str(app)])


def package_macos(version: str) -> None:
    app = DIST / f"{APP_NAME}.app"
    # PyInstaller signs each binary ad hoc; re-sign the bundle so its seal covers the final layout.
    run(["codesign", "--force", "--deep", "--sign", "-", str(app)])
    run(["codesign", "--verify", "--deep", "--strict", str(app)])
    stage = WORK / "dmg"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    run(["ditto", str(app), str(stage / app.name)])
    (stage / "Applications").symlink_to("/Applications")
    dmg = OUT / f"Genibo-Studio-{file_version(version)}-macos-{arch()}.dmg"
    dmg.unlink(missing_ok=True)
    run(
        [
            "hdiutil",
            "create",
            "-volname",
            f"{APP_NAME} {version}",
            "-srcfolder",
            str(stage),
            "-ov",
            "-format",
            "UDZO",
            str(dmg),
        ]
    )
    sha256_sidecar(dmg)


# --------------------------------------------------------------------------- linux

_GLIBC = re.compile(rb"GLIBC_(\d+)\.(\d+)")


def glibc_floor(root: Path) -> tuple[int, int]:
    """Highest GLIBC_x.y symbol version referenced by any ELF file in the bundle."""
    best = (2, 17)
    for f in root.rglob("*"):
        if not f.is_file() or f.is_symlink():
            continue
        with f.open("rb") as fh:
            if fh.read(4) != b"\x7fELF":
                continue
        out = subprocess.run(["objdump", "-T", str(f)], capture_output=True, check=False).stdout
        for m in _GLIBC.finditer(out):
            best = max(best, (int(m.group(1)), int(m.group(2))))
    return best


def nfpm_config(version: str, goarch: str, glibc: tuple[int, int], payload: Path) -> dict:
    g = f"{glibc[0]}.{glibc[1]}"
    return {
        "name": SLUG,
        "arch": goarch,
        "platform": "linux",
        "version": version,
        "release": "1",
        "section": "devel",
        "priority": "optional",
        "maintainer": os.environ.get("GENIBO_MAINTAINER", "Genibo SDK maintainers <genibo@example.invalid>"),
        "vendor": "Genibo SDK",
        # In GitHub Actions this resolves to the repository URL; override with GENIBO_HOMEPAGE.
        "homepage": os.environ.get("GENIBO_HOMEPAGE")
        or (
            f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ['GITHUB_REPOSITORY']}"
            if os.environ.get("GITHUB_REPOSITORY")
            else "https://github.com/"
        ),
        "license": os.environ.get("GENIBO_LICENSE", "Unspecified"),
        "description": DESCRIPTION,
        "contents": [
            {"src": str(payload), "dst": f"/opt/{SLUG}", "type": "tree"},
            {
                "src": str(PKG / "linux" / f"{SLUG}.desktop"),
                "dst": f"/usr/share/applications/{SLUG}.desktop",
                "file_info": {"mode": 0o644},
            },
            *[
                {
                    "src": str(png),
                    "dst": f"/usr/share/icons/hicolor/{png.parent.name}/apps/{SLUG}.png",
                    "file_info": {"mode": 0o644},
                }
                for png in sorted((PKG / "icons" / "hicolor").glob("*/genibo-studio.png"))
            ],
            {"src": f"/opt/{SLUG}/{SLUG}", "dst": f"/usr/bin/{SLUG}", "type": "symlink"},
            {"src": f"/opt/{SLUG}/{SLUG}", "dst": "/usr/bin/genibo", "type": "symlink"},
        ],
        "overrides": {
            "deb": {"depends": [f"libc6 (>= {g})", *DEB_DEPENDS]},
            "rpm": {"depends": [f"glibc >= {g}", *RPM_DEPENDS]},
        },
    }


def package_linux(version: str) -> None:
    src = DIST / SLUG
    a = arch()
    goarch = {"x86_64": "amd64", "arm64": "arm64"}[a]
    glibc = glibc_floor(src)
    log(f"bundle requires glibc >= {glibc[0]}.{glibc[1]}")
    tgz = OUT / f"Genibo-Studio-{file_version(version)}-linux-{a}.tar.gz"
    with tarfile.open(tgz, "w:gz") as t:
        t.add(src, arcname=f"{SLUG}-{version}")
    sha256_sidecar(tgz)
    if shutil.which("nfpm") is None:
        raise SystemExit("nfpm not found on PATH (https://nfpm.goreleaser.com); needed for .deb/.rpm")
    cfg = WORK / "nfpm.yaml"
    cfg.write_text(
        json.dumps(nfpm_config(version, goarch, glibc, src), indent=2), encoding="utf-8"
    )  # JSON is YAML
    rpm_arch = {"amd64": "x86_64", "arm64": "aarch64"}[goarch]
    targets = {
        "deb": OUT / f"{SLUG}_{file_version(version)}_{goarch}.deb",
        "rpm": OUT / f"{SLUG}-{file_version(version)}-1.{rpm_arch}.rpm",
    }
    for packager, target in targets.items():
        target.unlink(missing_ok=True)
        run(["nfpm", "package", "--config", str(cfg), "--packager", packager, "--target", str(target)])
        sha256_sidecar(target)
    (OUT / f"linux-{a}-glibc.txt").write_text(f"{glibc[0]}.{glibc[1]}\n", encoding="utf-8")


# --------------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", default=None, help="package version (default: pyproject version)")
    ap.add_argument(
        "--skip-pyinstaller", action="store_true", help="reuse an existing build/pyinstaller/dist"
    )
    ap.add_argument(
        "--self-test", action="store_true", help="run the frozen app's --self-test after building"
    )
    ap.add_argument(
        "--allow-legacy-mac-icon",
        action="store_true",
        help="macOS: continue with the .icns icon only if actool cannot compile genibo.icon",
    )
    args = ap.parse_args()
    version = args.version or project_version()
    OUT.mkdir(parents=True, exist_ok=True)
    log(f"Genibo Studio {version} for {sys.platform}/{arch()} (Python {platform.python_version()})")
    if not args.skip_pyinstaller:
        pyinstaller()
        if sys.platform == "darwin":
            install_liquid_glass_icon(DIST / f"{APP_NAME}.app", allow_legacy=args.allow_legacy_mac_icon)
    if args.self_test:
        self_test(frozen_executable())
    if sys.platform == "win32":
        package_windows(version)
    elif sys.platform == "darwin":
        package_macos(version)
    elif sys.platform.startswith("linux"):
        package_linux(version)
    else:
        raise SystemExit(f"unsupported platform {sys.platform}")
    log("done: " + ", ".join(sorted(p.name for p in OUT.iterdir() if not p.name.endswith(".sha256"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
