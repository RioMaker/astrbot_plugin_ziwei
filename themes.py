"""Immutable image palettes; presentation never changes calculation rules."""

from dataclasses import dataclass

ELEMENTS = {
    character: element
    for element, characters in (
        ("木", "甲乙寅卯"),
        ("火", "丙丁巳午"),
        ("土", "戊己辰戌丑未"),
        ("金", "庚辛申酉"),
        ("水", "壬癸亥子"),
    )
    for character in characters
}


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    paper: str
    center: str
    ink: str
    muted: str
    border: str
    accent: str
    on_accent: str
    major: str
    assistant: str
    malefic: str
    minor: str
    # Wood, fire, earth, metal, water; Lu, Quan, Ke, Ji.
    elements: tuple[str, ...]
    transformations: tuple[str, ...]

    def star_color(self, category):
        return getattr(self, category)

    def element_color(self, character):
        return self.elements[("木", "火", "土", "金", "水").index(ELEMENTS[character])]

    def hua_color(self, value):
        color = self.transformations[("禄", "权", "科", "忌").index(value)]
        # Tint against each palette's actual palace surface.
        weight = 0.12 if self.name == "day" else 0.14
        channels = (
            round(
                int(color[i : i + 2], 16) * weight
                + int(self.paper[i : i + 2], 16) * (1 - weight)
            )
            for i in (1, 3, 5)
        )
        return color, "#" + "".join(f"{channel:02x}" for channel in channels)


DAY = Palette(
    "day",
    "#f8f4ed",
    "#fffef8",
    "#f5f0e6",
    "#3b3a3e",
    "#607071",
    "#d9d2c7",
    "#2e7d32",
    "#fffef8",
    "#9e2f2f",
    "#8a4b9c",
    "#0f1a20",
    "#1e5a8f",
    ("#1a6840", "#9e2f2f", "#815f25", "#607071", "#0f1a20"),
    ("#1a6840", "#815f25", "#1e5a8f", "#9e2f2f"),
)
NIGHT = Palette(
    "night",
    "#10181c",
    "#172328",
    "#1d2e34",
    "#eef0ec",
    "#b6c3c6",
    "#41565d",
    "#99cbbb",
    "#14231f",
    "#e6c78a",
    "#8acdc6",
    "#c6cbd1",
    "#8faed3",
    ("#99cbbb", "#e8a89c", "#d4bc84", "#d7dde3", "#9bbad0"),
    ("#9bccb2", "#e4cb8d", "#9ebfdf", "#e8a29e"),
)


def normalize_theme(value):
    aliases = {"白天": "day", "日间": "day", "夜间": "night", "夜晚": "night"}
    theme = aliases.get(value, value) if isinstance(value, str) else None
    if theme not in {"day", "night"}:
        raise ValueError("主题须为白天／day或夜间／night")
    return theme


def get_theme(value="day"):
    return DAY if normalize_theme(value) == "day" else NIGHT
