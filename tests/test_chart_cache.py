from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.commands import DEMO_BIRTH, parse_request
from astrbot_plugin_ziwei.engine import build_chart
from astrbot_plugin_ziwei.llm_data import ChartCache

from .conftest import Event


def test_cache_capacity_copy_isolation_and_expiration():
    chart = build_chart(parse_request(DEMO_BIRTH).birth)
    now = [100.0]
    cache = ChartCache(capacity=2, clock=lambda: now[0])
    a, b, c = (capture_group(Event(user=x)) for x in ("a", "b", "c"))
    cache.put(a, chart)
    cache.put(b, chart)
    cache.put(c, chart)
    assert cache.get(a) is None
    row = cache.get(b)
    row.chart["palaces"].clear()
    assert len(cache.get(b).chart["palaces"]) == 12
    assert cache.get(c, row.chart_id) is None
    now[0] = 999.9
    assert cache.get(b) is not None
    now[0] = 1000
    assert cache.get(b) is None and not cache.entries
