"""A short, illustrated command guide with no model or network dependency."""

import io

from .help_content import (
    HELP_BIND_NOTE,
    HELP_BIRTH_FIELDS,
    HELP_BIRTH_NOTE,
    HELP_BIRTH_TITLE,
    HELP_COMPATIBILITY,
    HELP_COMPATIBILITY_NOTE,
    HELP_COMPATIBILITY_TITLE,
    HELP_ENTRY_COMMAND,
    HELP_IMAGE_NOTES,
    HELP_INTRO,
    HELP_OPTIONS,
    HELP_OPTIONS_NOTE,
    HELP_PERSONAL_ACTIONS,
)
from .modern_renderer import Painter
from .starfield import constellation, orbit


class HelpRenderer:
    def __init__(self, font_path):
        self.font_path = font_path

    def render(self, theme="night"):
        width, margin = 1080, 48
        compatibility_top = 426
        options_top = compatibility_top + 120 + len(HELP_COMPATIBILITY) * 100 + 18
        personal_top = options_top + 120 + len(HELP_OPTIONS) * 100 + 18
        footer_top = personal_top + 516
        height = footer_top + 46 + len(HELP_IMAGE_NOTES) * 34 + 12
        painter = Painter(width, height, self.font_path, theme)
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

        def examples(number, title, top, rows, note):
            section(number, title, top, 120 + len(rows) * 100)
            painter.text(note, left, top + 68, 26, p.muted)
            for index, (label, hint, example) in enumerate(rows):
                y = top + 118 + index * 100
                painter.text(label, left, y, 30, p.ink)
                painter.text(hint, left + 240, y + 3, 26, p.muted)
                painter.text(example, left, y + 42, 32, p.accent)
                if index < len(rows) - 1:
                    painter.rule(left, y + 86, content_width)

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

        examples(
            "02",
            HELP_COMPATIBILITY_TITLE,
            compatibility_top,
            HELP_COMPATIBILITY,
            HELP_COMPATIBILITY_NOTE,
        )
        examples("03", "常用选项", options_top, HELP_OPTIONS, HELP_OPTIONS_NOTE)

        section("04", "我的命盘 · 绑定一次，随时查看", personal_top, 490)
        painter.text(
            "绑定、换绑都要填出生资料；每次均留记录。",
            left,
            personal_top + 68,
            26,
            p.muted,
        )
        for index, (label, example) in enumerate(HELP_PERSONAL_ACTIONS):
            y = personal_top + 114 + index * 50
            painter.text(label, left, y + 2, 30, p.ink)
            painter.text(example, left + 240, y, 32, p.accent)
        painter.text(
            "大运填序号，0为童限；解绑保留历史记录。",
            left,
            personal_top + 416,
            24,
            p.muted,
        )
        painter.text(HELP_BIND_NOTE, left, personal_top + 452, 24, p.muted)

        painter.text("查看帮助", left, footer_top + 4, 26, p.ink)
        painter.text(HELP_ENTRY_COMMAND, left + 240, footer_top, 32, p.accent)
        for index, note in enumerate(HELP_IMAGE_NOTES):
            painter.text(note, margin, footer_top + 46 + index * 34, 22, p.muted)

        stream = io.BytesIO()
        painter.image.save(stream, format="PNG", optimize=True)
        return stream.getvalue()
