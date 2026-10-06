import ast
import json
import re
from pathlib import Path

import yaml

from astrbot_plugin_ziwei.commands import parse_request
from astrbot_plugin_ziwei.rules import RuleProfile

ROOT = Path(__file__).resolve().parents[1]


def test_metadata_and_config_are_loadable_and_consistent():
    meta = yaml.safe_load((ROOT / "metadata.yaml").read_text("utf-8"))
    assert meta["name"] == ROOT.name == "astrbot_plugin_ziwei"
    assert meta["version"] == "1.2.0" and meta["repo"] == ""
    tree = ast.parse((ROOT / "main.py").read_text("utf-8"))
    registration = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "register"
    )
    assert ast.literal_eval(registration.args[0]) == meta["name"]
    assert ast.literal_eval(registration.args[3]) == meta["version"]
    schema = json.loads((ROOT / "_conf_schema.json").read_text("utf-8"))
    defaults = {k: item["default"] for k, item in schema.items()}
    assert RuleProfile.from_config(defaults).snapshot() == RuleProfile().snapshot()
    assert set(RuleProfile().snapshot()) <= set(schema)
    assert parse_request("2001-03-19 10:00 男", defaults).output == "image"
    assert all(item["description"] and item["type"] for item in schema.values())


def test_repository_documents_have_valid_relative_links():
    for doc in ROOT.rglob("*.md"):
        if ".dev" in doc.parts:
            continue
        for target in re.findall(r"\]\(([^)]+)\)", doc.read_text("utf-8")):
            if "://" not in target and not target.startswith("#"):
                assert (doc.parent / target.split("#")[0]).exists(), (doc, target)
