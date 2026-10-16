"""Render a fictional life-atlas preview without AstrBot, archives or networking."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_ziwei.engine import build_chart  # noqa: E402
from astrbot_plugin_ziwei.life_atlas import build_life_atlas  # noqa: E402
from astrbot_plugin_ziwei.life_atlas_commands import (  # noqa: E402
    parse_life_atlas_request,
)
from astrbot_plugin_ziwei.life_atlas_renderer import LifeAtlasRenderer  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="离线人生变动图谱预览（虚构演示资料）")
    parser.add_argument(
        "command",
        nargs="?",
        default="",
        help="明确的出生日期、时刻、性别及选项，不包含 /人生 或 /紫微 图谱",
    )
    parser.add_argument("--output", default="life-atlas.png")
    parser.add_argument("--font", default="")
    parser.add_argument("--json", action="store_true", help="输出完整结构化 JSON")
    args = parser.parse_args()
    try:
        request = parse_life_atlas_request(args.command)
        if request.chart_request is None:
            raise ValueError(
                "离线预览须明确填写出生日期、时刻和性别，不支持读取已有命盘"
            )
        natal = request.chart_request
        atlas = build_life_atlas(build_chart(natal.birth, natal.profile))
        target = Path(args.output).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if args.json:
            target.write_text(
                json.dumps(atlas, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        else:
            target.write_bytes(
                LifeAtlasRenderer(args.font).render(
                    atlas, request.image_theme, demo=True
                )
            )
        print(target)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"图谱预览失败：{exc}\n")


if __name__ == "__main__":
    main()
