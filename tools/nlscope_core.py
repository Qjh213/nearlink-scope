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
    # 跨两个设备时钟的差值（t4-t3），含未知偏差，不作为时延指标，也不写进 CSV；
    # None 表示“这一份数据没有该字段”（新导出），0 表示“板端确实报 0”。
    dlt_us: Optional[int] = None
    wall_time: Optional[datetime] = None


@dataclass(frozen=True)
class AdvRecord:
    """SLE seek callback copied by the monitor firmware."""

    seq: int
    t_us: int
    event: int
    addr_id: str
    rssi_dbm: int
    data_status: int
    data_len: int
    payload_hex: str
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
    seek_interval: Optional[int] = None
    seek_window: Optional[int] = None
    filter_duplicates: Optional[int] = None
    selftest: str = "unknown"
    measurement_ready: bool = False
    source: str = "unknown"

    def compact(self) -> str:
        if self.role == "monitor" or self.link == "sle-passive-seek":
            return (
                f"role={self.role} | link={self.link} | "
                f"seek={self.seek_interval or '?'}/{self.seek_window or '?'} | "
                f"filter_dup={self.filter_duplicates if self.filter_duplicates is not None else '?'} | "
                f"selftest={self.selftest} | ready={'yes' if self.measurement_ready else 'no'}"
            )
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
    adv: Optional[AdvRecord] = None
    counters: Optional[dict[str, int]] = None
    monitor_counters: Optional[dict[str, int]] = None
    signature_changed: bool = False
    raw_line: str = ""
    parse_failure: Optional[str] = None


class LineParser:
    """同时接受 QCOM 落盘行和纯固件 CSV 行。"""

    def __init__(self, source: str = "unknown") -> None:
        self.signature = PlatformSignature(source=source)
        self._monitor_seek_enabled = False

    def _refresh_monitor_ready(self) -> None:
        signature = self.signature
        if signature.role != "monitor":
            return
        signature.measurement_ready = bool(
            self._monitor_seek_enabled
            and signature.link == "sle-passive-seek"
            and signature.selftest == "PASS"
            and signature.seek_interval is not None
            and signature.seek_window is not None
            and signature.filter_duplicates in (0, 1)
        )

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
                parts = value.split("/")
                parse = lambda item: int(item, 16) if item.lower().startswith(("0x", "-0x")) else int(item, 10)
                result[key] = parse(parts[0])
                if len(parts) == 2:
                    result[f"{key}_max"] = parse(parts[1])
            except ValueError:
                continue
        return result

    @staticmethod
    def valid_sample_values(values: list[int]) -> bool:
        """只校验同一时钟内成立的不变量。

        `dlt_us = t4 - t3` 跨了两个设备的时钟，含未知偏差 δ：B 端重新建链后 δ 可能变成
        几千万微秒，于是 `dlt_us > rtt_us` 完全正常（2026-11-15 断链实测：2,332 行因此
        被整行丢弃）。用 rtt 去约束 dlt 会把**有效样本**判成坏行，所以这个上限不能有。
        """
        if len(values) != 6:
            return False
        seq, t1_us, t4_us, rtt_us, bproc_us, _dlt_us = values
        return (
            0 <= seq <= 0xFFFF
            and all(0 <= value <= 0xFFFFFFFF for value in values[1:])
            and rtt_us == ((t4_us - t1_us) & 0xFFFFFFFF)
            and bproc_us <= rtt_us
        )

    def feed(self, line: str, received_at: Optional[datetime] = None) -> ParsedEvent:
        # CRLF 行以 CR 为界：切分后下一行的开头会留下 LF，必须两端都去掉，
        # 否则 raw_line 变成 "[stamp]\n正文"，导出时时间戳与正文被拆成两行。
        original = line.strip("\r\n")
        stamp, body = self._timestamp_and_body(line)
        had_source_timestamp = stamp is not None
        if stamp is None:
            stamp = received_at
        raw_line = original
        if not had_source_timestamp and received_at is not None:
            millis = received_at.microsecond // 1000
            raw_line = f"[{received_at:%Y-%m-%d_%H:%M:%S}:{millis:03d}]{original}"
        event = ParsedEvent(raw_line=raw_line)
        if "\ufffd" in body:
            event.parse_failure = "decode"
        if body.startswith("S,"):
            parts = body.split(",")
            if len(parts) >= 7:
                try:
                    values = [int(x) for x in parts[1:7]]
                    seq, t1_us, t4_us, rtt_us, bproc_us, dlt_us = values
                    # UART乱码偶尔仍可能组成“全是数字”的一行；用固件输出不变量拒绝伪样本。
                    if self.valid_sample_values(values):
                        event.sample = Sample(*values, wall_time=stamp)
                except ValueError:
                    pass
            if event.sample is None:
                event.parse_failure = "sample"
            return event

        if body.startswith("ADV,"):
            parts = body.split(",")
            if len(parts) == 9:
                try:
                    seq, t_us, adv_event = (int(parts[index], 0) for index in (1, 2, 3))
                    addr_id = parts[4].lower()
                    rssi, data_status, data_len = (int(parts[index], 0) for index in (5, 6, 7))
                    payload = parts[8].lower()
                    # Keep malformed UART lines out of statistics rather than partially accepting them.
                    if (re.fullmatch(r"[0-9a-f]{8}", addr_id)
                            and -128 <= rssi <= 127
                            and 0 <= data_status <= 255
                            and 0 <= data_len <= 255
                            and re.fullmatch(r"[0-9a-f]*", payload)
                            and len(payload) == data_len * 2):
                        event.adv = AdvRecord(seq, t_us, adv_event, addr_id, rssi,
                                              data_status, data_len, payload, stamp)
                except ValueError:
                    pass
            if event.adv is None:
                event.parse_failure = "adv"
            return event

        if body.startswith("# MON"):
            event.monitor_counters = self._ints(body)

        if "# SUM" in body:
            event.counters = self._ints(body)

        before = asdict(self.signature)
        if "role=" in body:
            values = dict(KV.findall(body))
            self.signature.role = values.get("role", self.signature.role)
            if self.signature.role == "monitor":
                # A fresh boot signature invalidates readiness retained by a resumed PC session.
                self._monitor_seek_enabled = False
                self.signature.measurement_ready = False
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
            if self.signature.link == "sle-passive-seek":
                integers = self._ints(body)
                self.signature.seek_interval = integers.get("seek_intv", self.signature.seek_interval)
                self.signature.seek_window = integers.get("seek_win", self.signature.seek_window)
                self.signature.filter_duplicates = integers.get("filter_dup", self.signature.filter_duplicates)
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
        if "[MON] seek enabled" in body:
            self._monitor_seek_enabled = self._ints(body).get("status") == 0
        self._refresh_monitor_ready()
        event.signature_changed = before != asdict(self.signature)
        return event


def percentile(values: list[int], p: float) -> Optional[int]:
    """NumPy-compatible linear percentile, rounded to the nearest microsecond."""
    if not values:
        return None
    ordered = sorted(values)
    position = min(len(ordered) - 1, max(0.0, (len(ordered) - 1) * p))
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return int(round(value))


@dataclass
class Session:
    signature: PlatformSignature = field(default_factory=PlatformSignature)
    samples: list[Sample] = field(default_factory=list)
    adv_records: list[AdvRecord] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    sum_frames_seen: int = 0
    latest_sum_sample_count: Optional[int] = None
    # 样本序号回退 = 板卡在一次会话中重启过。只认这个信号：
    # SUM 的 n 回退不能单独作证——打开串口时缓冲区里常残留上一次上电的 SUM，
    # 会造成“样本只有一次上电、却被判成多次”的假阳性（2026-11-15 全量回归发现）。
    board_restarts: int = 0
    csv_rows_total: int = 0
    csv_rows_skipped: int = 0
    parse_failures: dict[str, int] = field(default_factory=dict)
    monitor_counters: dict[str, int] = field(default_factory=dict)
    raw_lines: list[str] = field(default_factory=list)

    def add(self, event: ParsedEvent, signature: PlatformSignature) -> None:
        self.signature = PlatformSignature(**asdict(signature))
        if event.raw_line:
            self.raw_lines.append(event.raw_line)
        if event.sample:
            previous = self.samples[-1].seq if self.samples else None
            self.samples.append(event.sample)
            # 序号回退 32 以上只可能是板卡重启；回退一两格是串口乱序，不能当重启。
            if previous is not None:
                delta = (event.sample.seq - previous) & 0xFFFF
                if delta >= 0x8000 and (0x10000 - delta) >= 32:
                    self.board_restarts += 1
        if event.adv:
            self.adv_records.append(event.adv)
        if event.counters:
            self.counters = event.counters
            self.sum_frames_seen += 1
            self.latest_sum_sample_count = len(self.samples)
        if event.monitor_counters:
            self.monitor_counters = event.monitor_counters
        if event.parse_failure:
            self.parse_failures[event.parse_failure] = self.parse_failures.get(event.parse_failure, 0) + 1

    def monitor_metrics(self) -> dict[str, object]:
        """Aggregate passive reports without merging different SLE event types."""
        devices: dict[str, dict[str, object]] = {}
        for row in self.adv_records:
            item = devices.setdefault(row.addr_id, {"records": [], "events": {}})
            item["records"].append(row)  # type: ignore[union-attr]
            event_rows = item["events"].setdefault(row.event, [])  # type: ignore[union-attr]
            event_rows.append(row)

        result_devices: dict[str, object] = {}
        for addr_id, item in devices.items():
            rows: list[AdvRecord] = item["records"]  # type: ignore[assignment]
            valid_rssi = [row.rssi_dbm for row in rows if row.rssi_dbm != 127]
            event_metrics: dict[str, object] = {}
            for event, event_rows in item["events"].items():  # type: ignore[union-attr]
                # The firmware clock is uint32 microseconds; subtraction must tolerate wrap.
                intervals = [((event_rows[i].t_us - event_rows[i - 1].t_us) & 0xFFFFFFFF)
                             for i in range(1, len(event_rows))
                             if event_rows[i].seq > event_rows[i - 1].seq]
                event_metrics[str(event)] = {
                    "n": len(event_rows),
                    "interval_p50_us": percentile(intervals, .5),
                    "interval_p90_us": percentile(intervals, .9),
                    "interval_p99_us": percentile(intervals, .99),
                    "interval_max_us": max(intervals) if intervals else None,
                }
            result_devices[addr_id] = {
                "n": len(rows),
                "rssi_min_dbm": min(valid_rssi) if valid_rssi else None,
                "rssi_p10_dbm": percentile(valid_rssi, .1),
                "rssi_p50_dbm": percentile(valid_rssi, .5),
                "rssi_p90_dbm": percentile(valid_rssi, .9),
                "rssi_max_dbm": max(valid_rssi) if valid_rssi else None,
                "invalid_rssi": len(rows) - len(valid_rssi),
                "events": event_metrics,
            }

        seqs = [row.seq for row in self.adv_records]
        gaps = duplicates = out_of_order = resets = 0
        for previous, current in zip(seqs, seqs[1:]):
            if current == previous:
                duplicates += 1
            elif current == 1 and previous != 0:
                resets += 1
            elif current > previous + 1:
                gaps += current - previous - 1
            elif current < previous:
                out_of_order += 1
        return {
            "n": len(self.adv_records),
            "devices": result_devices,
            "sequence": {"gaps": gaps, "duplicates": duplicates,
                         "out_of_order": out_of_order, "resets": resets},
            "board_latest_mon": self.monitor_counters,
        }

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
            "board_sample_span_s": self.board_sample_span_seconds(),
        }
        for key in ("sent", "recv", "to", "dup", "ooo", "bad", "drop", "rx_drop",
                    "send_err", "cfm_err", "dlt_neg", "loss_ppm"):
            result[key] = self.counters.get(key)
        result["sum_frames_seen"] = self.sum_frames_seen
        result["samples_since_latest_sum"] = (
            None if self.latest_sum_sample_count is None
            else len(self.samples) - self.latest_sum_sample_count
        )
        result["csv_rows_total"] = self.csv_rows_total
        result["csv_rows_skipped"] = self.csv_rows_skipped
        result["parse_failures"] = dict(self.parse_failures)
        result["pc_sequence"] = self.sample_sequence_metrics()
        result["board_restarts"] = self.board_restarts
        return result

    def sample_sequence_metrics(self) -> dict[str, int]:
        gaps = duplicates = out_of_order = wraps = 0
        for previous, current in zip(self.samples, self.samples[1:]):
            delta = (current.seq - previous.seq) & 0xFFFF
            if delta == 0:
                duplicates += 1
            elif delta < 0x8000:
                if current.seq < previous.seq:
                    wraps += 1
                if delta > 1:
                    gaps += delta - 1
            else:
                out_of_order += 1
        return {"gaps": gaps, "duplicates": duplicates, "out_of_order": out_of_order,
                "wraps": wraps}

    def sample_span_seconds(self) -> float:
        stamps = [row.wall_time for row in self.samples if row.wall_time is not None]
        if len(stamps) < 2:
            return 0.0
        return max(0.0, (max(stamps) - min(stamps)).total_seconds())

    def board_sample_span_seconds(self) -> Optional[float]:
        """Elapsed initiator time, summing uint32 deltas only across forward sample order.

        板卡重启过就返回 None：此时样本来自不同次上电，跨重启累加的时间没有物理意义。
        """
        if self.board_restarts:
            return None
        elapsed_us = 0
        for previous, current in zip(self.samples, self.samples[1:]):
            seq_delta = (current.seq - previous.seq) & 0xFFFF
            if 0 < seq_delta < 0x8000:
                elapsed_us += (current.t1_us - previous.t1_us) & 0xFFFFFFFF
        return elapsed_us / 1_000_000.0

    def monitor_span_seconds(self) -> float:
        stamps = [row.wall_time for row in self.adv_records if row.wall_time is not None]
        if len(stamps) < 2:
            return 0.0
        return max(0.0, (max(stamps) - min(stamps)).total_seconds())

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
        existing = [path for path in (raw_path, csv_path, meta_path, report_path) if path.exists()]
        if existing:
            names = ", ".join(path.name for path in existing)
            raise FileExistsError(f"拒绝覆盖既有实验文件：{names}")
        with raw_path.open("x", encoding="utf-8") as handle:
            handle.write("\n".join(self.raw_lines) + ("\n" if self.raw_lines else ""))
        with csv_path.open("x", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            if self.adv_records and not self.samples:
                writer.writerow(("seq", "t_us", "event", "addr_id", "rssi_dbm", "data_status",
                                 "data_len", "payload_hex", "wall_time"))
                for row in self.adv_records:
                    writer.writerow((row.seq, row.t_us, row.event, row.addr_id, row.rssi_dbm,
                                     row.data_status, row.data_len, row.payload_hex,
                                     row.wall_time.isoformat(timespec="milliseconds") if row.wall_time else ""))
            else:
                writer.writerow(("seq", "t1_us", "t4_us", "rtt_us", "bproc_us", "wall_time"))
                for row in self.samples:
                    writer.writerow((row.seq, row.t1_us, row.t4_us, row.rtt_us, row.bproc_us,
                                     row.wall_time.isoformat(timespec="milliseconds") if row.wall_time else ""))
        metrics = self.metrics()
        payload = {"platform_signature": asdict(self.signature), "pc_exact_metrics": metrics,
                   "monitor_metrics": self.monitor_metrics(), "board_latest_sum": self.counters,
                   "sum_frames_seen": self.sum_frames_seen,
                   "samples_since_latest_sum": metrics["samples_since_latest_sum"]}
        with meta_path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        with report_path.open("x", encoding="utf-8") as handle:
            handle.write(self.markdown_report())
        return raw_path, csv_path, meta_path, report_path

    def markdown_report(self) -> str:
        if self.adv_records and not self.samples:
            return self.monitor_markdown_report()
        m = self.metrics()
        def ms(key: str) -> str:
            value = m[key]
            return "—" if value is None else f"{float(value) / 1000.0:.3f} ms"
        def seconds(key: str) -> str:
            value = m.get(key)
            return "—" if value is None else f"{float(value):.3f}"

        def counter(key: str) -> str:
            value = m[key]
            return "—" if value is None else str(value)
        sum_status = (
            "未收到板端 SUM；板端异常计数未知，不能解释为零"
            if self.sum_frames_seen == 0
            else f"已收到 {self.sum_frames_seen} 帧；最近 SUM 后还有 "
                 f"{m['samples_since_latest_sum']} 条样本"
        )
        return f"""# LinkScope 实验报告

## 平台签名

`{self.signature.compact()}`

## PC 原始样本精确统计

| 指标 | 数值 |
|---|---:|
| 有效样本 | {m['n']} |
| 样本记录时长 | {seconds('sample_span_s')} s |
| 板端发包时间跨度 | {seconds('board_sample_span_s')} s |
| 板卡重启次数（按 SUM 的 n 回退判定） | {m['board_restarts']} |
| RTT p50 | {ms('p50_us')} |
| RTT p90 | {ms('p90_us')} |
| RTT p99 | {ms('p99_us')} |
| RTT p99.9 | {ms('p999_us')} |
| RTT max | {ms('max_us')} |
| RTT mean | {ms('mean_us')} |
| jitter | {ms('jitter_us')} |
| timeout / duplicate / out-of-order | {counter('to')} / {counter('dup')} / {counter('ooo')} |
| bad / ring drop / rx drop / send error / write-cfm error | {counter('bad')} / {counter('drop')} / {counter('rx_drop')} / {counter('send_err')} / {counter('cfm_err')} |
| 方向时延负值归零计数（`dlt` 跨时钟，不可作为时延） | {counter('dlt_neg')} |
| CSV总行数 / 跳过坏行 | {m['csv_rows_total']} / {m['csv_rows_skipped']} |
| PC解析失败 | `{json.dumps(m['parse_failures'], ensure_ascii=False)}` |
| PC序列诊断 | `{json.dumps(m['pc_sequence'], ensure_ascii=False)}` |

## 板端计数器状态

{sum_status}

## 板端最近一条 SUM

```json
{json.dumps(self.counters, ensure_ascii=False, indent=2)}
```

## 口径

- PC 分位数使用线性插值（与 NumPy 默认 percentile 一致），最终四舍五入到 1 µs。

RTT 使用 initiator 板端同一时钟的 `t4-t1`；PC 串口到达时间不参与链路计时。PC 分位数由已落盘原始样本精确排序得到；板端 SUM 使用内存受限的对数直方图插值，两者允许存在小幅差异。`jitter` 在两端均定义为相邻 RTT 差分绝对值的平均值。板端未上报或当前固件不支持的计数显示为 `—`，绝不等同于实测零。本报告描述当前固件、SDK、配置与环境，不外推为协议标准的固定性能。
"""

    def monitor_markdown_report(self) -> str:
        metrics = self.monitor_metrics()
        rows = []
        for addr_id, device in metrics["devices"].items():
            events = ", ".join(f"{key}: {value['n']}" for key, value in device["events"].items())
            rows.append(
                f"| `{addr_id}` | {device['n']} | {device['rssi_min_dbm']} / {device['rssi_p50_dbm']} / "
                f"{device['rssi_max_dbm']} | {device['invalid_rssi']} | {events} |"
            )
        device_table = "\n".join(rows) or "| — | 0 | — | 0 | — |"
        return f"""# LinkScope SLE被动监测报告

## 平台签名

`{self.signature.compact()}`

## 会话摘要

- 有效ADV报告：{metrics['n']}
- QCOM记录跨度：{self.monitor_span_seconds():.3f} s
- 序列诊断：`{json.dumps(metrics['sequence'], ensure_ascii=False)}`
- 板端最近MON：`{json.dumps(metrics['board_latest_mon'], ensure_ascii=False)}`

| 地址ID | 报告数 | RSSI min / p50 / max (dBm) | 无效RSSI | 各event报告数 |
|---|---:|---:|---:|---|
{device_table}

## 口径

报告间隔只在同一地址ID、同一event类型内计算，且跨复位不连算。它是SDK seek回调的报告到达间隔，
不是空口广播周期；没有广播端序号时，序列诊断也不能解释为空口丢包率。
"""


def load_log(path: Path) -> Session:
    if path.suffix.lower() == ".csv":
        return load_csv(path)
    parser = LineParser(source=str(path))
    session = Session()
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            session.add(parser.feed(line), parser.signature)
    return session


def load_csv(path: Path) -> Session:
    session = Session()
    session.signature.source = str(path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            session.csv_rows_total += 1
            try:
                wall = datetime.fromisoformat(row["wall_time"]) if row.get("wall_time") else None
                if "addr_id" in row:
                    session.adv_records.append(AdvRecord(
                        seq=int(row["seq"]), t_us=int(row["t_us"]), event=int(row["event"]),
                        addr_id=row["addr_id"].lower(), rssi_dbm=int(row["rssi_dbm"]),
                        data_status=int(row["data_status"]), data_len=int(row["data_len"]),
                        payload_hex=row["payload_hex"].lower(), wall_time=wall,
                    ))
                else:
                    base = [int(row[key]) for key in
                            ("seq", "t1_us", "t4_us", "rtt_us", "bproc_us")]
                    # 旧证据文件带 dlt_us 列（跨时钟差值，不可用于比较），新导出不再写；
                    # 两种都要能读，缺列时把 dlt 记为 None 而不是 0，避免伪造“测到 0”。
                    dlt_raw = row.get("dlt_us")
                    dlt = int(dlt_raw) if dlt_raw not in (None, "") else None
                    if not LineParser.valid_sample_values(base + [dlt if dlt is not None else 0]):
                        raise ValueError("sample invariant failed")
                    session.samples.append(Sample(*base, dlt_us=dlt, wall_time=wall))
            except (KeyError, TypeError, ValueError):
                session.csv_rows_skipped += 1
                continue
    meta_path = path.with_suffix(".json")
    if meta_path.exists():
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            signature = payload.get("platform_signature", {})
            signature["source"] = str(path)
            session.signature = PlatformSignature(**{k: v for k, v in signature.items()
                                                     if k in PlatformSignature.__dataclass_fields__})
            session.counters = payload.get("board_latest_sum", {})
            failures = payload.get("pc_exact_metrics", {}).get("parse_failures", {})
            if isinstance(failures, dict):
                session.parse_failures = {str(k): int(v) for k, v in failures.items()}
            session.sum_frames_seen = int(payload.get("sum_frames_seen", 1 if session.counters else 0))
            lag = payload.get("samples_since_latest_sum")
            if session.sum_frames_seen and isinstance(lag, int):
                session.latest_sum_sample_count = max(0, len(session.samples) - lag)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
    return session


def feed_lines(lines: Iterable[str], source: str = "memory") -> Session:
    parser = LineParser(source=source)
    session = Session()
    for line in lines:
        session.add(parser.feed(line), parser.signature)
    return session


def count_csv_rows(path: Path | str) -> int:
    """已导出 CSV 的数据行数（不含表头）；读不到返回 -1，由调用方区分“没检查”与“0 行”。"""
    try:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
            rows = sum(1 for _ in csv.reader(handle))
    except OSError:
        return -1
    return max(0, rows - 1)


def evaluate_evidence(record: dict, csv_rows: int | None = None) -> dict:
    """导出后自检：只陈述可比对的事实，不给“链路稳不稳”的结论。

    fail 表示证据本身不成立（行数与样本数对不上）；warn 表示某项证据缺失或存在
    可解释的异常（缺口、坏行、未上报 SUM），需要人看一眼再决定能否入库。
    """
    items: list[dict] = []

    def add(name: str, status: str, detail: str) -> None:
        items.append({"name": name, "status": status, "detail": detail})

    analysis = record.get("analysis_samples")
    if csv_rows is None:
        add("csv_rows", "warn", "未提供 CSV 行数，样本数一致性未检查")
    elif analysis is None:
        add("csv_rows", "warn", f"缺少 analysis_samples，CSV 实有 {csv_rows} 行")
    elif csv_rows == analysis:
        add("csv_rows", "pass", f"CSV {csv_rows} 行 == 样本数 {analysis}")
    else:
        add("csv_rows", "fail", f"CSV {csv_rows} 行 != 样本数 {analysis}")

    sequence = record.get("pc_sequence") or {}
    gaps = int(sequence.get("gaps") or 0)
    duplicates = int(sequence.get("duplicates") or 0)
    out_of_order = int(sequence.get("out_of_order") or 0)
    restarts = int(record.get("board_restarts") or 0)
    if not analysis:
        add("sequence", "warn", "本轮没有有效样本")
    elif restarts:
        add("sequence", "warn",
            f"板卡重启 {restarts} 次，样本来自 {restarts + 1} 次上电，"
            f"不可当作单轮：gaps={gaps} dup={duplicates} ooo={out_of_order}")
    elif gaps or duplicates or out_of_order:
        add("sequence", "warn", f"gaps={gaps} dup={duplicates} ooo={out_of_order}")
    else:
        add("sequence", "pass", "PC 端序号连续、无重复、无乱序")

    failures = record.get("parse_failures") or {}
    parsed = sum(int(value) for value in failures.values() if isinstance(value, int))
    if parsed:
        add("parse", "warn", f"坏行 {parsed} 条：{json.dumps(failures, ensure_ascii=False)}")
    else:
        add("parse", "pass", "没有无法解析的行")

    signature = record.get("platform_signature") or {}
    if signature.get("cfg") is None:
        add("signature", "warn", "平台签名不完整，无法核对本轮实验配置")
    else:
        add("signature", "pass", f"cfg={signature.get('cfg')} role={signature.get('role')}")

    if not record.get("sum_frames_seen"):
        add("board_sum", "warn", "板端未上报 # SUM：板端计数器未知，不能当作零")
    else:
        add("board_sum", "pass", f"收到 {record.get('sum_frames_seen')} 帧 SUM")

    identity = record.get("firmware_identity") or {}
    missing = [name for name, item in identity.items()
               if not item or item.get("status") != "provided"]
    inherited = [name for name, item in identity.items() if item and item.get("inherited")]
    if identity and not missing and not inherited:
        add("firmware", "pass", "两端固件哈希齐全，且都是本轮登记")
    elif identity and not missing:
        add("firmware", "warn",
            f"沿用上一会话登记的固件路径（{', '.join(inherited)}），本轮没有重新确认")
    else:
        add("firmware", "warn", f"固件身份缺失：{', '.join(missing) if missing else '未填写'}")

    source = record.get("source_identity") or {}
    # run.json 写的是 git_head；保留 head 兼容外部构造的最小 record。
    head = str(source.get("git_head") or source.get("head") or "")
    if head:
        add("source", "pass", f"git HEAD {head[:12]}{'（有未提交改动）' if source.get('git_dirty', source.get('dirty')) else ''}")
    else:
        add("source", "warn", "没有代码身份（git HEAD 缺失），复核时无法定位版本")

    raw_bytes = record.get("analysis_raw_bytes")
    if raw_bytes:
        add("raw_bytes", "pass", f"{raw_bytes} 字节原始字节")
    else:
        add("raw_bytes", "warn", "没有 .raw 原始字节，逐字节复核不可能")

    failed = [item for item in items if item["status"] == "fail"]
    warned = [item for item in items if item["status"] == "warn"]
    status = "fail" if failed else ("warn" if warned else "pass")
    return {"status": status, "items": items}


def format_evidence(checks: dict) -> str:
    """把自检结果压成一行给控制台/弹窗用。"""
    bad = [item for item in checks.get("items", []) if item.get("status") != "pass"]
    head = f"自检：{checks.get('status', 'unknown').upper()}"
    if not bad:
        return head + "（全部通过）"
    return head + "；" + "；".join(f"{item['name']}={item['status']} {item['detail']}"
                                   for item in bad)
