from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Final

from PIL import Image, UnidentifiedImageError


ROOT: Final = Path(__file__).resolve().parents[1]
ICON_DIR: Final = ROOT / "src-tauri" / "icons"
PUBLIC_DIR: Final = ROOT / "public"
SOURCE_ICON: Final = ICON_DIR / "nothing-icon-1024.png"
FAVICON_PATH: Final = PUBLIC_DIR / "favicon.png"

PNG_SIZES: Final = (
    ("32x32.png", 32),
    ("128x128.png", 128),
    ("128x128@2x.png", 256),
    ("icon.png", 512),
    ("Square30x30Logo.png", 30),
    ("Square44x44Logo.png", 44),
    ("StoreLogo.png", 50),
    ("Square71x71Logo.png", 71),
    ("Square89x89Logo.png", 89),
    ("Square107x107Logo.png", 107),
    ("Square142x142Logo.png", 142),
    ("Square150x150Logo.png", 150),
    ("Square284x284Logo.png", 284),
    ("Square310x310Logo.png", 310),
)
ICO_SIZES: Final = ((16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256))
ICNS_SIZES: Final = (
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
)
ARTIFACT_NAMES: Final = tuple(filename for filename, _size in PNG_SIZES) + ("icon.ico", "icon.icns")


class InvalidIconSourceError(RuntimeError):
    def __init__(self, path: Path, size: tuple[int, int]) -> None:
        self.path = path
        self.size = size
        super().__init__(f"icon source must be a 1024x1024 PNG: {path} ({size[0]}x{size[1]})")


def load_source_icon() -> Image.Image:
    with Image.open(SOURCE_ICON) as image:
        if image.format != "PNG" or image.size != (1024, 1024):
            raise InvalidIconSourceError(SOURCE_ICON, image.size)
        return image.convert("RGBA")


def generate_with_tauri(output_dir: Path) -> bool:
    npx = shutil.which("npx")
    local_tauri = ROOT / "node_modules" / ".bin" / "tauri"
    local_tauri_windows = local_tauri.with_suffix(".cmd")
    if npx is None or not (local_tauri.exists() or local_tauri_windows.exists()):
        return False
    subprocess.run(
        [npx, "--no-install", "tauri", "icon", str(SOURCE_ICON), "-o", str(output_dir)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return True


def save_fallback_icns(source: Image.Image, output_path: Path) -> None:
    iconutil = shutil.which("iconutil")
    if iconutil is not None:
        with tempfile.TemporaryDirectory(prefix="nothing-iconset-") as temp_dir:
            iconset = Path(temp_dir) / "Nothing.iconset"
            iconset.mkdir()
            for filename, size in ICNS_SIZES:
                source.resize((size, size), Image.Resampling.LANCZOS).save(iconset / filename, format="PNG")
            subprocess.run(
                [iconutil, "-c", "icns", str(iconset), "-o", str(output_path)],
                check=True,
                capture_output=True,
                text=True,
            )
        return

    frames = [
        source.resize((size, size), Image.Resampling.LANCZOS)
        for size in (32, 64, 128, 256, 512)
    ]
    source.save(output_path, format="ICNS", append_images=frames)


def generate_with_pillow(source: Image.Image, output_dir: Path) -> None:
    for filename, size in PNG_SIZES:
        source.resize((size, size), Image.Resampling.LANCZOS).save(output_dir / filename, format="PNG")
    source.save(output_dir / "icon.ico", format="ICO", sizes=list(ICO_SIZES))
    save_fallback_icns(source, output_dir / "icon.icns")


def same_raster(left: Path, right: Path) -> bool:
    try:
        with Image.open(left) as left_image, Image.open(right) as right_image:
            left_rgba = left_image.convert("RGBA")
            right_rgba = right_image.convert("RGBA")
            return left_rgba.size == right_rgba.size and left_rgba.tobytes() == right_rgba.tobytes()
    except (FileNotFoundError, OSError, UnidentifiedImageError):
        return False


def publish_artifact(generated: Path, destination: Path, compare_raster: bool = False) -> None:
    if destination.exists():
        if compare_raster and same_raster(generated, destination):
            return
        if not compare_raster and generated.read_bytes() == destination.read_bytes():
            return
    shutil.copyfile(generated, destination)


def main() -> None:
    source = load_source_icon()
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nothing-icons-") as temp_dir:
        output_dir = Path(temp_dir)
        if not generate_with_tauri(output_dir):
            generate_with_pillow(source, output_dir)
        for filename in ARTIFACT_NAMES:
            publish_artifact(output_dir / filename, ICON_DIR / filename, compare_raster=filename == "icon.icns")
        publish_artifact(output_dir / "128x128@2x.png", FAVICON_PATH)


if __name__ == "__main__":
    main()
