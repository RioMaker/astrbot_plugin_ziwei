"""Short-lived, owner-scoped chart data for AstrBot function tools."""

import json
import math
import time
import uuid
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass

from .modern_renderer import display_category
from .rules import BRANCHES, STEMS


def tool_error(status, message):
    return json.dumps(
        {"schema_version": 1, "status": status, "message": message}, ensure_ascii=False
    )


def owner_key(scope):
    if not scope.platform_id or not scope.group_id or not scope.user_id:
        raise ValueError("无法确认当前群或发送者身份")
    return scope.platform_id, scope.group_id, scope.user_id


@dataclass
class CachedChart:
    chart_id: str
    chart: dict
    expires_at: float


class ChartCache:
    def __init__(self, ttl=900, capacity=128, clock=time.monotonic):
        self.ttl, self.capacity, self.clock = ttl, capacity, clock
        self.entries = OrderedDict()

    def _expire(self):
        now = self.clock()
        for key in [key for key, row in self.entries.items() if row.expires_at <= now]:
            del self.entries[key]

    def put(self, scope, chart):
        self._expire()
        key = owner_key(scope)
        row = CachedChart(uuid.uuid4().hex, deepcopy(chart), self.clock() + self.ttl)
        self.entries.pop(key, None)
        self.entries[key] = row
        while len(self.entries) > self.capacity:
            self.entries.popitem(last=False)
        return row

    def get(self, scope, chart_id=""):
        self._expire()
        row = self.entries.get(owner_key(scope))
        if row is None or (chart_id and row.chart_id != chart_id):
            return None
        # Reading does not extend retention or expose a mutable cached object.
        return deepcopy(row)

    def clear_group(self, scope):
        for key in [
            key
            for key in self.entries
            if key[:2] == (scope.platform_id, scope.group_id)
        ]:
            del self.entries[key]

    def clear(self):
        self.entries.clear()


def chart_payload(row, *, remaining_seconds, palace=""):
    chart = row.chart
    name = palace.strip()
    palaces = chart["palaces"]
    if name:
        palaces = [
            p
            for p in palaces
            if name
            in {
                p["name"],
                p["name"].removesuffix("宫"),
                p["name"].removesuffix("宫") + "宫",
            }
        ]
        if not palaces:
            raise ValueError(
                "宫位须为命宫、兄弟、夫妻、子女、财帛、疾厄、迁移、交友、官禄、田宅、福德或父母"
            )
    exported = []
    for p in palaces:
        item = deepcopy(p)
        for star in item["stars"]:
            star["visual_category"] = display_category(star)
        exported.append(item)
    n = chart["normalized"]
    result = {
        "schema_version": 1,
        "status": "ok",
        "chart_id": row.chart_id,
        "expires_in_seconds": max(0, math.ceil(remaining_seconds)),
        "data": {
            "rules_version": chart["rules_version"],
            "rule_profile": deepcopy(chart["profile"]),
            "normalized_birth": deepcopy(n),
            "summary": {
                "sex": chart["sex"],
                "bureau": chart["bureau"],
                "bureau_name": chart["bureau_name"],
                "life_branch": chart["life"],
                "life_branch_name": BRANCHES[chart["life"] - 1],
                "body_branch": chart["body"],
                "body_branch_name": BRANCHES[chart["body"] - 1],
                "life_master": chart["life_master"],
                "body_master": chart["body_master"],
                "ziwei_year": STEMS[n["year_stem"] - 1]
                + BRANCHES[n["year_branch"] - 1],
                "decade_direction": "顺行" if chart["direction"] == 1 else "逆行",
            },
            "palaces": exported,
            "decades": deepcopy(chart["decades"]),
            "flow": deepcopy(chart["flow"]),
            "selected_palace": name or None,
        },
        "reading_notes": {
            "indices": "天干甲=1至癸=10，地支子=1至亥=12；"
            "宫名字段为本命，flow_names为运限。",
            "age": "大限年龄和流盘年龄均为虚岁；流年年份按农历换年。",
            "mode": "flow.kind=decade为所选童限或大运，year/target为空，"
            "start_year/end_year为农历年区间；kind=annual为流年或完整流盘。",
            "stars": "各宫stars保留独立instance_id，副星secondary=true；"
            "group为计算分类，visual_category为图面分类。",
            "hua": "hua分别存生年、命宫、日干和所选运限四化；"
            "self_hua.outward为离心，inward为向心。",
            "scope": "返回计算事实；真太阳时为近似校正，"
            "尚未原软件全盘比对，不含独立限流曜集合。",
            "interpretation": "按返回数据解释，"
            "不得编造星曜或未返回的运限、庙旺与四化。",
        },
    }
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
