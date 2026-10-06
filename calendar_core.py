"""Independent calendar adapter. Solar-time correction is a Meeus approximation."""

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from lunar_python import Lunar, LunarMonth, Solar

from .rules import BRANCHES, STEMS, RuleProfile, wrap


@dataclass(frozen=True)
class BirthInput:
    year: int
    month: int
    day: int
    hour: int
    minute: int
    sex: str
    calendar: str = "solar"
    leap: bool = False
    second: int = 0
    timezone: float = 8.0
    longitude: float = 120.0
    true_solar: bool = True

    def __post_init__(self):
        if type(self.leap) is not bool or type(self.true_solar) is not bool:
            raise ValueError("闰月标记和真太阳时开关必须为布尔值")
        if self.sex not in {"男", "女"}:
            raise ValueError("性别须明确填写男或女")
        if self.calendar not in {"solar", "lunar"}:
            raise ValueError("历法须为公历或农历")
        if not 1900 <= self.year <= 2100:
            raise ValueError("出生年份支持 1900—2100")
        if not (
            0 <= self.hour <= 23 and 0 <= self.minute <= 59 and 0 <= self.second <= 59
        ):
            raise ValueError("出生时刻无效，请使用 HH:MM 或 HH:MM:SS")
        if not math.isfinite(self.timezone) or not -12 <= self.timezone <= 14:
            raise ValueError("时区须在 UTC-12 至 UTC+14 之间")
        if not math.isfinite(self.longitude) or not -180 <= self.longitude <= 180:
            raise ValueError("经度须在 -180 至 180 之间，东经为正")
        if self.leap and self.calendar != "lunar":
            raise ValueError("闰月标记只能用于农历日期")
        if self.calendar == "solar":
            try:
                datetime(self.year, self.month, self.day)
            except ValueError as exc:
                raise ValueError("公历日期不存在") from exc
        else:
            if not 1 <= self.month <= 12:
                raise ValueError("农历月份须在 1—12 之间")
            month = LunarMonth.fromYm(
                self.year, -self.month if self.leap else self.month
            )
            if month is None or not 1 <= self.day <= month.getDayCount():
                raise ValueError("农历日期或闰月不存在")

    def civil_datetime(self) -> datetime:
        if self.calendar == "solar":
            return datetime(
                self.year, self.month, self.day, self.hour, self.minute, self.second
            )
        lunar = Lunar.fromYmdHms(
            self.year,
            -self.month if self.leap else self.month,
            self.day,
            self.hour,
            self.minute,
            self.second,
        )
        solar = lunar.getSolar()
        return datetime(
            solar.getYear(),
            solar.getMonth(),
            solar.getDay(),
            self.hour,
            self.minute,
            self.second,
        )


def solar_at(value: datetime):
    return Solar.fromYmdHms(
        value.year, value.month, value.day, value.hour, value.minute, value.second
    )


def equation_of_time(utc: datetime) -> float:
    """Equation of time in minutes, using Julian centuries and solar elements."""
    jd = (utc - datetime(2000, 1, 1, 12)).total_seconds() / 86400 + 2451545
    t = (jd - 2451545) / 36525
    l0 = math.radians((280.46646 + t * (36000.76983 + t * 0.0003032)) % 360)
    anomaly = math.radians(357.52911 + t * (35999.05029 - 0.0001537 * t))
    eccentricity = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    seconds = 21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))
    obliquity = 23 + (26 + seconds / 60) / 60
    obliquity += 0.00256 * math.cos(math.radians(125.04 - 1934.136 * t))
    y = math.tan(math.radians(obliquity) / 2) ** 2
    e = eccentricity
    angle = (
        y * math.sin(2 * l0)
        - 2 * e * math.sin(anomaly)
        + 4 * e * y * math.sin(anomaly) * math.cos(2 * l0)
        - 0.5 * y * y * math.sin(4 * l0)
        - 1.25 * e * e * math.sin(2 * anomaly)
    )
    return 4 * math.degrees(angle)


def normalize(birth: BirthInput, profile: RuleProfile) -> dict:
    civil = birth.civil_datetime()
    utc = civil - timedelta(hours=birth.timezone)
    correction = (
        4 * (birth.longitude - 15 * birth.timezone) + equation_of_time(utc)
        if birth.true_solar
        else 0
    )
    computed = civil + timedelta(seconds=round(correction * 60))
    if not 1900 <= computed.year <= 2100:
        raise ValueError("校正后的日期超出 1900—2100 支持范围")
    actual = solar_at(computed).getLunar()
    # Chinese lunar dates and late-Zi rollover use the full calendar conversion,
    # so 29/30-day, leap-month and lunar-year boundaries remain valid.
    star_date = (
        computed + timedelta(days=1)
        if computed.hour == 23 and profile.late_zi_rule == "next_day"
        else computed
    )
    star_lunar = solar_at(star_date).getLunar()
    # Term instants in lunar-python are Beijing time. Year/month pillars must
    # use the same UT instant; solar correction only changes day/hour pillars.
    term_lunar = solar_at(utc + timedelta(hours=8)).getLunar()
    day_pillar = (
        actual.getDayInGanZhiExact()
        if profile.late_zi_rule == "next_day"
        else actual.getDayInGanZhiExact2()
    )
    day_stem = STEMS.index(day_pillar[0]) + 1
    hour_branch = (computed.hour + 1) // 2 % 12 + 1
    hour_stem = wrap((day_stem - 1) % 5 * 2 + hour_branch, 10)
    pillars = [
        term_lunar.getYearInGanZhiExact(),
        term_lunar.getMonthInGanZhiExact(),
        day_pillar,
        STEMS[hour_stem - 1] + BRANCHES[hour_branch - 1],
    ]
    return {
        "original": asdict(birth),
        "civil": civil.isoformat(sep=" "),
        "computed": computed.isoformat(sep=" "),
        "correction_minutes": round(correction, 4),
        "true_solar": birth.true_solar,
        "lunar": {
            "year": actual.getYear(),
            "month": abs(actual.getMonth()),
            "day": actual.getDay(),
            "leap": actual.getMonth() < 0,
            "text": actual.toString(),
        },
        "astrology_lunar": {
            "year": star_lunar.getYear(),
            "month": abs(star_lunar.getMonth()),
            "day": star_lunar.getDay(),
            "leap": star_lunar.getMonth() < 0,
        },
        "month": profile.effective_month(
            abs(star_lunar.getMonth()), star_lunar.getDay(), star_lunar.getMonth() < 0
        ),
        "day": star_lunar.getDay(),
        "hour_branch": hour_branch,
        "year_stem": (star_lunar.getYear() - 4) % 10 + 1,
        "year_branch": (star_lunar.getYear() - 4) % 12 + 1,
        "pillars": pillars,
    }
