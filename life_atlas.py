"""Auditable P1 structural windows; these are not verified life-event forecasts."""

from collections import defaultdict
from copy import deepcopy

from .analysis_facts import build_facts, reading_chart
from .engine import apply_flow
from .knowledge import KnowledgeBase
from .rules import PALACES, wrap

_REQUIRES = frozenset({"natal_stars", "layer_palaces", "palace_stem_flights"})
_RULE_IDS = (
    "hl-method",
    "hl-stems",
    "hl-annual",
    "hl-decade-inside",
    "hl-context",
)
_TOPICS = (
    ("官禄", "career"),
    ("夫妻", "relationship"),
    ("迁移", "mobility"),
    ("田宅", "home"),
)


def _check_capabilities(facts: dict) -> None:
    missing = _REQUIRES - set(facts["capabilities"])
    if missing:
        raise ValueError("人生图谱缺少必要计算能力：" + "、".join(sorted(missing)))


def _annual_hits(natal: dict, facts: dict) -> list[dict]:
    """Keep both source paths, without counting a common target as probability."""
    _check_capabilities(facts)
    flow = facts["flow"]
    decade_source = flow["layers"]["大限"]["life"]
    annual_source = flow["layers"]["流年"]["life"]
    index = flow["decade"]["index"]
    if index == 0 or decade_source == annual_source:
        return []
    decade_paths = [
        path
        for path in facts["palace_stem_flights"]
        if path["source_branch"] == decade_source
    ]
    annual_paths = [
        path
        for path in facts["palace_stem_flights"]
        if path["source_branch"] == annual_source
    ]
    hits = []
    for name, topic in _TOPICS:
        target = wrap(decade_source - PALACES.index(name))
        dp = [path for path in decade_paths if path["target_branch"] == target]
        ap = [path for path in annual_paths if path["target_branch"] == target]
        if not dp or not ap:
            continue
        palace = natal["palaces"][target - 1]
        hits.append(
            {
                "age": flow["age"],
                "year": flow["year"],
                "decade_index": index,
                "topic": "study" if topic == "career" and flow["age"] < 18 else topic,
                "target_branch": target,
                "target_decade_palace": name,
                "target_natal_palace": palace["name"],
                "natal_major_stars": [
                    {key: star[key] for key in ("name", "brightness", "secondary")}
                    for star in palace["stars"]
                    if 1 <= star["id"] <= 14
                ],
                "decade_paths": deepcopy(dp),
                "annual_paths": deepcopy(ap),
                "interpretation_status": "structural_candidate_only",
                "conditions_status": "literature_context_not_fully_evaluated",
                "feedback": None,
            }
        )
    return hits


def _merge_windows(hits: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for hit in hits:
        grouped[(hit["topic"], hit["decade_index"], hit["target_branch"])].append(hit)
    windows = []
    for entries in grouped.values():
        run = []
        for hit in sorted(entries, key=lambda item: item["age"]):
            if run and hit["age"] != run[-1]["age"] + 1:
                windows.append({"hits": run})
                run = []
            run.append(hit)
        if run:
            windows.append({"hits": run})
    windows.sort(key=lambda item: (item["hits"][0]["age"], item["hits"][0]["topic"]))
    for number, window in enumerate(windows, 1):
        first, last = window["hits"][0], window["hits"][-1]
        hua = {
            path["hua"]
            for hit in window["hits"]
            for path in hit["decade_paths"] + hit["annual_paths"]
        }
        window.update(
            {
                "id": f"{number:02}",
                "start_age": first["age"],
                "end_age": last["age"],
                "start_year": first["year"],
                "end_year": last["year"],
                "topic": first["topic"],
                "mixed": "忌" in hua and bool(hua & {"禄", "权", "科"}),
            }
        )
    return windows


def build_life_atlas(chart: dict) -> dict:
    """Scan birth through the end of the decade containing age 60 (nominal age).

    Accept an engine natal/flow chart, return a detached serializable snapshot,
    and never mutate the input. The full lunar-year range must fit 1900--2100.
    P1 is a display heuristic, not a literature rule or a major-event verdict.
    """
    natal = reading_chart(chart, "natal")
    # JSON-restored charts no longer share star objects between these arrays.
    for star in natal["stars"]:
        star["hua"] = {
            key: value
            for key, value in star["hua"].items()
            if key in {"生年", "命宫", "日干"}
        }
    endpoint = next(
        decade
        for decade in natal["decades"]
        if decade["age_start"] <= 60 <= decade["age_end"]
    )
    end_age = endpoint["age_end"]
    start_year = natal["normalized"]["astrology_lunar"]["year"]
    end_year = start_year + end_age - 1
    if start_year < 1900 or end_year > 2100:
        raise ValueError(
            f"人生图谱需完整扫描农历 {start_year}—{end_year} 年，"
            "超出支持的 1900—2100 年范围；不能截断至60虚岁所在大运结束前。"
        )

    natal_facts = build_facts(natal, "natal", "general")
    _check_capabilities(natal_facts)
    knowledge = KnowledgeBase()
    cards = {card["id"]: card for card in knowledge.cards}
    snapshots = []
    for rule_id in _RULE_IDS:
        if rule_id not in cards:
            raise ValueError("人生图谱缺少知识来源：" + rule_id)
        card = cards[rule_id]
        if set(card["requires"]) - set(natal_facts["capabilities"]):
            raise ValueError("人生图谱知识来源缺少必要计算能力：" + rule_id)
        snapshots.append(deepcopy(card))

    hits = []
    for year in range(start_year, end_year + 1):
        annual = apply_flow(natal, year=year)
        facts = build_facts(annual, "annual", "general")
        hits.extend(_annual_hits(natal, facts))
    windows = _merge_windows(hits)
    return {
        "schema_version": "ziwei-life-atlas-v1",
        "title": "人生变动图谱",
        "rules_version": natal["rules_version"],
        "knowledge_version": knowledge.version,
        "age_basis": "虚岁；安星农历年递增；出生所在年为虚岁1",
        "start_age": 1,
        "end_age": end_age,
        "start_year": start_year,
        "end_year": end_year,
        "endpoint_decade": deepcopy(endpoint),
        "decades": [
            deepcopy(decade)
            for decade in natal["decades"]
            if decade["age_start"] <= end_age
        ],
        "natal": natal,
        "windows": windows,
        "scan_summary": {
            "scanned_years": end_age,
            "annual_hits": len(hits),
            "window_count": len(windows),
            "required_capabilities": sorted(_REQUIRES),
            "unsupported": deepcopy(natal_facts["unsupported"]),
        },
        "preview_rule": {
            "id": "P1-common-target-v1",
            "status": "自定结构预览启发式，非古籍重大事件公式",
            "source_kind": "本命固定宫干飞化；流年按宫位取宫干，非流年年干四化",
            "trigger": (
                "大限命宫和流年命宫地支不同，二者飞化共同落入"
                "同一个大限官禄、夫妻、迁移或田宅宫"
            ),
            "merge": "仅同主题、同大运、同目标地支且连续命中的农历年合并",
            "display_mapping": (
                "官禄议题在虚岁18之前标学习评估，其后标工作职责；此为展示规则"
            ),
            "not_evaluated": [
                "童限事件判断",
                "完整星情庙旺会照条件",
                "现实职业或关系情境",
                "重大事件阈值",
                "月日时",
                "独立限流曜",
            ],
            "no_hit_meaning": "本次筛选未标出，不表示无事发生",
        },
        "knowledge_snapshot": snapshots,
        "knowledge_source_snapshot": {
            source: deepcopy(knowledge.sources[source])
            for source in sorted({card["source"] for card in snapshots})
        },
    }
