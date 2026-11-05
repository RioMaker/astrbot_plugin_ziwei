"""Grouped, starfield command card, with no model or network dependency."""

import io

from .help_content import HELP_INTRO, HELP_SECTIONS
from .modern_renderer import Painter
from .starfield import constellation, orbit


def _wrap(painter, value, size, width):
    lines, line = [], ""
    for character in value:
        if line and painter.font(size).getlength(line + character) > width:
            lines.append(line)
            line = ""
        line += character
    if line:
        lines.append(line)
    return lines


class HelpRenderer:
    def __init__(self, font_path):
        self.font_path = font_path

    def render(self, theme="night"):
        width, margin = 1080, 56
        # Measure before allocating the final canvas, so wrapping never clips.
        measure = Painter(width, 1, self.font_path, theme, background=False)
        sections = []
        for title, rows in HELP_SECTIONS:
            measured = [
                (_wrap(measure, command, 28, 900), _wrap(measure, description, 22, 900))
                for command, description in rows
            ]
            height = 82 + sum(len(a) * 40 + len(b) * 34 + 24 for a, b in measured)
            sections.append((title, measured, height))
        footer_y = 230 + sum(height + 22 for _, _, height in sections)
        painter = Painter(width, footer_y + 162, self.font_path, theme)
        p = painter.palette
        orbit(painter.draw, 91, 92, 34, p)
        painter.text("紫微斗数", 150, 62, 46, p.ink)
        painter.text(HELP_INTRO, 152, 127, 24, p.muted)
        constellation(painter.draw, 665, 65, 325, 78, p)
        painter.text("指令指南", margin, 190, 22, p.accent)
        painter.text("ZIWEI  /  CHART GUIDE", 737, 192, 17, p.muted)
        y = 230
        for index, (title, rows, height) in enumerate(sections, 1):
            painter.card(margin, y, width - margin * 2, height, radius=14)
            painter.text(f"0{index}", margin + 26, y + 26, 23, p.accent)
            painter.text(title, margin + 79, y + 22, 30, p.ink)
            painter.rule(margin + 26, y + 65, width - margin * 2 - 52)
            line_y = y + 86
            for commands, descriptions in rows:
                for line in commands:
                    painter.text(line, margin + 28, line_y, 28, p.accent)
                    line_y += 40
                for line in descriptions:
                    painter.text(line, margin + 28, line_y, 22, p.muted)
                    line_y += 34
                line_y += 24
            y += height + 22
        painter.text(
            "所有日期均为虚构演示资料 · 请填写实际需要排盘的资料",
            margin,
            footer_y + 4,
            22,
            p.muted,
        )
        painter.text(
            "仅指令排盘，暂不提供 AI 看盘、AI 排盘或辅助定盘。",
            margin,
            footer_y + 44,
            22,
            p.ink,
        )
        painter.rule(margin, footer_y + 93, width - margin * 2)
        painter.text(
            "完整参数与可复制示例：/紫微 帮助 文字",
            margin,
            footer_y + 117,
            22,
            p.accent,
        )
        stream = io.BytesIO()
        painter.image.save(stream, format="PNG", optimize=True)
        return stream.getvalue()
