"""Inspect scoped notes against an exported chart without AstrBot or a model."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_ziwei.analysis_facts import (  # noqa: E402
    SCOPES,
    TOPICS,
    build_facts,
)
from astrbot_plugin_ziwei.knowledge import KnowledgeBase  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="查询指定命盘和分析作用域的文献总结")
    parser.add_argument("chart", type=Path)
    parser.add_argument("--scope", choices=SCOPES, default="natal")
    parser.add_argument("--topic", choices=TOPICS, default="general")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        chart = json.loads(args.chart.read_text("utf-8"))
        facts = build_facts(chart, args.scope, args.topic)
        knowledge = KnowledgeBase().query(facts, offset=args.offset)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {"facts": facts, "knowledge": knowledge}, ensure_ascii=False, indent=2
            )
            + "\n",
            encoding="utf-8",
        )
        print(args.output.resolve())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"查询失败：{type(exc).__name__}，请检查导出盘及查询范围。\n")


if __name__ == "__main__":
    main()
