"""Rebuild distributable templates with fixed fictional inputs only."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_ziwei.commands import DEMO_BIRTH, parse_request  # noqa: E402
from astrbot_plugin_ziwei.engine import apply_decade, apply_flow, build_chart  # noqa: E402
from astrbot_plugin_ziwei.help_renderer import HelpRenderer  # noqa: E402
from astrbot_plugin_ziwei.renderer import Renderer  # noqa: E402


def main():
    assets = ROOT / "assets"
    renderer = Renderer()
    examples = {
        "preview-natal.png": DEMO_BIRTH,
        "preview-decade.png": DEMO_BIRTH + " 运限=1",
        "preview-flow.png": DEMO_BIRTH + " 流年=2026 主题=白天",
        "preview-night.png": DEMO_BIRTH + " 流年=2026",
        "preview-day.png": DEMO_BIRTH + " 主题=白天",
        "preview-dense.png": "农历 1993-10-25 20:00 男 真太阳时=关 流年=2026",
    }
    for name, text in examples.items():
        request = parse_request(text)
        chart = build_chart(request.birth, request.profile)
        if request.decade_index is not None:
            chart = apply_decade(chart, request.decade_index)
        elif request.flow_year is not None:
            chart = apply_flow(chart, year=request.flow_year)
        (assets / name).write_bytes(renderer.render(chart, request.image_theme))
        print(name)
    (assets / "preview-help.png").write_bytes(HelpRenderer(renderer.font_path).render())
    print("preview-help.png")


if __name__ == "__main__":
    main()
