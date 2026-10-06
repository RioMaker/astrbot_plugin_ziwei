"""Immutable rule choices; numeric tables preserve one-based source indices."""

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

STEMS = "甲乙丙丁戊己庚辛壬癸"
BRANCHES = "子丑寅卯辰巳午未申酉戌亥"
PALACES = (
    "命宫",
    "兄弟",
    "夫妻",
    "子女",
    "财帛",
    "疾厄",
    "迁移",
    "交友",
    "官禄",
    "田宅",
    "福德",
    "父母",
)
HUA = ("", "禄", "权", "科", "忌")
BUREAUS = {2: "水二局", 3: "木三局", 4: "金四局", 5: "土五局", 6: "火六局"}
RULES_VERSION = "ziwei-1.0-wm259-formulas-lunar148"
_DATA = Path(__file__).resolve().parent / "data"
TABLES = json.loads((_DATA / "rules.json").read_text(encoding="utf-8"))
STAR_NAMES = {
    int(k): v
    for k, v in json.loads((_DATA / "stars.json").read_text(encoding="utf-8")).items()
}


def wrap(value: int, size: int = 12) -> int:
    return (value - 1) % size + 1


@dataclass(frozen=True)
class RuleProfile:
    leap_month_rule: str = "split"
    late_zi_rule: str = "next_day"
    brightness: str = "qs"
    kuiyue: int = 1
    void_rule: int = 2
    tianma: str = "year"
    life_master: str = "palace"
    sky_hour: bool = False
    swap_injury: bool = False
    growth_forward: bool = False
    earth_growth: str = "water"
    annual_hua: str = "year"
    minor_stars: bool = True
    jia: int = 1
    wu: int = 1
    geng: int = 1
    xin: int = 1
    ren: int = 1
    gui: int = 1

    def __post_init__(self):
        choices = {
            "leap_month_rule": {"current", "next", "split"},
            "late_zi_rule": {"next_day", "midnight"},
            "brightness": {"qs", "zz", "xd1", "xd2"},
            "kuiyue": {1, 2, 3, 4},
            "void_rule": {1, 2, 3},
            "tianma": {"year", "month"},
            "life_master": {"palace", "year"},
            "earth_growth": {"water", "fire"},
            "annual_hua": {"year", "palace"},
            "jia": {1, 2},
            "wu": {1, 2},
            "geng": {1, 2, 3, 4, 5},
            "xin": {1, 2},
            "ren": {1, 2, 3},
            "gui": {1, 2},
        }
        for key, allowed in choices.items():
            value = getattr(self, key)
            expected_type = type(next(iter(allowed)))
            if type(value) is not expected_type or value not in allowed:
                raise ValueError(f"规则 {key} 无效")
        for key in ("sky_hour", "swap_injury", "growth_forward", "minor_stars"):
            if not isinstance(getattr(self, key), bool):
                raise ValueError(f"规则 {key} 必须为布尔值")

    @classmethod
    def from_config(cls, config):
        keys = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in config.items() if key in keys})

    def snapshot(self):
        return asdict(self)

    def four_hua(self, stem: int) -> tuple[int, ...]:
        variants = {
            1: (self.jia, ("JLPWY", "JLPQY")),
            5: (self.wu, ("WTYYOUJ", "WTYYANGJ")),
            7: (self.geng, ("GYWYT", "GYWTY", "GYWFT", "GYWFX", "GYWTX")),
            8: (self.xin, ("XJYQC", "XJYWC")),
            9: (self.ren, ("RLZFW", "RLZFW2", "RLZXW")),
            10: (self.gui, ("GPJYT1", "GPJYT2")),
        }
        if stem in variants:
            selection, names = variants[stem]
            return tuple(TABLES["_AX_SHB_ARR_" + names[selection - 1]][1:])
        return tuple(TABLES["_AX_SHB_ARR"][stem][1:])

    def effective_month(self, month: int, day: int, leap: bool) -> int:
        advance = self.leap_month_rule == "next" or (
            self.leap_month_rule == "split" and day > 15
        )
        return min(12, month + int(leap and advance))
