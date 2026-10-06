"""Generate a standalone chart without loading AstrBot."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_ziwei.commands import parse_request  # noqa: E402
from astrbot_plugin_ziwei.engine import apply_flow, build_chart  # noqa: E402
from astrbot_plugin_ziwei.renderer import Renderer, text_chart  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="离线紫微斗数排盘预览")
    parser.add_argument("command", help="出生日期 时刻 性别及选项，不包含 /紫微")
    parser.add_argument("--output", default="ziwei.png")
    parser.add_argument("--font", default="")
    parser.add_argument("--json", action="store_true", help="输出完整结构化 JSON")
    args = parser.parse_args()
    try:
        request = parse_request(args.command)
        chart = build_chart(request.birth, request.profile)
        if request.flow_year is not None or request.flow_target is not None:
            chart = apply_flow(
                chart, year=request.flow_year, target=request.flow_target
            )
        target = Path(args.output).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if args.json:
            target.write_text(
                json.dumps(chart, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        elif request.output == "text":
            target.write_text(text_chart(chart) + "\n", encoding="utf-8")
        else:
            target.write_bytes(Renderer(args.font).render(chart))
        print(target)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"排盘失败：{exc}\n")


if __name__ == "__main__":
    main()
