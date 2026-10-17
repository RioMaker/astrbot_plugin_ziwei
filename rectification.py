"""Partition clock ranges by real calendar state without changing the engine."""

import json
import math
from dataclasses import replace
from datetime import datetime, timedelta

from .calendar_core import equation_of_time, solar_at
from .engine import build_chart
from .rectification_commands import RectificationRequest
from .rules import BRANCHES

_DAY = 86400
_LIMIT = 32


def _computed_clock(midnight, second, birth):
    civil = midnight + timedelta(seconds=second)
    if not birth.true_solar:
        return civil
    utc = civil - timedelta(hours=birth.timezone)
    correction = 4 * (birth.longitude - 15 * birth.timezone) + equation_of_time(utc)
    return civil + timedelta(seconds=round(correction * 60))


def _clock_boundaries(midnight, birth):
    """Invert the monotone, rounded solar-clock map at each state boundary.

    Hour branches change at odd hours; midnight also changes the actual lunar
    date and the midnight-rule day pillar. Late-Zi rollover is covered by 23:00.
    Equation-of-time drift is tiny relative to one civil second, so this map is
    nondecreasing even when rounding produces an occasional repeated second.
    """
    first = _computed_clock(midnight, 0, birth)
    last = _computed_clock(midnight, _DAY, birth)
    boundaries = {0, _DAY}
    day = first.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= last:
        for hour in (0, *range(1, 24, 2)):
            target = day + timedelta(hours=hour)
            if not first < target <= last:
                continue
            low, high = 0, _DAY
            while low < high:
                middle = (low + high) // 2
                if _computed_clock(midnight, middle, birth) < target:
                    low = middle + 1
                else:
                    high = middle
            boundaries.add(low)
        day += timedelta(days=1)
    return boundaries


def _term_boundaries(midnight, birth):
    """Year/month pillars use UT converted to Beijing time, not solar time."""
    offset = timedelta(hours=birth.timezone)
    beijing = midnight - offset + timedelta(hours=8)
    table = solar_at(beijing).getLunar().getJieQiTable()
    boundaries = set()
    for term in table.values():
        instant = (
            datetime(
                term.getYear(),
                term.getMonth(),
                term.getDay(),
                term.getHour(),
                term.getMinute(),
                term.getSecond(),
            )
            + offset
            - timedelta(hours=8)
        )
        # BirthInput has whole-second precision; solar_at also drops fractional
        # seconds after timezone conversion. The first admitted second is ceil.
        second = math.ceil((instant - midnight).total_seconds())
        if 0 < second < _DAY:
            boundaries.add(second)
    return boundaries


def _state_key(chart):
    normalized = chart["normalized"]
    # Include every input consumed by build_natal, plus displayed calendar and
    # all pillars. Never merge just because the life palace happens to match.
    return json.dumps(
        {
            key: normalized[key]
            for key in (
                "lunar",
                "astrology_lunar",
                "month",
                "day",
                "hour_branch",
                "year_stem",
                "year_branch",
                "pillars",
            )
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _birth_at(birth, second):
    hour, rest = divmod(second, 3600)
    minute, second = divmod(rest, 60)
    return replace(birth, hour=hour, minute=minute, second=second)


def build_candidates(parsed: RectificationRequest) -> list[dict]:
    """Return all states in the selected half-open, whole-second clock ranges.

    Each complete engine chart uses a representative civil second. The explicit
    intervals remain the birth-time hypothesis; no exact birth time is claimed.
    If any selected interval is unsupported, reject the whole request.
    """
    birth, profile = parsed.request.birth, parsed.request.profile
    midnight = _birth_at(birth, 0).civil_datetime()
    intervals = parsed.intervals
    if not intervals or any(
        type(start) is not int or type(end) is not int or not 0 <= start < end <= _DAY
        for start, end in intervals
    ):
        raise ValueError("定盘时间范围无效")
    for start, end in intervals:
        for second in (start, end - 1):
            if not 1900 <= _computed_clock(midnight, second, birth).year <= 2100:
                raise ValueError(
                    "所选时间范围部分校正后超出1900—2100年，不能完整生成候选；请缩小时辰范围"
                )
    boundaries = _clock_boundaries(midnight, birth) | _term_boundaries(midnight, birth)
    candidates = {}
    for start, end in intervals:
        edges = [
            start,
            *sorted(point for point in boundaries if start < point < end),
            end,
        ]
        for left, right in zip(edges, edges[1:], strict=False):
            representative = (left + right - 1) // 2
            chart = build_chart(_birth_at(birth, representative), profile)
            state = _state_key(chart)
            interval = {
                "start": (midnight + timedelta(seconds=left)).isoformat(sep=" "),
                "end": (midnight + timedelta(seconds=right)).isoformat(sep=" "),
            }
            if state not in candidates:
                if len(candidates) >= _LIMIT:
                    raise ValueError("所选范围产生超过32个候选，请缩小候选时辰范围")
                candidates[state] = {
                    "id": f"c{len(candidates) + 1:02}",
                    "label": "",
                    "intervals": [],
                    "representative_time": chart["normalized"]["civil"],
                    "chart": chart,
                }
            current = candidates[state]
            if (
                current["intervals"]
                and current["intervals"][-1]["end"] == interval["start"]
            ):
                current["intervals"][-1]["end"] = interval["end"]
            else:
                current["intervals"].append(interval)
    for candidate in candidates.values():
        branch = BRANCHES[candidate["chart"]["normalized"]["hour_branch"] - 1]
        ranges = "、".join(
            f"{item['start']} 至 {item['end']}" for item in candidate["intervals"]
        )
        candidate["label"] = (
            f"计算{branch}时；钟表范围 {ranges}（终点不含，盘中时刻为代表值）"
        )
        candidate["chart"]["rectification"] = {
            "candidate_id": candidate["id"],
            "intervals": [dict(item) for item in candidate["intervals"]],
            "representative_only": True,
        }
    return list(candidates.values())
