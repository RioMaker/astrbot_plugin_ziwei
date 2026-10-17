"""Real-calendar candidate coverage; all birth inputs are fictional examples."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from astrbot_plugin_ziwei import calendar_core
from astrbot_plugin_ziwei.engine import build_chart
from astrbot_plugin_ziwei.rectification import build_candidates
from astrbot_plugin_ziwei.rectification_commands import parse_rectification_request


def _ranges(candidates):
    return sorted(
        (datetime.fromisoformat(item["start"]), datetime.fromisoformat(item["end"]))
        for candidate in candidates
        for item in candidate["intervals"]
    )


def _at(candidates, instant):
    matches = [
        candidate
        for candidate in candidates
        if any(
            datetime.fromisoformat(item["start"])
            <= instant
            < datetime.fromisoformat(item["end"])
            for item in candidate["intervals"]
        )
    ]
    assert len(matches) == 1
    return matches[0]


def test_full_day_keeps_both_zi_dates_at_lunar_new_year():
    parsed = parse_rectification_request("2024-02-09 女 真太阳时=关")
    assert parsed.intervals == ((0, 86400),)
    candidates = build_candidates(parsed)
    assert len(candidates) == 13
    early = _at(candidates, datetime(2024, 2, 9, 0, 30))
    late = _at(candidates, datetime(2024, 2, 9, 23, 30))
    assert early["id"] != late["id"]
    assert early["chart"]["normalized"]["hour_branch"] == 1
    assert late["chart"]["normalized"]["hour_branch"] == 1
    assert early["chart"]["normalized"]["astrology_lunar"]["year"] == 2023
    assert late["chart"]["normalized"]["astrology_lunar"]["year"] == 2024
    assert early["chart"]["stars"] != late["chart"]["stars"]
    ranges = _ranges(candidates)
    assert ranges[0][0] == datetime(2024, 2, 9)
    assert ranges[-1][1] == datetime(2024, 2, 10)
    assert all(
        left[1] == right[0] for left, right in zip(ranges, ranges[1:], strict=False)
    )
    assert all("代表值" in item["label"] for item in candidates)
    for candidate in candidates:
        assert candidate["chart"]["rectification"] == {
            "candidate_id": candidate["id"],
            "intervals": candidate["intervals"],
            "representative_only": True,
        }


def test_midnight_rule_can_merge_early_and_late_zi_without_losing_either_range():
    candidates = build_candidates(
        parse_rectification_request("1990-06-15 男 时辰=子 真太阳时=关 子时=midnight")
    )
    assert len(candidates) == 1
    assert candidates[0]["intervals"] == [
        {"start": "1990-06-15 00:00:00", "end": "1990-06-15 01:00:00"},
        {"start": "1990-06-15 23:00:00", "end": "1990-06-16 00:00:00"},
    ]


def test_lunar_leap_month_end_is_converted_before_zi_rollover():
    candidates = build_candidates(
        parse_rectification_request("农历 2023-闰02-29 男 时辰=子 真太阳时=关")
    )
    assert len(candidates) == 2
    early = candidates[0]["chart"]["normalized"]
    late = candidates[1]["chart"]["normalized"]
    assert early["original"]["calendar"] == "lunar"
    assert early["astrology_lunar"] == {
        "year": 2023,
        "month": 2,
        "day": 29,
        "leap": True,
    }
    assert late["astrology_lunar"] == {
        "year": 2023,
        "month": 3,
        "day": 1,
        "leap": False,
    }
    assert candidates[0]["intervals"][0]["start"].startswith("2023-04-19")


def test_late_zi_crosses_leap_month_fifteen_sixteen_rule_without_mutation():
    split = build_candidates(
        parse_rectification_request("农历 2023-闰02-15 女 时辰=子 真太阳时=关")
    )
    current = build_candidates(
        parse_rectification_request(
            "农历 2023-闰02-15 女 时辰=子 真太阳时=关 闰月=current"
        )
    )
    assert [item["chart"]["normalized"]["month"] for item in split] == [2, 3]
    assert [item["chart"]["normalized"]["month"] for item in current] == [2, 2]
    assert split[1]["chart"]["stars"] != current[1]["chart"]["stars"]
    assert split[1]["chart"]["normalized"]["original"]["day"] == 15


@pytest.mark.parametrize(
    "options,branch,boundary",
    [
        ("真太阳时=关 时区=8", "申", datetime(2024, 2, 4, 16, 27, 7)),
        ("经度=116.4 时区=8", "申", datetime(2024, 2, 4, 16, 27, 7)),
        ("真太阳时=关 时区=0", "辰", datetime(2024, 2, 4, 8, 27, 7)),
        ("真太阳时=关 时区=8.0001", "申", datetime(2024, 2, 4, 16, 27, 8)),
    ],
)
def test_lichun_splits_same_hour_at_same_ut_instant(options, branch, boundary):
    candidates = build_candidates(
        parse_rectification_request(f"2024-02-04 女 时辰={branch} {options}")
    )
    before = _at(candidates, boundary - timedelta(seconds=1))
    after = _at(candidates, boundary)
    assert before["id"] != after["id"]
    assert before["chart"]["normalized"]["pillars"][:2] == ["癸卯", "乙丑"]
    assert after["chart"]["normalized"]["pillars"][:2] == ["甲辰", "丙寅"]
    assert any(start == boundary for start, _ in _ranges(candidates))


@pytest.mark.parametrize(
    "options",
    [
        "经度=73.5 时区=8",
        "经度=179 时区=8 子时=midnight",
        "经度=-179 时区=14",
    ],
)
def test_solar_clock_crossing_dates_covers_boundary_seconds_and_interior(options):
    parsed = parse_rectification_request(f"1990-06-15 男 {options}")
    candidates = build_candidates(parsed)
    midnight = datetime(1990, 6, 15)
    samples = {midnight + timedelta(minutes=30 * index) for index in range(48)}
    ranges = _ranges(candidates)
    assert ranges[0][0] == midnight
    assert ranges[-1][1] == midnight + timedelta(days=1)
    assert all(
        left[1] == right[0] for left, right in zip(ranges, ranges[1:], strict=False)
    )
    for start, end in ranges:
        samples.update((start, end - timedelta(seconds=1)))
    assert any(
        item["chart"]["normalized"]["computed"][:10] != "1990-06-15"
        for item in candidates
    )
    for instant in samples:
        representative = _at(candidates, instant)["chart"]
        actual = build_chart(
            replace(
                parsed.request.birth,
                hour=instant.hour,
                minute=instant.minute,
                second=instant.second,
            ),
            parsed.request.profile,
        )
        metadata = {"normalized", "rectification"}
        assert {k: v for k, v in actual.items() if k not in metadata} == {
            k: v for k, v in representative.items() if k not in metadata
        }
        for key in ("lunar", "astrology_lunar", "pillars", "hour_branch"):
            assert actual["normalized"][key] == representative["normalized"][key]


def test_selected_civil_hours_can_split_after_solar_correction():
    parsed = parse_rectification_request("1990-06-15 男 时辰=辰 经度=116.4")
    candidates = build_candidates(parsed)
    assert parsed.intervals == ((7 * 3600, 9 * 3600),)
    assert len(candidates) == 2
    assert {item["chart"]["normalized"]["hour_branch"] for item in candidates} == {4, 5}
    ranges = _ranges(candidates)
    assert ranges[0][0] == datetime(1990, 6, 15, 7)
    assert ranges[-1][1] == datetime(1990, 6, 15, 9)


@pytest.mark.parametrize(
    "text",
    ["1900-01-01 男 经度=0", "2100-12-31 女 经度=180"],
)
def test_partly_unsupported_day_rejects_whole_batch(text):
    with pytest.raises(ValueError, match="不能完整生成候选"):
        build_candidates(parse_rectification_request(text))


def test_valid_subset_at_supported_lower_boundary_is_allowed():
    candidates = build_candidates(
        parse_rectification_request("1900-01-01 男 经度=0 时辰=午")
    )
    assert candidates
    assert all(
        item["chart"]["normalized"]["computed"].startswith("1900-01-01")
        for item in candidates
    )


def test_request_and_charts_do_not_share_mutable_config_or_candidate_data():
    config = {
        "timezone": 9,
        "longitude": 135,
        "true_solar": False,
        "late_zi_rule": "midnight",
        "leap_month_rule": "next",
        "geng": 3,
        "image_theme": "night",
    }
    snapshot = deepcopy(config)
    parsed = parse_rectification_request(
        "19900615 男 时辰=巳，辰时、子 主题=白天", config
    )
    assert parsed.metadata["selected_branches"] == ["子", "辰", "巳"]
    assert parsed.intervals == ((0, 3600), (7 * 3600, 11 * 3600), (23 * 3600, 86400))
    assert parsed.request.image_theme == "day"
    assert parsed.metadata["image_theme"] == "day"
    assert parsed.request.profile.geng == 3
    candidates = build_candidates(parsed)
    first_copy = deepcopy(candidates[1])
    candidates[0]["chart"]["stars"][0]["name"] = "changed locally"
    assert candidates[1] == first_copy
    assert config == snapshot
    other = parse_rectification_request("19900615 男")
    assert other.request.birth.timezone == 8
    assert other.request.profile.geng == 1
    assert other.request.profile.late_zi_rule == "next_day"


def test_full_day_does_not_convert_every_second_with_lunar_library(monkeypatch):
    original = calendar_core.solar_at
    calls = []

    def counted(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(calendar_core, "solar_at", counted)
    candidates = build_candidates(parse_rectification_request("1990-06-15 男"))
    assert 12 <= len(candidates) <= 32
    assert len(calls) < 100


@pytest.mark.parametrize(
    "extra",
    ["流年=2026", "流盘=2026-01-01", "流时=午", "运限=0", "大限=2", "大运=3"],
)
def test_flow_options_are_explicitly_rejected(extra):
    with pytest.raises(ValueError, match="不能混用"):
        parse_rectification_request(f"1990-06-15 男 {extra}")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "19900615",
        "19900615 未知",
        "199006150830 男",
        "1991-02-29 男",
        "19900615 男 时辰=",
        "19900615 男 时辰=子,",
        "19900615 男 时辰=子,子时",
        "19900615 男 时辰=子 时辰=午",
        "19900615 男 时辰=00:00",
        "19900615 男 时辰=子丑",
        "19900615 男 时辰=全天",
        "19900615 男 08:30",
        "19900615 男 输出=图片",
        "19900615 男 foo=bar",
        "19900615 男 子时=unknown",
        "19900615 男 经度=nan",
        '19900615 男 "',
    ],
)
def test_invalid_inputs_do_not_invent_a_time_or_drop_an_option(text):
    with pytest.raises(ValueError):
        parse_rectification_request(text)
