"""Guard the command signature consumed by AstrBot before handler invocation."""

import inspect

import pytest
from astrbot.core.star.filter.command import GreedyStr

from astrbot_plugin_ziwei.main import ZiweiPlugin


@pytest.mark.parametrize("handler", [ZiweiPlugin.ziwei, ZiweiPlugin.life_atlas])
def test_greedy_command_preserves_annotation_for_framework_binding(handler):
    # AstrBot CommandFilter.init_handler_md uses the default value instead of
    # the annotation when a default is present. An empty str therefore prevents
    # GreedyStr from consuming sex/theme tokens after the birth date.
    # Source: AstrBotDevs/AstrBot, astrbot/core/star/filter/command.py
    # Checked blob: abf339fda7f0740f542a91cd4adb859d5ef6ed5f (2026-10-16).
    parameters = list(inspect.signature(handler, eval_str=True).parameters.values())
    assert [parameter.name for parameter in parameters] == ["self", "event", "args"]
    greedy = parameters[-1]
    assert greedy.annotation is GreedyStr
    assert greedy.default is inspect.Parameter.empty, (
        "GreedyStr already accepts empty input; a default makes AstrBot treat "
        "the field as a single ordinary string and lose the remaining tokens"
    )
