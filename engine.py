"""Pure natal and flow calculations derived from the documented formulas."""

from copy import deepcopy
from datetime import datetime, timedelta

from .calendar_core import BirthInput, normalize, solar_at
from .rules import (
    BRANCHES,
    BUREAUS,
    HUA,
    PALACES,
    RULES_VERSION,
    STAR_NAMES,
    STEMS,
    TABLES,
    RuleProfile,
    wrap,
)


def hua_for(star_id, stem, profile):
    row = profile.four_hua(stem)
    return HUA[row.index(star_id) + 1] if star_id in row else ""


def _brightness(star_id, branch, profile):
    table = TABLES["_AX_SB_" + profile.brightness.upper()]
    grade = table[star_id][branch]
    labels = TABLES[
        "_StarBN_ZZ_CHS" if profile.brightness in {"zz", "xd2"} else "_StarBN_QS_CHS"
    ]
    return labels[grade] if grade else ""


def build_chart(birth: BirthInput, profile: RuleProfile | None = None) -> dict:
    profile = profile or RuleProfile()
    normalized = normalize(birth, profile)
    return build_natal(normalized, birth.sex, profile)


def build_natal(normalized: dict, sex: str, profile: RuleProfile) -> dict:
    m, d, h = normalized["month"], normalized["day"], normalized["hour_branch"]
    yg, yz = normalized["year_stem"], normalized["year_branch"]
    if sex not in {"男", "女"} or not (
        1 <= m <= 12
        and 1 <= d <= 30
        and 1 <= h <= 12
        and 1 <= yg <= 10
        and 1 <= yz <= 12
    ):
        raise ValueError("归一化后的排盘参数无效")
    life, body = wrap(m - h + 3), wrap(m + h + 1)
    tiger_stem = TABLES["_WHD"][yg]
    stems = {
        b: wrap(tiger_stem + (b - 1 if b < 3 else b - 3), 10) for b in range(1, 13)
    }
    bureau = TABLES["_AX_WXJS_ARR"][(yg - 1) % 5 + 1][(life + 1) // 2]
    direction = 1 if (yg % 2 == 1) == (sex == "男") else -1
    palaces = [
        {
            "branch": b,
            "branch_name": BRANCHES[b - 1],
            "stem": stems[b],
            "stem_name": STEMS[stems[b] - 1],
            "name": PALACES[wrap(life - b + 1) - 1],
            "body": b == body,
            "stars": [],
            "sequences": {},
            "flow_names": {},
        }
        for b in range(1, 13)
    ]
    stars = []

    def add(sid, branch, secondary=False):
        branch = wrap(branch)
        hua = {}
        outward = inward = ""
        if sid <= 28:
            for layer, stem in (
                ("生年", yg),
                ("命宫", stems[life]),
                ("日干", STEMS.index(normalized["pillars"][2][0]) + 1),
            ):
                value = hua_for(sid, stem, profile)
                if value:
                    hua[layer] = value
            outward = hua_for(sid, stems[branch], profile)
            inward = hua_for(sid, stems[wrap(branch + 6)], profile)
        star = {
            "instance_id": f"natal-{sid}-{'secondary' if secondary else 'primary'}",
            "id": sid,
            "name": STAR_NAMES[sid],
            "branch": branch,
            "group": "major" if sid <= 14 else "assistant" if sid <= 28 else "minor",
            "brightness": _brightness(sid, branch, profile),
            "secondary": secondary,
            "hua": hua,
            "self_hua": {"outward": outward, "inward": inward},
        }
        stars.append(star)
        palaces[branch - 1]["stars"].append(star)
        return branch

    padding = (-d) % bureau
    purple = wrap((d + padding) // bureau + (-padding if padding % 2 else padding) + 2)
    treasury = wrap(6 - purple)
    for sid, offset in enumerate((0, -1, -3, -4, -5, -8), 1):
        add(sid, purple + offset)
    for sid, offset in enumerate((0, 1, 2, 3, 4, 5, 6, 10), 7):
        add(sid, treasury + offset)
    chang, qu = TABLES["_AX_CQ_ARR"][h]
    zuo, you = TABLES["_AX_ZY_ARR"][m]
    for sid, b in ((15, chang), (16, qu), (17, zuo), (18, you)):
        add(sid, b)
    for sid, b in zip((19, 20), TABLES[f"_AX_KY_ARR{profile.kuiyue}"][yg], strict=True):
        add(sid, b)
    lu = add(21, TABLES["_AX_LC_ARR"][yg])
    add(22, TABLES["_AX_TM_ARR"][yz if profile.tianma == "year" else wrap(m + 2)])
    for sid, b in zip((23, 24), TABLES["_AX_HL_ARR"][yz], strict=True):
        add(sid, b + h - 1)
    for sid, b in ((25, lu + 1), (26, lu - 1), (27, 13 - h), (28, h - 1)):
        add(sid, b)

    if profile.minor_stars:
        positions = {
            33: zuo + d - 1,
            32: you - d + 1,
            52: chang + d - 2,
            42: qu + d - 2,
            34: life + yz - 1,
            43: body + yz - 1,
            51: 5 - yz,
            40: 11 - yz,
            60: yz + 4,
            58: 12 - yz,
            39: 8 - yz,
            41: yz + 6,
            31: m + 9,
            38: m + 1,
            47: h + 6,
            50: h + 2,
            37: yz + 1 + (h - 1 if profile.sky_hour else 0),
            65: life + 5,
            64: life - 5,
            45: yz + 9,
            46: yz + 5,
            81: yz + 7,
            79: 12 - yz,
            62: yz + 6 + (1 if yz % 2 else -1),
        }
        if profile.swap_injury and direction == -1:
            positions[65], positions[64] = positions[64], positions[65]
        for sid, b in sorted(positions.items()):
            add(sid, b)
        lookups = {
            44: ("TC", yg),
            53: ("PS", yz),
            59: ("FL", yz),
            54: ("YS", m),
            35: ("TY", m),
            36: ("TW", m),
            56: ("JS", m),
            82: ("XC", yz),
            55: ("HG", yz),
        }
        for sid, (table, index) in lookups.items():
            add(sid, TABLES[f"_AX_{table}_ARR"][index])
        add(80, TABLES["_AX_HG_ARR"][yz] + 1)
        for pair, table, index in (((30, 29), "FG", yg), ((48, 57), "GG", yz)):
            for sid, b in zip(pair, TABLES[f"_AX_{table}_ARR"][index], strict=True):
                add(sid, b)
        if profile.void_rule == 2:
            for i, b in enumerate(TABLES["_AX_JK_ARR2"][yg]):
                add(77, b, secondary=i == 1)
            # The empty pair after the current ten-day cycle. Odd stems use
            # the first branch as primary; even stems use the second.
            first = wrap(yz - yg - 1)
            pair = (first, wrap(first + 1))
            if yg % 2 == 0:
                pair = pair[::-1]
            for i, b in enumerate(pair):
                add(78, b, secondary=i == 1)
        else:
            add(77, TABLES[f"_AX_JK_ARR{profile.void_rule}"][yg])
            add(78, yz + 11 - yg + (1 if profile.void_rule == 3 and yg % 2 else 0))

    growth_bureau = 6 if bureau == 5 and profile.earth_growth == "fire" else bureau
    growth_dir = 0 if direction == 1 or profile.growth_forward else 1
    for ordinal in range(1, 13):
        b = TABLES["_AX_SECS_ARR"][growth_bureau][growth_dir][ordinal]
        palaces[b - 1]["sequences"]["长生"] = TABLES["_SECS_CHS"][ordinal].replace(
            "\n", ""
        )
        palaces[wrap(lu + direction * (ordinal - 1)) - 1]["sequences"]["博士"] = TABLES[
            "_SETSSL_CHS"
        ][ordinal]
        for label, table, names in (
            ("岁前", "_AX_LNSQX_ARR", "_LNSQX_CHS"),
            ("将前", "_AX_LNJQX_ARR", "_LNJQX_CHS"),
        ):
            palaces[TABLES[table][ordinal][yz] - 1]["sequences"][label] = TABLES[names][
                ordinal
            ]
    decades = [
        {"index": 0, "branch": wrap(life - 2), "age_start": 1, "age_end": bureau - 1}
    ]
    for k in range(12):
        decades.append(
            {
                "index": k + 1,
                "branch": wrap(life + direction * k),
                "age_start": bureau + k * 10,
                "age_end": bureau + k * 10 + 9,
            }
        )
    for decade in decades:
        decade["stem"] = stems[decade["branch"]]
        decade["four_hua"] = [
            STAR_NAMES[sid] + HUA[i]
            for i, sid in enumerate(profile.four_hua(decade["stem"]), 1)
        ]
    return {
        "rules_version": RULES_VERSION,
        "profile": profile.snapshot(),
        "normalized": deepcopy(normalized),
        "sex": sex,
        "life": life,
        "body": body,
        "bureau": bureau,
        "bureau_name": BUREAUS[bureau],
        "direction": direction,
        "life_master": STAR_NAMES[
            TABLES["_MZX_ARR"][life if profile.life_master == "palace" else yz]
        ],
        "body_master": STAR_NAMES[TABLES["_SZX_ARR"][yz]],
        "palaces": palaces,
        "stars": stars,
        "decades": decades,
        "flow": None,
    }


def _fresh_flow_chart(natal: dict) -> dict:
    """Copy natal data and remove all previously selected limit and flow layers."""
    chart = deepcopy(natal)
    natal_layers = {"生年", "命宫", "日干"}
    for palace in chart["palaces"]:
        palace["flow_names"] = {}
        for star in palace["stars"]:
            star["hua"] = {k: v for k, v in star["hua"].items() if k in natal_layers}
    chart["flow"] = None
    return chart


def _apply_layers(chart: dict, layers: dict, profile: RuleProfile) -> dict:
    """Overlay selected palace names and four transformations onto the fresh copy."""
    result_layers = {}
    for layer, (branch, stem) in layers.items():
        for palace in chart["palaces"]:
            palace["flow_names"][layer] = PALACES[
                wrap(branch - palace["branch"] + 1) - 1
            ]
            for star in palace["stars"]:
                if star["id"] <= 28:
                    value = hua_for(star["id"], stem, profile)
                    if value:
                        star["hua"][layer] = value
        result_layers[layer] = {
            "life": branch,
            "stem": stem,
            "four_hua": [
                STAR_NAMES[sid] + HUA[i]
                for i, sid in enumerate(profile.four_hua(stem), 1)
            ],
        }
    return result_layers


def apply_decade(natal: dict, index: int) -> dict:
    """Return the selected toddler/decade chart without inventing an annual layer."""
    if type(index) is not int or not 0 <= index <= 12:
        raise ValueError("运限须为 0—12 的整数，0 为童限，1—12 为第几个大限")
    chart = _fresh_flow_chart(natal)
    profile = RuleProfile(**chart["profile"])
    decade = next(item for item in chart["decades"] if item["index"] == index)
    birth_year = chart["normalized"]["astrology_lunar"]["year"]
    chart["flow"] = {
        "kind": "decade",
        "year": None,
        "target": None,
        "age": decade["age_start"],
        "decade": deepcopy(decade),
        "start_year": birth_year + decade["age_start"] - 1,
        "end_year": birth_year + decade["age_end"] - 1,
        "layers": _apply_layers(
            chart, {"大限": (decade["branch"], decade["stem"])}, profile
        ),
    }
    return chart


def apply_flow(
    natal: dict, *, year: int | None = None, target: datetime | None = None
) -> dict:
    """Return a fresh chart; rebuilding clears all previously selected flow layers."""
    chart = _fresh_flow_chart(natal)
    profile = RuleProfile(**chart["profile"])
    if target is not None:
        if not 1900 <= target.year <= 2100:
            raise ValueError("流盘日期支持 1900—2100")
        if target < datetime.fromisoformat(chart["normalized"]["computed"]):
            raise ValueError("流盘日期不能早于出生时刻")
        normalized_target = (
            target + timedelta(days=1)
            if target.hour == 23 and profile.late_zi_rule == "next_day"
            else target
        )
        lunar = solar_at(normalized_target).getLunar()
        year = lunar.getYear()
    elif year is None:
        raise ValueError("请指定流年年份或流盘日期")
    if not 1900 <= year <= 2100:
        raise ValueError("流年年份支持 1900—2100")
    birth_year = chart["normalized"]["astrology_lunar"]["year"]
    age = year - birth_year + 1
    if age < 1 or age > chart["decades"][-1]["age_end"]:
        raise ValueError("目标年份超出本盘童限与十二个大限范围")
    yg, yz = (year - 4) % 10 + 1, (year - 4) % 12 + 1
    decade = next(
        item for item in chart["decades"] if item["age_start"] <= age <= item["age_end"]
    )
    start = TABLES["_AX_XIAOXIAN_ARR"][chart["normalized"]["year_branch"]]
    minor_branch = wrap(start + (1 if chart["sex"] == "男" else -1) * (age - 1))
    minor_stem = chart["palaces"][minor_branch - 1]["stem"]
    annual_stem = (
        yg if profile.annual_hua == "year" else chart["palaces"][yz - 1]["stem"]
    )
    layers = {
        "大限": (decade["branch"], decade["stem"]),
        "小限": (minor_branch, minor_stem),
        "流年": (yz, annual_stem),
    }
    if target is not None:
        effective_month = profile.effective_month(
            abs(lunar.getMonth()), lunar.getDay(), lunar.getMonth() < 0
        )
        base = wrap(
            1 - chart["normalized"]["month"] + chart["normalized"]["hour_branch"]
        )
        month_life = wrap(base + yz + effective_month - 2)
        day_life = wrap(month_life + lunar.getDay() - 1)
        h = (target.hour + 1) // 2 % 12 + 1
        day_stem = STEMS.index(lunar.getDayInGanZhi()[0]) + 1
        layers.update(
            {
                "流月": (
                    month_life,
                    wrap(TABLES["_WHD"][yg] + effective_month - 1, 10),
                ),
                "流日": (day_life, day_stem),
                "流时": (wrap(day_life + h - 1), wrap((day_stem - 1) % 5 * 2 + h, 10)),
            }
        )
    chart["flow"] = {
        "kind": "annual",
        "year": year,
        "age": age,
        "target": target.isoformat(sep=" ") if target else None,
        "decade": deepcopy(decade),
        "layers": _apply_layers(chart, layers, profile),
    }
    return chart
