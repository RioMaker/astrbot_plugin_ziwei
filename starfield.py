"""Deterministic celestial ornaments shared by chart and help images.

These are decorative constellations, not an astronomical sky calculation.
All colors, random state and surfaces belong to the current render.
"""

import math
import random

from PIL import Image, ImageDraw, ImageFilter


def _rgb(value):
    return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))


def starfield(width, height, palette):
    canvas = Image.new("RGB", (width, height), palette.bg)
    night = palette.name == "night"
    # A small blurred nebula keeps large/dense charts cheap to render.
    haze = Image.new("RGBA", (max(1, width // 8), max(1, height // 8)))
    draw = ImageDraw.Draw(haze)
    w, h = haze.size
    draw.ellipse(
        (w * 0.15, -h * 0.25, w * 1.2, h * 0.5),
        fill=(64, 75, 142, 65 if night else 13),
    )
    draw.ellipse(
        (-w * 0.6, h * 0.1, w * 0.6, h * 0.9),
        fill=(79, 53, 124, 48 if night else 10),
    )
    haze = haze.filter(ImageFilter.GaussianBlur(max(8, w // 8)))
    haze = haze.resize(canvas.size, Image.Resampling.BILINEAR)
    canvas.paste(haze, (0, 0), haze)
    stars = Image.new("RGBA", canvas.size)
    draw = ImageDraw.Draw(stars)
    rng = random.Random(271828)
    color = _rgb(palette.accent)
    count = max(35, width * height // 11000) if width > 16 and height > 16 else 0
    for _ in range(count):
        x, y = rng.randrange(8, width - 8), rng.randrange(8, height - 8)
        r = rng.choice((0.7, 0.8, 1.1, 1.5))
        draw.ellipse(
            (x - r, y - r, x + r, y + r), fill=(*color, rng.randrange(65, 155))
        )
    canvas.paste(stars, (0, 0), stars)
    canvas.putpixel((0, 0), _rgb(palette.bg))
    return canvas


def orbit_image(radius, palette):
    """Refine the original armillary mark as a transparent, antialiased tile."""
    scale = 4
    extent = math.ceil(radius * 1.1 + 2)
    size = extent * 2 + 1
    tile = Image.new("RGBA", (size * scale, size * scale))
    draw = ImageDraw.Draw(tile)
    cx = cy = (extent + 0.5) * scale
    radius *= scale
    # Keep the original theme colors, with a little more definition in the rings.
    line = tuple(
        round(base * 0.75 + highlight * 0.25)
        for base, highlight in zip(
            _rgb(palette.border), _rgb(palette.muted), strict=True
        )
    )
    light = palette.accent
    for ratio, weight in ((1, 0.035), (0.73, 0.025)):
        r = radius * ratio
        draw.ellipse(
            (cx - r, cy - r, cx + r, cy + r),
            outline=line,
            width=max(1, round(radius * weight)),
        )
    draw.ellipse(
        (cx - radius, cy - radius * 0.32, cx + radius, cy + radius * 0.32),
        outline=line,
        width=max(1, round(radius * 0.027)),
    )
    for angle in (-70, 20, 130, 210):
        t = math.radians(angle)
        x, y = cx + radius * math.cos(t), cy + radius * math.sin(t)
        r = radius * (0.075 if angle == -70 else 0.06)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=light)
    h, v, waist = radius * 0.26, radius * 0.29, radius * 0.065
    draw.polygon(
        (
            (cx, cy - v),
            (cx + waist, cy - waist),
            (cx + h, cy),
            (cx + waist, cy + waist),
            (cx, cy + v),
            (cx - waist, cy + waist),
            (cx - h, cy),
            (cx - waist, cy - waist),
        ),
        fill=light,
    )
    return tile.resize((size, size), Image.Resampling.LANCZOS)


def orbit(image, cx, cy, radius, palette):
    """Draw the shared logo directly on this request's chart or help background."""
    tile = orbit_image(radius, palette)
    extent = tile.width // 2
    image.paste(tile, (round(cx) - extent, round(cy) - extent), tile)


def constellation(draw, x, y, width, height, palette):
    points = [
        (0, 0.66),
        (0.15, 0.29),
        (0.34, 0.45),
        (0.51, 0.12),
        (0.70, 0.34),
        (0.86, 0.10),
        (1, 0.44),
    ]
    points = [(x + px * width, y + py * height) for px, py in points]
    draw.line(points, fill=palette.border, width=1)
    for i, (px, py) in enumerate(points):
        r = 3 if i in (1, 4) else 2
        draw.ellipse((px - r, py - r, px + r, py + r), fill=palette.accent)
