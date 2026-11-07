"""Explicit civil/lunar date spellings, independent of time correction."""

import re

CALENDAR_NAMES = {
    "公历": "solar",
    "阳历": "solar",
    "solar": "solar",
    "农历": "lunar",
    "阴历": "lunar",
    "lunar": "lunar",
}
CALENDAR_PATTERN = "(?:" + "|".join(CALENDAR_NAMES) + ")"
NAMED_DATE_PATTERN = (
    r"[0-9]{4}年闰?[0-9一二三四五六七八九十正冬腊]+月"
    r"[0-9一二三四五六七八九十初廿卅]+[日号]"
)
_DIGITS = "一二三四五六七八九"
_MONTHS = {name: index for index, name in enumerate(_DIGITS, 1)}
_MONTHS.update({"十": 10, "十一": 11, "十二": 12, "正": 1, "冬": 11, "腊": 12})
_DAYS = {name: index for index, name in enumerate(_DIGITS, 1)}
_DAYS.update({"初" + name: index for index, name in enumerate(_DIGITS, 1)})
_DAYS.update({"十" + name: 10 + index for index, name in enumerate(_DIGITS, 1)})
_DAYS.update({"二十" + name: 20 + index for index, name in enumerate(_DIGITS, 1)})
_DAYS.update({"廿" + name: 20 + index for index, name in enumerate(_DIGITS, 1)})
_DAYS.update({"十": 10, "初十": 10, "二十": 20, "廿": 20, "三十": 30, "卅": 30})


def calendar_name(text: str) -> str:
    try:
        return CALENDAR_NAMES[text.lower()]
    except KeyError as exc:
        raise ValueError("历法须为公历／阳历或农历／阴历") from exc


def parse_date(text: str, lunar=False):
    """Parse an explicit date; BirthInput validates the actual month/day."""
    named = re.fullmatch(r"([0-9]{4})年(闰?)([^月]+)月([^日号]+)[日号]", text)
    if named:
        year, leap, month, day = named.groups()
        if not lunar and (not month.isdecimal() or not day.isdecimal()):
            raise ValueError("公历年月日请使用数字；中文农历日期须注明农历／阴历")
        month = int(month) if month.isdecimal() else _MONTHS.get(month)
        day = int(day) if day.isdecimal() else _DAYS.get(day)
        if month is None or day is None:
            raise ValueError("农历月日写法无效，如 1990年五月廿三日")
        year, leap = int(year), bool(leap)
    else:
        match = re.fullmatch(r"([0-9]{4})[-/](闰?)([0-9]{1,2})[-/]([0-9]{1,2})", text)
        if not match:
            match = re.fullmatch(r"([0-9]{4})(闰?)([0-9]{2})([0-9]{2})", text)
        if not match:
            raise ValueError("日期请填 YYYYMMDD、YYYY-MM-DD 或 YYYY年M月D日")
        year, leap, month, day = match.groups()
        year, month, day, leap = int(year), int(month), int(day), bool(leap)
    if leap and not lunar:
        raise ValueError("闰月标记只能用于农历")
    return year, month, day, leap
