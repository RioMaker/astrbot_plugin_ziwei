"""Deterministic relationships for readings; never change natal/flow algorithms."""

import hashlib
import json
from copy import deepcopy

from .engine import _fresh_flow_chart
from .rules import HUA, PALACES, STAR_NAMES, RuleProfile, wrap

SCOPES = ("natal", "decade", "annual")
TOPICS = {
    "general": ("命宫", "福德", "迁移"),
    "career": ("官禄", "命宫", "财帛"),
    "wealth": ("财帛", "田宅", "福德"),
    "relationship": ("夫妻", "命宫", "福德"),
    "family": ("父母", "兄弟", "田宅"),
    "children": ("子女", "夫妻"),
    "friends": ("交友", "兄弟", "命宫"),
    "health": ("疾厄", "福德", "命宫"),
    "studies": ("命宫", "官禄", "父母"),
    "mobility": ("迁移", "命宫", "官禄"),
    "property": ("田宅", "财帛"),
    "wellbeing": ("福德", "命宫"),
}


def reading_chart(chart, scope):
    if scope not in SCOPES:
        raise ValueError("分析范围须为 natal、decade 或 annual")
    if scope == "natal":
        return _fresh_flow_chart(chart)
    flow = chart.get("flow") or {}
    if scope == "decade" and flow.get("kind") != "decade":
        raise ValueError("大运分析请先明确并排出所选运限盘")
    if scope == "annual" and flow.get("kind") != "annual":
        raise ValueError("流年分析请先明确并排出所选农历年盘")
    chart = deepcopy(chart)
    if scope == "annual":
        allowed = {"大限", "流年"}
        chart["flow"]["layers"] = {
            k: v for k, v in chart["flow"]["layers"].items() if k in allowed
        }
        chart["flow"]["target"] = None
        for p in chart["palaces"]:
            p["flow_names"] = {k: v for k, v in p["flow_names"].items() if k in allowed}
            for star in p["stars"]:
                star["hua"] = {
                    k: v
                    for k, v in star["hua"].items()
                    if k in allowed | {"生年", "命宫", "日干"}
                }
    return chart


def build_facts(chart, scope, topic):
    if topic not in TOPICS:
        raise ValueError("未知分析主题")
    chart = reading_chart(chart, scope)
    profile = RuleProfile(**chart["profile"])
    layer = {"natal": None, "decade": "大限", "annual": "流年"}[scope]
    life = chart["flow"]["layers"][layer]["life"] if layer else chart["life"]
    layers = {"本命": chart["life"]}
    if chart["flow"]:
        layers.update({k: v["life"] for k, v in chart["flow"]["layers"].items()})
    positions = {s["id"]: s for s in chart["stars"] if not s["secondary"]}
    relevant = set()
    for name in TOPICS[topic]:
        branch = wrap(life - PALACES.index(name))
        relevant.update(wrap(branch + delta) for delta in (0, 4, 8, 6))
    palaces = []
    flights = []
    for p in chart["palaces"]:
        b = p["branch"]
        if b in relevant:
            palaces.append(
                dict(
                    deepcopy(p),
                    layer_names={
                        k: PALACES[wrap(v - b + 1) - 1] for k, v in layers.items()
                    },
                    opposite=wrap(b + 6),
                    trines=[wrap(b + 4), wrap(b + 8)],
                    qi_number_branch=wrap(b - 8),
                )
            )
        # Heluo uses natal palace stems at the same physical branch in all layers.
        # These flights are distinct from the configured annual year-stem Hua.
        for i, sid in enumerate(profile.four_hua(p["stem"]), 1):
            star = positions.get(sid)
            if star:
                flights.append(
                    {
                        "source_branch": b,
                        "source_stem": p["stem"],
                        "source_names": {
                            k: PALACES[wrap(v - b + 1) - 1] for k, v in layers.items()
                        },
                        "star": STAR_NAMES[sid],
                        "hua": HUA[i],
                        "target_branch": star["branch"],
                        "opposite_branch": wrap(star["branch"] + 6),
                        "self": b == star["branch"],
                    }
                )
    return {
        "scope": scope,
        "topic": topic,
        "rules_version": chart["rules_version"],
        "profile": chart["profile"],
        "life": chart["life"],
        "body": chart["body"],
        "selected_life": life,
        "flow": chart["flow"],
        "palaces": palaces,
        "palace_stem_flights": flights,
        "capabilities": [
            "natal_stars",
            "brightness",
            "trines",
            "layer_palaces",
            "palace_stem_flights",
            "self_hua",
        ],
        "unsupported": [
            "独立限流曜",
            "流昌流曲",
            "流羊流陀",
            "按古书重排童限",
            "天地人独立安星盘",
        ],
        "notes": [
            "一基地支索引；同一物理宫位在不同层级有不同宫名。",
            "宫干飞化沿用本命宫干，不等于当前配置的流年年干四化。",
            "本期只分析本命、大运、流年；日期盘中的月日时不纳入本轮推断。",
            "这些是计算关系，不是已经发生的事件或已验证预测。",
        ],
    }


def feature_tags(facts):
    """Small controlled vocabulary for cross-owner aggregation; no free text."""
    tags = {"scope:" + facts["scope"], "topic:" + facts["topic"]}
    setup = json.dumps([facts["rules_version"], facts["profile"]], sort_keys=True)
    tags.add("setup:" + hashlib.sha256(setup.encode()).hexdigest())
    layer = {"natal": "本命", "decade": "大限", "annual": "流年"}[facts["scope"]]
    for p in facts["palaces"]:
        palace_name = p["layer_names"][layer]
        if palace_name in TOPICS[facts["topic"]]:
            for star in p["stars"]:
                if star["id"] <= 14:
                    tags.add("major:" + palace_name + ":" + str(star["id"]))
                for source, value in star["hua"].items():
                    if source in {"生年", "大限", "流年"}:
                        tags.add("hua:" + palace_name + ":" + source + ":" + value)
    return sorted(tags)
