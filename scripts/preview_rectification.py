"""Preview explicit hypothetical date ranges without AstrBot or runtime archives."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_ziwei.rectification import build_candidates  # noqa: E402
from astrbot_plugin_ziwei.rectification_commands import (  # noqa: E402
    parse_rectification_request,
)
from astrbot_plugin_ziwei.rectification_renderer import (  # noqa: E402
    render_candidate_collage,
)
from astrbot_plugin_ziwei.renderer import Renderer  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="离线定盘候选预览，不调用AI")
    parser.add_argument("command", help="日期 性别 [时辰=辰,巳] 及历法选项")
    parser.add_argument("--candidate", default="c01")
    parser.add_argument("--output", default="rectification.png")
    output_mode = parser.add_mutually_exclusive_group()
    output_mode.add_argument(
        "--json", action="store_true", help="导出全部候选与原始范围"
    )
    output_mode.add_argument(
        "--collage", action="store_true", help="把全部候选完整拼成单张总图"
    )
    args = parser.parse_args()
    try:
        parsed = parse_rectification_request(args.command)
        candidates = build_candidates(parsed)
        target = Path(args.output).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if args.json:
            target.write_text(
                json.dumps(
                    {"metadata": parsed.metadata, "candidates": candidates},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        elif args.collage:
            png = render_candidate_collage(
                candidates, Renderer(), parsed.request.image_theme
            )
            target.write_bytes(png)
            print(f"全部候选已拼为单张PNG，文件大小：{len(png):,} 字节")
        else:
            candidate = next((c for c in candidates if c["id"] == args.candidate), None)
            if candidate is None:
                raise ValueError("候选ID不存在，请先用--json查看候选清单")
            target.write_bytes(
                Renderer().render(candidate["chart"], parsed.request.image_theme)
            )
            print(candidate["label"])
        print(f"候选数：{len(candidates)}；时刻仅为范围代表。\n{target}")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"定盘预览失败：{exc}\n")


if __name__ == "__main__":
    main()
