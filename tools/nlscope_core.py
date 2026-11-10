#!/usr/bin/env python3
"""LinkScope PC 端统一解析、统计与报告引擎。"""

from __future__ import annotations

import csv
import json
import math
import re
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional


QCOM_TS = re.compile(r"^\[(\d{4})-(\d\d)-(\d\d)_(\d\d):(\d\d):(\d\d):(\d{3})\]")
KV = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s,]+)")


@dataclass(frozen=True)
class Sample:
    seq: int
    t1_us: int
    t4_us: int
    rtt_us: int
    bproc_us: int
    dlt_us: int
    wall_time: Optional[datetime] = None


@dataclass
class PlatformSignature:
    role: str = "unknown"
    link: str = "unknown"
    cfg: str = "unknown"
    payload_b: Optional[int] = None
    rate_hz: Optional[int] = None
    window: Optional[int] = None
    timeout_ms: Optional[int] = None
    mtu: Optional[int] = None
    interval: Optional[int] = None
    latency: Optional[int] = None
    selftest: str = "unknown"
    measurement_ready: bool = False
    source: str = "unknown"

    def compact(self) -> str:
        return (
            f"role={self.role} | link={self.link} | cfg={self.cfg} | "
            f"payload={self.payload_b or '?'}B | rate={self.rate_hz or '?'}Hz | "
            f"win={self.window or '?'} | timeout={self.timeout_ms or '?'}ms | "
            f"mtu={self.mtu or '?'} | intv={self.interval or '?'} | "
            f"selftest={self.selftest} | ready={'yes' if self.measurement_ready else 'no'}"
        )


@dataclass
class ParsedEvent:
    sample: Optional[Sample] = None
    counters: Optional[dict[str, int]] = None
    signature_changed: bool = False
    raw_line: str = ""


class LineParser:
    """同时接受 QCOM 落盘行和纯固件 CSV 行。"""

    def __init__(self, source: str = "unknown") -> None:
        self.signature = PlatformSignature(source=source)

    @staticmethod
    def _timestamp_and_body(line: str) -> tuple[Optional[datetime], str]:
        clean = line.rstrip("\r\n")
        match = QCOM_TS.match(clean)
        if not match:
            return None, clean.strip()
        values = [int(x) for x in match.groups()]
        stamp = datetime(values[0], values[1], values[2], values[3], values[4], values[5], values[6] * 1000)
        return stamp, clean[match.end():].strip()

    @staticmethod
    def _ints(body: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for key, value in KV.findall(body):
            try:
                result[key] = int(value.split("/")[0], 0)
            except ValueError:
                continue
        return result

    def feed(self, line: str, received_at: Optional[datetime] = None) -> ParsedEvent:
        stamp, body = self._timestamp_and_body(line)
        if stamp is None:
            stamp = received_at
        event = ParsedEvent(raw_line=line.rstrip("\r\n"))
        if body.startswith("S,"):
            parts = body.split(",")
            if len(parts) >= 7:
                try:
                    event.sample = Sample(*(int(x) for x in parts[1:7]), wall_time=stamp)
                except ValueError:
                    pass
            return event

        if "# SUM" in body:
            event.counters = self._ints(body)

        before = asdict(self.signature)
        if "role=" in body:
            values = dict(KV.findall(body))
            self.signature.role = values.get("role", self.signature.role)
        if "link=" in body:
            values = dict(KV.findall(body))
            self.signature.link = values.get("link", self.signature.link)
            self.signature.cfg = values.get("cfg", self.signature.cfg)
            for key, attr in (("payload", "payload_b"), ("rate", "rate_hz"), ("win", "window"), ("timeout", "timeout_ms")):
                raw = values.get(key)
                if raw:
                    digits = re.match(r"\d+", raw)
                    if digits:
                        setattr(self.signature, attr, int(digits.group()))
        if "[SELFTEST]" in body:
            self.signature.selftest = "PASS" if "-> PASS" in body else "FAIL"
        if "mtu=" in body:
            values = self._ints(body)
            if "mtu" in values:
                self.signature.mtu = values["mtu"]
        if "param updated" in body:
            values = self._ints(body)
            self.signature.interval = values.get("intv", self.signature.interval)
            self.signature.latency = values.get("latency", self.signature.latency)
        if "measurement ready" in body or "link ready" in body:
            self.signature.measurement_ready = True
        event.signature_changed = before != asdict(self.signature)
        return event


def percentile(values: list[int], p: float) -> Optional[int]:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * p))))
    return ordered[index]


@dataclass
class Session:
    signature: PlatformSignature = field(default_factory=PlatformSignature)
    samples: list[Sample] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    raw_lines: list[str] = field(default_factory=list)

    def add(self, event: ParsedEvent, signature: PlatformSignature) -> None:
        self.signature = PlatformSignature(**asdict(signature))
        if event.raw_line:
            self.raw_lines.append(event.raw_line)
        if event.sample:
            self.samples.append(event.sample)
        if event.counters:
            self.counters = event.counters

    def metrics(self) -> dict[str, float | int | None]:
        rtt = [row.rtt_us for row in self.samples]
        bproc = [row.bproc_us for row in self.samples]
        adjacent = [abs(rtt[i] - rtt[i - 1]) for i in range(1, len(rtt))]
        result: dict[str, float | int | None] = {
            "n": len(rtt),
            "p50_us": percentile(rtt, .5),
            "p90_us": percentile(rtt, .9),
            "p99_us": percentile(rtt, .99),
            "p999_us": percentile(rtt, .999),
            "max_us": max(rtt) if rtt else None,
            "mean_us": round(statistics.mean(rtt), 1) if rtt else None,
            # 与板端口径一致：相邻 RTT 差分的平均绝对值，不是总体标准差。
            "jitter_us": round(statistics.mean(adjacent), 1) if adjacent else 0,
            "bproc_mean_us": round(statistics.mean(bproc), 1) if bproc else None,
            "sample_span_s": self.sample_span_seconds(),
        }
        for key in ("sent", "recv", "to", "dup", "ooo", "bad", "drop", "loss_ppm"):
            result[key] = self.counters.get(key, 0)
        return result

    def sample_span_seconds(self) -> float:
        stamps = [row.wall_time for row in self.samples if row.wall_time is not None]
        if len(stamps) < 2:
            return 0.0
        return max(0.0, (stamps[-1] - stamps[0]).total_seconds())

    def histogram(self, bins: int = 24) -> list[tuple[float, float, int]]:
        values = [row.rtt_us / 1000.0 for row in self.samples]
        if not values:
            return []
        low, high = min(values), max(values)
        if math.isclose(low, high):
            return [(low, high, len(values))]
        width = (high - low) / bins
        counts = [0] * bins
        for value in values:
            counts[min(bins - 1, int((value - low) / width))] += 1
        return [(low + i * width, low + (i + 1) * width, count) for i, count in enumerate(counts)]

    def export(self, directory: Path, stem: str = "linkscope-session") -> tuple[Path, Path, Path, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        raw_path = directory / f"{stem}.log"
        csv_path = directory / f"{stem}.csv"
        meta_path = directory / f"{stem}.json"
        report_path = directory / f"{stem}.md"
        raw_path.write_text("\n".join(self.raw_lines) + ("\n" if self.raw_lines else ""), encoding="utf-8")
        with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(("seq", "t1_us", "t4_us", "rtt_us", "bproc_us", "dlt_us", "wall_time"))
            for row in self.samples:
                writer.writerow((row.seq, row.t1_us, row.t4_us, row.rtt_us, row.bproc_us, row.dlt_us,
                                 row.wall_time.isoformat(timespec="milliseconds") if row.wall_time else ""))
        payload = {"platform_signature": asdict(self.signature), "pc_exact_metrics": self.metrics(),
                   "board_latest_sum": self.counters}
        meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        report_path.write_text(self.markdown_report(), encoding="utf-8")
        return raw_path, csv_path, meta_path, report_path

    def markdown_report(self) -> str:
        m = self.metrics()
        def ms(key: str) -> str:
            value = m[key]
            return "—" if value is None else f"{float(value) / 1000.0:.3f} ms"
        return f"""# LinkScope 实验报告

## 平台签名

`{self.signature.compact()}`

## PC 原始样本精确统计

| 指标 | 数值 |
|---|---:|
| 有效样本 | {m['n']} |
| 样本记录时长 | {float(m['sample_span_s']):.3f} s |
| RTT p50 | {ms('p50_us')} |
| RTT p90 | {ms('p90_us')} |
| RTT p99 | {ms('p99_us')} |
| RTT p99.9 | {ms('p999_us')} |
| RTT max | {ms('max_us')} |
| RTT mean | {ms('mean_us')} |
| jitter | {ms('jitter_us')} |
| timeout / duplicate / out-of-order | {m['to']} / {m['dup']} / {m['ooo']} |
| bad / drop | {m['bad']} / {m['drop']} |

## 板端最近一条 SUM

```json
{json.dumps(self.counters, ensure_ascii=False, indent=2)}
```

## 口径

RTT 使用 initiator 板端同一时钟的 `t4-t1`；PC 串口到达时间不参与链路计时。PC 分位数由已落盘原始样本精确排序得到；板端 SUM 使用内存受限的对数直方图插值，两者允许存在小幅差异。`jitter` 在两端均定义为相邻 RTT 差分绝对值的平均值。本报告描述当前固件、SDK、配置与环境，不外推为协议标准的固定性能。
"""


def load_log(path: Path) -> Session:
    parser = LineParser(source=str(path))
    session = Session()
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            session.add(parser.feed(line), parser.signature)
    return session


def feed_lines(lines: Iterable[str], source: str = "memory") -> Session:
    parser = LineParser(source=source)
    session = Session()
    for line in lines:
        session.add(parser.feed(line), parser.signature)
    return session
