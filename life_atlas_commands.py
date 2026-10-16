"""Input for the fixed-range life atlas, sharing the chart calendar parser."""

import re
import shlex
from dataclasses import dataclass

from .commands import ChartRequest, parse_request
from .themes import normalize_theme

_RANGE_OPTIONS = {"流年", "流盘", "流时", "运限", "大限", "大运"}


@dataclass(frozen=True)
class LifeAtlasRequest:
    chart_request: ChartRequest | None
    chart_id: str = ""
    image_theme: str = "night"


def parse_life_atlas_request(text: str, config=None) -> LifeAtlasRequest:
    """Parse birth data or an archive selection without inferring missing data."""
    if len(text) > 1024:
        raise ValueError("指令过长")
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise ValueError("指令中存在未闭合的引号") from exc

    options = {}
    for token in tokens:
        if token in {"文字", "text"}:
            raise ValueError("人生图谱仅支持图片输出")
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key in _RANGE_OPTIONS:
            raise ValueError(
                "人生图谱固定从出生到60虚岁所在大限结束，不支持指定流年或运限"
            )
        if key == "输出" and value in {"文字", "text"}:
            raise ValueError("人生图谱仅支持图片输出")
        if key in {"命盘", "主题"}:
            if key in options:
                raise ValueError(f"参数「{key}」重复")
            options[key] = value

    theme = normalize_theme(options.get("主题", "night"))
    if "命盘" in options:
        if any(
            "=" not in token or token.split("=", 1)[0] not in {"命盘", "主题"}
            for token in tokens
        ):
            raise ValueError(
                "命盘编号只能与图片主题同时使用，不能混用出生资料或其他参数"
            )
        if not re.fullmatch(r"[0-9a-f]{32}", options["命盘"]):
            raise ValueError("命盘编号须为32位小写十六进制字符")
        return LifeAtlasRequest(None, options["命盘"], theme)

    if not tokens or all(token.startswith("主题=") for token in tokens):
        return LifeAtlasRequest(None, image_theme=theme)

    # Keep calculation defaults, but atlas presentation is independent of natal
    # chart output/theme settings and always defaults to the approved dark image.
    chart_config = dict(config or {})
    chart_config.update(output_mode="image", image_theme="night")
    request = parse_request(text, chart_config)
    if request.output != "image":
        raise ValueError("人生图谱仅支持图片输出")
    return LifeAtlasRequest(request, image_theme=request.image_theme)
