"""Derive every platform icon from the designer sources in packaging/icons/source/.

Sources (committed, provided by the designer):
    source/genibo.png    1024x1024 RGBA master: full-bleed rounded square
    source/genibo.ico    Windows icon as delivered
    genibo.icon/         Icon Composer bundle (macOS 26 "Liquid Glass"); compiled by actool at build time

Generated (committed, so builds need no image tooling):
    genibo.ico                      Windows: delivered 16-128 entries kept byte-for-byte, 20/40 added,
                                    one PNG-compressed 256 entry (the delivered file had a 512 PNG
                                    mislabelled as 256 and a duplicate uncompressed 256)
    genibo.icns                     macOS 14/15 fallback: master on Apple's 824/1024 grid with shadow, 16-1024
    hicolor/<N>x<N>/genibo-studio.png   Linux icon theme sizes 16-512
    ../../src/genibo/studio/resources/genibo.png   512 px window icon used at run time

    uv run --with pillow python packaging/make_icons.py
"""

from __future__ import annotations

import io
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageFilter

HERE = Path(__file__).parent
ICONS = HERE / "icons"
SOURCE = ICONS / "source"
RUNTIME = HERE.parent / "src" / "genibo" / "studio" / "resources"

LINUX_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
ICNS_SIZES = (16, 32, 64, 128, 256, 512, 1024)


def master() -> Image.Image:
    im = Image.open(SOURCE / "genibo.png").convert("RGBA")
    if im.size != (1024, 1024):
        raise SystemExit(f"source/genibo.png must be 1024x1024, got {im.size}")
    return im


def scaled(im: Image.Image, size: int) -> Image.Image:
    return im.resize((size, size), Image.Resampling.LANCZOS)


# --------------------------------------------------------------------------- Windows .ico


def read_ico(path: Path) -> list[tuple[int, int, bytes]]:
    """(directory width, real pixel width, raw image bytes) for every entry."""
    data = path.read_bytes()
    _, kind, count = struct.unpack_from("<HHH", data, 0)
    if kind != 1:
        raise SystemExit(f"{path} is not an icon file")
    entries = []
    for i in range(count):
        w, _h, _c, _r, _planes, _bpp, size, off = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        raw = data[off : off + size]
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            real = struct.unpack(">I", raw[16:20])[0]
        else:
            real = struct.unpack_from("<i", raw, 4)[0]
        entries.append((w or 256, real, raw))
    return entries


def dib_to_image(raw: bytes) -> Image.Image:
    """Decode a 32-bpp BITMAPINFOHEADER icon entry (bottom-up BGRA, height doubled for the mask)."""
    hdr_size, w, h2, _planes, bpp = struct.unpack_from("<IiiHH", raw, 0)
    if bpp != 32:
        raise SystemExit(f"unsupported {bpp}-bpp icon entry")
    h = h2 // 2
    pixels = raw[hdr_size : hdr_size + w * h * 4]
    return Image.frombuffer("RGBA", (w, h), pixels, "raw", "BGRA", 0, -1).copy()


def image_to_dib(im: Image.Image) -> bytes:
    w, h = im.size
    pixels = im.convert("RGBA").transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes("raw", "BGRA")
    mask_row = ((w + 31) // 32) * 4
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(pixels) + mask_row * h, 0, 0, 0, 0)
    return header + pixels + bytes(mask_row * h)  # alpha carries transparency; AND mask all zero


def write_ico(path: Path, images: list[tuple[int, bytes]]) -> None:
    images = sorted(images)
    out = io.BytesIO()
    out.write(struct.pack("<HHH", 0, 1, len(images)))
    offset = 6 + 16 * len(images)
    for size, raw in images:
        d = 0 if size >= 256 else size
        out.write(struct.pack("<BBBBHHII", d, d, 0, 0, 1, 32, len(raw), offset))
        offset += len(raw)
    for _, raw in images:
        out.write(raw)
    path.write_bytes(out.getvalue())


def make_ico(m: Image.Image) -> None:
    delivered = read_ico(SOURCE / "genibo.ico")
    keep = {
        real: raw for dir_w, real, raw in delivered if real == dir_w and real <= 128 and raw[:4] != b"\x89PNG"
    }
    big = next((raw for dir_w, real, raw in delivered if real == 256 and raw[:4] != b"\x89PNG"), None)
    entries: list[tuple[int, bytes]] = [(s, r) for s, r in keep.items()]
    for s in (20, 40):  # Windows shell sizes at 125 % / 250 % scaling
        if s not in keep:
            entries.append((s, image_to_dib(scaled(m, s))))
    img256 = dib_to_image(big) if big is not None else scaled(m, 256)
    png = io.BytesIO()
    img256.save(png, "PNG", optimize=True)
    entries.append((256, png.getvalue()))
    write_ico(ICONS / "genibo.ico", entries)
    check = read_ico(ICONS / "genibo.ico")
    assert all(d == r for d, r, _ in check), "directory/size mismatch"
    print("genibo.ico:", ", ".join(str(r) for _, r, _ in check))


# --------------------------------------------------------------------------- macOS .icns


def apple_grid(m: Image.Image) -> Image.Image:
    """Place the full-bleed artwork on Apple's macOS icon grid: 824 px body, centred, soft shadow."""
    canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    body = scaled(m, 824)
    alpha = body.getchannel("A")
    shadow = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    shade = Image.new("RGBA", body.size, (0, 0, 0, 90))
    shadow.paste(shade, (100, 112), alpha)
    canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(14)))
    canvas.alpha_composite(body, (100, 100))
    return canvas


def make_icns(m: Image.Image) -> None:
    """Prefer Apple's iconutil (writes every classic entry incl. 16/32 px); Pillow elsewhere."""
    grid = apple_grid(m)
    grid.save(ICONS / "genibo-macos-preview.png", optimize=True)
    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "genibo.iconset"
            iconset.mkdir()
            for pt in (16, 32, 128, 256, 512):
                scaled(grid, pt).save(iconset / f"icon_{pt}x{pt}.png")
                scaled(grid, pt * 2).save(iconset / f"icon_{pt}x{pt}@2x.png")
            subprocess.run(
                ["iconutil", "-c", "icns", str(iconset), "-o", str(ICONS / "genibo.icns")], check=True
            )
        print("genibo.icns (iconutil): 16-1024 incl. @2x")
    else:
        grid.save(ICONS / "genibo.icns", format="ICNS", append_images=[scaled(grid, s) for s in ICNS_SIZES])
        print("genibo.icns (Pillow):", ", ".join(map(str, ICNS_SIZES)))


# --------------------------------------------------------------------------- Linux / runtime


def make_linux_and_runtime(m: Image.Image) -> None:
    for s in LINUX_SIZES:
        d = ICONS / "hicolor" / f"{s}x{s}"
        d.mkdir(parents=True, exist_ok=True)
        scaled(m, s).save(d / "genibo-studio.png", optimize=True)
    print("hicolor:", ", ".join(map(str, LINUX_SIZES)))
    RUNTIME.mkdir(parents=True, exist_ok=True)
    scaled(m, 512).save(RUNTIME / "genibo.png", optimize=True)
    print("runtime icon:", RUNTIME / "genibo.png")


def main() -> None:
    m = master()
    make_ico(m)
    make_icns(m)
    make_linux_and_runtime(m)
    if not (ICONS / "genibo.icon" / "icon.json").exists():
        raise SystemExit("genibo.icon/ (Icon Composer bundle) is missing")


if __name__ == "__main__":
    main()
