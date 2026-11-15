#!/usr/bin/env python3
"""第三方 CSV 适配器：把别的工具导出的表，映射成 LinkScope 的规范化样本。

设计约束：
- 只做"列名映射 + 单位换算"，不猜；映射缺列就明确报错，不静默补零；
- 原始文件只读，转换结果通过 `Session.export()` 落盘，三个计数（总行/坏行/跳过）写进报告；
- 时间戳列可缺省；缺省时 `wall_time` 为空，`sample_span_s` 也会如实反映"没有墙钟"。
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from nlscope_core import Sample, Session

REQUIRED = ("seq", "t1_us", "t4_us")


@dataclass
class AdapterSpec:
    name: str
    required: tuple[str, ...] = REQUIRED
    delimiter: str = ","
    time_scale: float = 1.0           # 输入单位 → 微秒的乘数（ms 填 1000）
    columns: dict[str, str] = field(default_factory=dict)
    timestamp_column: str = ""
    timestamp_format: str = "%Y-%m-%d %H:%M:%S.%f"

    @classmethod
    def from_json(cls, path: Path) -> "AdapterSpec":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("适配器必须是 JSON 对象")
        if payload.get("schema_version") != 1:
            raise ValueError("适配器 schema_version 必须是 1")
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("适配器缺少 name")
        columns = payload.get("columns") or {}
        if not isinstance(columns, dict) or not columns:
            raise ValueError("适配器缺少 columns 映射")
        lowered = {str(key).strip().lower(): str(value) for key, value in columns.items()}
        missing = [key for key in REQUIRED if key not in lowered]
        if missing:
            raise ValueError("适配器缺少必需列映射：" + ", ".join(missing))
        scale = float(payload.get("time_scale", 1.0))
        if scale <= 0:
            raise ValueError("time_scale 必须为正数")
        return cls(name=name, delimiter=str(payload.get("delimiter") or ","),
                   time_scale=scale, columns=lowered,
                   timestamp_column=str(payload.get("timestamp_column") or ""),
                   timestamp_format=str(payload.get("timestamp_format")
                                        or "%Y-%m-%d %H:%M:%S.%f"))


def import_csv(path: Path, spec: AdapterSpec) -> tuple[Session, dict]:
    """读第三方 CSV，返回会话与转换报告（总行/坏行/跳过及原因计数）。"""
    session = Session()
    report = {"adapter": spec.name, "rows": 0, "imported": 0, "bad": 0,
              "reasons": {}, "columns": dict(spec.columns)}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=spec.delimiter)
        if reader.fieldnames is None:
            raise ValueError("CSV 没有表头，无法按列名映射")
        available = {name.strip().lower(): name for name in reader.fieldnames}
        for key in REQUIRED:
            if spec.columns[key].strip().lower() not in available:
                raise ValueError(f"CSV 里没有列 {spec.columns[key]!r}（映射 {key}）")
        for row in reader:
            report["rows"] += 1
            try:
                values = {key: row[available[spec.columns[key].strip().lower()]]
                          for key in REQUIRED}
                seq = int(float(values["seq"]))
                t1 = int(round(float(values["t1_us"]) * spec.time_scale))
                t4 = int(round(float(values["t4_us"]) * spec.time_scale))
            except (KeyError, TypeError, ValueError) as exc:
                report["bad"] += 1
                reason = f"{type(exc).__name__}"
                report["reasons"][reason] = report["reasons"].get(reason, 0) + 1
                continue
            bproc = None
            if "bproc_us" in spec.columns:
                raw = row.get(available.get(spec.columns["bproc_us"].strip().lower(), ""), "")
                if raw not in ("", None):
                    try:
                        bproc = int(round(float(raw) * spec.time_scale))
                    except ValueError:
                        bproc = None
            wall = None
            if spec.timestamp_column:
                raw_ts = row.get(available.get(spec.timestamp_column.strip().lower(), ""), "")
                if raw_ts:
                    try:
                        wall = datetime.strptime(raw_ts.strip(), spec.timestamp_format)
                    except ValueError:
                        wall = None
            session.samples.append(Sample(seq=seq, t1_us=t1, t4_us=t4,
                                          rtt_us=(t4 - t1) & 0xFFFFFFFF,
                                          bproc_us=bproc, wall_time=wall))
            report["imported"] += 1
    return session, report


def format_import_report(report: dict) -> str:
    reasons = "；".join(f"{key}×{value}" for key, value in sorted(report["reasons"].items()))
    return (f"适配器 {report['adapter']}：读 {report['rows']} 行，导入 {report['imported']} 条，"
            f"坏行 {report['bad']}" + (f"（{reasons}）" if reasons else ""))
