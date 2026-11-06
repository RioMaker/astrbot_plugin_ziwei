"""Explicit personal-chart command boundaries and display-only options."""

import re
import shlex

from .commands import parse_request
from .themes import normalize_theme

PERSONAL_HELP = """绑定一次，以后直接看自己的盘。
/紫微 绑定 199006150830 男（虚构示例，请换成自己的资料）
/紫微 绑定：从自己的起盘记录中选择
/我的命盘：返回已绑定的本命盘图片
/紫微 流年 2026：用已绑定资料排农历流年
/紫微 大运 2：用已绑定资料排第2个大运，0为童限
/紫微 换绑 199207201430 女：明确替换绑定（虚构示例）
/紫微 解绑：解除绑定，保留历史记录
支持紫薇等原别名、省空格写法，以及 主题=白天／主题=夜间、文字。
同一机器人内各已开启的群通用；私聊和未开启群不可用。
普通排盘不会改变绑定；绑定保留当时的本命盘和取法。"""


def split_personal_command(text):
    for action in ("我的命盘", "绑定", "换绑", "解绑", "流年", "大运"):
        if not text.startswith(action):
            continue
        tail = text[len(action) :]
        if (
            not tail
            or tail[0].isspace()
            or re.match(
                r"(?:[0-9]|公历|阳历|农历|阴历|solar|lunar|命盘=|主题=|输出=|页=|文字$|图片$|帮助(?:文字)?$|help(?:text)?$)",
                tail,
                re.IGNORECASE,
            )
        ):
            return action, tail.strip()
    return None


def natal_request(args, config):
    request = parse_request(args, config)
    if (
        request.flow_year is not None
        or request.flow_target is not None
        or request.decade_index is not None
    ):
        raise ValueError(
            "绑定只需出生资料；查看运势盘请在绑定后使用 /紫微 流年 或 /紫微 大运。"
        )
    return request


def display_options(args, config):
    options = {}
    for token in shlex.split(args):
        if token in {"文字", "text", "图片", "image"}:
            key, value = "输出", token
        elif "=" in token:
            key, value = token.split("=", 1)
        else:
            raise ValueError(
                "仅支持 主题=白天、主题=夜间 或 文字；修改出生资料请用 /紫微 换绑。"
            )
        if key not in {"主题", "输出"} or key in options:
            raise ValueError("显示选项无效或重复；修改出生资料请用 /紫微 换绑。")
        options[key] = value
    output = options.get("输出", config.get("output_mode", "image"))
    output = {"文字": "text", "图片": "image"}.get(output, output)
    if output not in {"image", "text"}:
        raise ValueError("输出须为图片或文字。")
    return output, normalize_theme(
        options.get("主题", config.get("image_theme", "night"))
    )
