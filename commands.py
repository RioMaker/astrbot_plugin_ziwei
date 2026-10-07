"""Validated command input shared by the bot and standalone preview CLI."""

import re
import shlex
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .calendar_core import BirthInput, equation_of_time
from .rules import RuleProfile

DEMO_BIRTH = "1990-06-15 08:30 男"

HELP = f"""紫微斗数排盘
/紫微 开启：群主、群管理员或 AstrBot 管理员在目标群内授权使用。
/紫微 关闭：移除本群使用授权。
/紫微 状态：查看本群是否已开启。
所有群默认未开启，排盘仅限已开启的群聊；帮助可直接查看。
开启后可让LLM按明确资料排盘，或读取本人在本群最近一张盘进行解读。
最近命盘缓存有效期15分钟，仅保留于内存，关闭本群功能或重载后清除。
/紫微 {DEMO_BIRTH}
/紫微 农历 2023-闰02-16 12:00 女 真太阳时=关
/紫微 {DEMO_BIRTH} 经度=116.4 时区=8
/紫微 {DEMO_BIRTH} 流年=2026
/紫微 {DEMO_BIRTH} 流盘=2026-10-06 流时=10:00
以上日期均为虚构演示数据，请替换为需要排盘的出生资料。
末尾加「文字」返回完整文字盘；默认发送图片。别名 /紫微排盘、/ziwei。
支持 1900—2100 年，时间须明确填写，可用 HH:MM:SS 或十二时辰（子时以 00:00 代表）。
默认公历、UTC+8、东经120度，启用真太阳时近似校正。
可选：闰月=current|next|split，子时=next_day|midnight。
默认闰月十五／十六分界、晚子时换日。出生资料不写入永久命例库。"""


@dataclass(frozen=True)
class ChartRequest:
    birth: BirthInput
    profile: RuleProfile
    output: str
    flow_year: int | None = None
    flow_target: datetime | None = None


def parse_date(text: str, lunar=False):
    leap = "闰" in text
    if leap and not lunar:
        raise ValueError("闰月标记只能用于农历")
    clean = text.replace("闰", "")
    match = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", clean)
    if not match:
        match = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", clean)
    if not match:
        raise ValueError("日期格式应为 YYYY-MM-DD 或 YYYYMMDD")
    return (*map(int, match.groups()), leap)


def parse_clock(text: str):
    branches = "子丑寅卯辰巳午未申酉戌亥"
    if text.removesuffix("时") in branches and len(text.removesuffix("时")) == 1:
        return branches.index(text[0]) * 2, 0, 0
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if not match:
        raise ValueError("时刻应为 HH:MM、HH:MM:SS 或十二时辰")
    hour, minute, second = (int(match[1]), int(match[2]), int(match[3] or 0))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59 or not 0 <= second <= 59:
        raise ValueError("时刻超出有效范围")
    return hour, minute, second


def parse_request(text: str, config=None) -> ChartRequest:
    config = config or {}
    if len(text) > 1024:
        raise ValueError("指令过长")
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise ValueError("指令中存在未闭合的引号") from exc
    if not tokens:
        raise ValueError("请填写出生日期、时刻和性别")
    calendar = "solar"
    if tokens[0] in {"公历", "阳历", "solar", "农历", "阴历", "lunar"}:
        calendar = "lunar" if tokens.pop(0) in {"农历", "阴历", "lunar"} else "solar"
    if len(tokens) < 3:
        raise ValueError(f"请完整填写出生日期、时刻和性别，例如：{DEMO_BIRTH}")
    year, month, day, leap = parse_date(tokens[0], calendar == "lunar")
    hour, minute, second = parse_clock(tokens[1])
    sex = {"male": "男", "female": "女", "m": "男", "f": "女"}.get(
        tokens[2].lower(), tokens[2]
    )
    options = {}
    output = str(config.get("output_mode", "image"))
    for token in tokens[3:]:
        if token in {"图片", "image", "文字", "text"}:
            key, value = "输出", "text" if token in {"文字", "text"} else "image"
        else:
            if "=" not in token:
                raise ValueError(f"无法识别参数「{token}」，选项请使用 名称=值")
            key, value = token.split("=", 1)
        if key in options:
            raise ValueError(f"参数「{key}」重复")
        if key not in {
            "输出",
            "经度",
            "时区",
            "真太阳时",
            "闰月",
            "子时",
            "流年",
            "流盘",
            "流时",
        }:
            raise ValueError(f"不支持参数「{key}」")
        options[key] = value
    if "输出" in options:
        output = {"文字": "text", "图片": "image"}.get(options["输出"], options["输出"])
    if output not in {"image", "text"}:
        raise ValueError("输出须为 image／图片或 text／文字")
    solar_option = options.get("真太阳时")
    if solar_option is None:
        true_solar = config.get("true_solar", True)
    elif solar_option in {"开", "是", "true", "1"}:
        true_solar = True
    elif solar_option in {"关", "否", "false", "0"}:
        true_solar = False
    else:
        raise ValueError("真太阳时选项应为开或关")
    try:
        timezone = float(options.get("时区", config.get("timezone", 8)))
        longitude = float(options.get("经度", config.get("longitude", 120)))
    except (ValueError, TypeError) as exc:
        raise ValueError("经度和时区应为数字") from exc
    profile = RuleProfile.from_config(config)
    changes = {}
    if "闰月" in options:
        changes["leap_month_rule"] = {
            "本月": "current",
            "下月": "next",
            "分界": "split",
        }.get(options["闰月"], options["闰月"])
    if "子时" in options:
        changes["late_zi_rule"] = {"换日": "next_day", "零点": "midnight"}.get(
            options["子时"], options["子时"]
        )
    profile = replace(profile, **changes)
    birth = BirthInput(
        year,
        month,
        day,
        hour,
        minute,
        sex,
        calendar,
        leap,
        second,
        timezone,
        longitude,
        true_solar,
    )
    flow_year = flow_target = None
    if "流年" in options and "流盘" in options:
        raise ValueError("流年与流盘请选择其中一种")
    if "流时" in options and "流盘" not in options:
        raise ValueError("流时须与流盘日期一起使用")
    if "流年" in options:
        if not re.fullmatch(r"\d{4}", options["流年"]):
            raise ValueError("流年须为四位农历年份")
        flow_year = int(options["流年"])
    if "流盘" in options:
        y, m, d, _ = parse_date(options["流盘"])
        fh, fm, fs = parse_clock(options.get("流时", "12:00"))
        try:
            flow_target = datetime(y, m, d, fh, fm, fs)
        except ValueError as exc:
            raise ValueError("流盘日期不存在") from exc
        if not 1900 <= y <= 2100:
            raise ValueError("流盘日期支持 1900—2100")
        if true_solar:
            utc = flow_target - timedelta(hours=timezone)
            correction = 4 * (longitude - 15 * timezone) + equation_of_time(utc)
            flow_target += timedelta(seconds=round(correction * 60))
    return ChartRequest(birth, profile, output, flow_year, flow_target)
