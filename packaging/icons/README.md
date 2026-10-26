# Application icons

## Sources (from the designer, do not edit by hand)

| File | What |
|---|---|
| `source/genibo.png` | 1024×1024 RGBA master: full-bleed rounded square |
| `source/genibo.ico` | Windows icon as delivered (16–256 px, hand-tuned small sizes) |
| `genibo.icon/` | Icon Composer bundle (macOS 26 Liquid Glass): `icon.json` plus a transparent foreground layer |

## Generated (committed; run `uv run --with pillow python packaging/make_icons.py` after changing a source)

| File | Used by | Notes |
|---|---|---|
| `genibo.ico` | Windows exe and installer | Delivered 16–128 entries kept byte for byte; 20/40 px added for 125 %/250 % scaling; one PNG-compressed 256 entry. The delivered file declared a 512 px PNG as 256 alongside an uncompressed 256 BMP; both are replaced by the single PNG 256 |
| `genibo.icns` | macOS 14/15 (`CFBundleIconFile`) | Master on Apple's icon grid (824 px body on a 1024 canvas, soft shadow), all sizes 16–1024 (`iconutil`) |
| `genibo-macos-preview.png` | review only | What `genibo.icns` looks like at 1024 px |
| `hicolor/<N>x<N>/genibo-studio.png` | Linux .deb/.rpm | Installed to `/usr/share/icons/hicolor/`, sizes 16–512 |
| `../../src/genibo/studio/resources/genibo.png` | running app | 512 px window icon (wheel and frozen builds). Not applied on packaged macOS, where it would override the bundle icon |

## Compiled at build time

`genibo.icon` → `Assets.car` (+ `CFBundleIconName`) via `xcrun actool`, in
`packaging/build.py`. It needs Xcode 26 or newer. CI selects the newest
`/Applications/Xcode_26*.app` on the macOS runners. A local build without it
fails, unless you pass `--allow-legacy-mac-icon`, which ships the `.icns` only.
