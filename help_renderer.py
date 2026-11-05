"""A short, illustrated command guide with no model or network dependency."""

import io

from .help_content import (
    HELP_BIRTH_FIELDS,
    HELP_BIRTH_NOTE,
    HELP_BIRTH_TITLE,
    HELP_IMAGE_NOTES,
    HELP_INTRO,
    HELP_MORE_COMMAND,
    HELP_OPTIONS,
    HELP_OPTIONS_NOTE,
)
from .modern_renderer import Painter
from .starfield import constellation, orbit


class HelpRenderer:
    def __init__(self, font_path):
        self.font_path = font_path

    def render(self, theme="night"):
        width, margin = 1080, 48
        painter = Painter(width, 944, self.font_path, theme)
        p = painter.palette
        left, content_width = margin + 30, width - margin * 2 - 60

        def section(number, title, top, height, *, featured=False):
            painter.card(
                margin,
                top,
                width - margin * 2,
                height,
                fill=p.center if featured else p.paper,
                outline=p.accent if featured else p.border,
            )
            painter.text(number, left, top + 26, 28, p.accent)
            painter.text(title, left + 62, top + 24, 34, p.ink)

        orbit(painter.draw, 84, 78, 28, p)
        painter.text("紫微排盘 · 使用说明", 134, 44, 42, p.ink)
        painter.text(HELP_INTRO, 136, 103, 26, p.muted)
        constellation(painter.draw, 782, 49, 230, 68, p)

        section("01", HELP_BIRTH_TITLE, 160, 250, featured=True)
        painter.card(
            left,
            238,
            content_width,
            102,
            fill=p.paper,
            outline=p.paper,
            radius=8,
        )
        # Labels sit under their own field, so beginners can see what to replace.
        fields = [
            (
                value,
                label,
                max(
                    painter.font(40).getlength(value),
                    painter.font(24).getlength(label),
                ),
            )
            for value, label in HELP_BIRTH_FIELDS
        ]
        gap = (content_width - 48 - sum(item[2] for item in fields)) / (len(fields) - 1)
        x = left + 24
        for value, label, field_width in fields:
            for text, size, y, color in (
                (value, 40, 252, p.accent),
                (label, 24, 306, p.muted),
            ):
                text_width = painter.font(size).getlength(text)
                painter.text(text, x + (field_width - text_width) / 2, y, size, color)
            x += field_width + gap
        painter.text(HELP_BIRTH_NOTE, left, 366, 26, p.ink)

        section("02", "常用选项", 426, 300)
        painter.text(HELP_OPTIONS_NOTE, left, 494, 26, p.muted)
        for index, (label, position, option) in enumerate(HELP_OPTIONS):
            y = 546 + index * 58
            painter.text(label, left, y, 30, p.ink)
            painter.text(position, left + 240, y + 3, 26, p.muted)
            painter.text(option, left + 402, y, 32, p.accent)

        painter.card(margin, 744, width - margin * 2, 94, fill=p.center)
        painter.text("更多用法", left, 777, 26, p.ink)
        painter.text(HELP_MORE_COMMAND, left + 240, 773, 34, p.accent)
        for index, note in enumerate(HELP_IMAGE_NOTES):
            painter.text(note, margin, 862 + index * 36, 22, p.muted)

        stream = io.BytesIO()
        painter.image.save(stream, format="PNG", optimize=True)
        return stream.getvalue()
