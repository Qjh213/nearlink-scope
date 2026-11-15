"""Versioned experiment specifications, independent from the GUI and parser."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
DEFAULT_SPEC_PATH = Path(__file__).with_name("experiment_specs.json")


@dataclass(frozen=True)
class ExperimentSpec:
    name: str
    mode: str
    expect: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("实验规格名称不能为空")
        if self.mode not in {"raw", "auto", "measure"}:
            raise ValueError(f"不支持的实验规格模式：{self.mode}")
        if self.mode == "measure" and not self.expect:
            raise ValueError("测量规格必须声明至少一个预期字段")


def load_specs(path: str | Path = DEFAULT_SPEC_PATH) -> dict[str, ExperimentSpec]:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"实验规格schema_version={payload.get('schema_version')!r}，当前仅支持{SCHEMA_VERSION}"
        )
    result: dict[str, ExperimentSpec] = {}
    for item in payload.get("profiles", []):
        spec = ExperimentSpec(
            name=str(item["name"]), mode=str(item.get("mode", "measure")),
            expect=dict(item.get("expect", {})),
        )
        if spec.name in result:
            raise ValueError(f"实验规格名称重复：{spec.name}")
        result[spec.name] = spec
    if not result:
        raise ValueError("实验规格文件没有profiles")
    return result


def expected_mapping(specs: dict[str, ExperimentSpec]) -> dict[str, dict[str, Any] | None]:
    """Compatibility view for non-GUI callers; raw/auto have no fixed expected fields."""
    return {name: (dict(spec.expect) if spec.mode == "measure" else None)
            for name, spec in specs.items()}
