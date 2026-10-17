"""Civil-clock candidate ranges for administrator-led birth-time comparison."""

import re
import shlex
from dataclasses import dataclass

from .commands import ChartRequest, parse_request
from .rules import BRANCHES

_CALENDARS = {"公历", "阳历", "solar", "农历", "阴历", "lunar"}
_OPTIONS = {"经度", "时区", "真太阳时", "闰月", "子时", "主题"}
_FLOW_OPTIONS = {"流年", "流盘", "流时", "运限", "大限", "大运"}


@dataclass(frozen=True)
class RectificationRequest:
    request: ChartRequest
    intervals: tuple[tuple[int, int], ...]
    description: str
    metadata: dict


def _intervals(branches):
    intervals = []
    for branch in branches:
        index = BRANCHES.index(branch)
        if index == 0:
            intervals.extend(((0, 3600), (23 * 3600, 24 * 3600)))
        else:
            intervals.append(((index * 2 - 1) * 3600, (index * 2 + 1) * 3600))
    merged = []
    for start, end in sorted(intervals):
        if merged and merged[-1][1] == start:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return tuple(merged)


def parse_rectification_request(text: str, config=None) -> RectificationRequest:
    """Keep date and rule parsing shared; a branch denotes a civil-clock range."""
    if len(text) > 1024:
        raise ValueError("定盘指令过长")
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise ValueError("定盘指令中存在未闭合的引号") from exc
    calendar = tokens.pop(0) if tokens and tokens[0] in _CALENDARS else "公历"
    if len(tokens) < 2:
        raise ValueError("请填写出生日期和性别，可附时辰=辰,巳,午；不填时辰表示全天")
    date, sex, *extras = tokens
    branches = tuple(BRANCHES)
    options = []
    seen = set()
    for token in extras:
        if "=" not in token:
            raise ValueError("定盘选项请使用 名称=值，候选时辰请用 时辰=辰,巳,午")
        key, value = token.split("=", 1)
        if key in _FLOW_OPTIONS:
            raise ValueError("定盘候选仅使用出生日期和时辰，不能混用流年、流盘或运限")
        if key in seen:
            raise ValueError(f"参数「{key}」重复")
        seen.add(key)
        if key == "时辰":
            parts = re.split(r"[,，、]", value)
            branches = tuple(part.removesuffix("时") for part in parts)
            if not branches or any(
                branch not in BRANCHES or len(branch) != 1 for branch in branches
            ):
                raise ValueError(
                    "时辰须为子至亥的时辰名，以逗号分隔，例如 时辰=辰,巳,午"
                )
            if len(set(branches)) != len(branches):
                raise ValueError("候选时辰不能重复")
            branches = tuple(branch for branch in BRANCHES if branch in branches)
        elif key in _OPTIONS:
            options.append(token)
        else:
            raise ValueError(f"定盘不支持参数「{key}」")
    # Noon validates the shared birth input; it is not itself a candidate choice.
    request = parse_request(
        shlex.join([calendar, date, "12:00", sex, *options]), dict(config or {})
    )
    interval_list = _intervals(branches)
    selection = "全天" if len(branches) == 12 else "、".join(branches) + "时"
    calendar_label = "农历" if request.birth.calendar == "lunar" else "公历"
    description = (
        f"{calendar_label} {date}，当地钟表时间{selection}；候选时刻仅代表范围"
    )
    metadata = {
        "schema_version": "ziwei-rectification-input-v1",
        "time_basis": "civil_clock",
        "selected_branches": list(branches),
        "selection": selection,
        "calendar": request.birth.calendar,
        "timezone": request.birth.timezone,
        "longitude": request.birth.longitude,
        "true_solar": request.birth.true_solar,
        "image_theme": request.image_theme,
        "profile": request.profile.snapshot(),
        "interval_semantics": (
            "当地钟表时间，起点包含、终点不包含；子时含当天早子和晚子"
        ),
        "representative_only": True,
    }
    return RectificationRequest(request, interval_list, description, metadata)
