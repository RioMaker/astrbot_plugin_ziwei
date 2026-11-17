"""Export the same native star-orbit mark used by charts and image help."""

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_ziwei.starfield import orbit  # noqa: E402
from astrbot_plugin_ziwei.themes import NIGHT  # noqa: E402


def main():
    for size, target in (
        (128, ROOT / "logo.png"),
        (1024, ROOT / "assets" / "logo-orbit-1024.png"),
    ):
        image = Image.new("RGB", (size, size), NIGHT.bg)
        orbit(image, size / 2, size / 2, size * 0.38, NIGHT)
        if target == ROOT / "logo.png":
            # The default list icon needs few colors, including smooth edge shades.
            image = image.quantize(
                colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
            )
        image.save(target, optimize=True)
        print(target.name)


if __name__ == "__main__":
    main()
