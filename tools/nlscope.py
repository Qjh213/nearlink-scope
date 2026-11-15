#!/usr/bin/env python3
"""LinkScope 桌面端：串口实时分析、日志回放与报告导出。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from dataclasses import asdict
from pathlib import Path

from nlscope_core import (LineParser, Session, count_csv_rows, evaluate_evidence,
                          format_evidence, format_preflight, load_log, preflight_items)
from serial_terminal import BootGateDecision, BootSessionGate, SERIAL_READ_TIMEOUT, pump_serial_lines, recovery_path
from serial_reader import RawByteSink
from experiment_specs import ExperimentSpec, expected_mapping, load_specs
from wizard import ExperimentWizard
from adapters import AdapterSpec, format_import_report, import_csv

APP_VERSION = "M6.0.1"


def _force_utf8(streams=None) -> None:
    """把文本流切到 UTF-8；被打包成窗口程序（console=False）时补上空写入器。

    两件事都为了 CLI 模式不因为"输出"而崩：
    1. Windows 控制台/重定向的默认编码可能是 cp1252（CI 里就是），打印中文会抛
       UnicodeEncodeError，把一次正常的"导出成功"变成失败。
    2. PyInstaller 的窗口模式把 sys.stdout/sys.stderr 置为 None，一句 print 就抛
       AttributeError；换成等价的空写入器后，CLI 仍能写文件并按退出码报告结果。
    没有 reconfigure 的流（如测试里的替身）原样跳过。
    """
    targets = (sys.stdout, sys.stderr) if streams is None else streams
    for index, stream in enumerate(targets):
        if stream is None:
            if streams is None:
                sink = open(os.devnull, "w", encoding="utf-8")
                if index == 0:
                    sys.stdout = sink
                else:
                    sys.stderr = sink
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            continue


_force_utf8()


def _load_default_specs() -> dict[str, ExperimentSpec]:
    """规格文件缺失/损坏时不能拖垮整个程序：退回两条通用规格并保留错误原因。"""
    global SPECS_LOAD_ERROR
    try:
        return load_specs()
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        SPECS_LOAD_ERROR = f"{type(exc).__name__}: {exc}"
        return {
            "仅原始日志（不进入统计）": ExperimentSpec("仅原始日志（不进入统计）", "raw", {}),
            "自动识别平台签名": ExperimentSpec("自动识别平台签名", "auto", {}),
        }


SPECS_LOAD_ERROR: str | None = None
DEFAULT_EXPERIMENT_SPECS = _load_default_specs()
M5_PROFILES = expected_mapping(DEFAULT_EXPERIMENT_SPECS)  # compatibility for existing API/tests


def repository_identity() -> dict[str, object]:
    """Best-effort source identity; packaged builds honestly report unavailable git data."""
    root = Path(__file__).resolve().parent.parent
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
            timeout=2, check=True,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True,
            timeout=2, check=True,
        ).stdout.strip())
        return {"git_head": head, "git_dirty": dirty, "source": "git"}
    except (OSError, subprocess.SubprocessError):
        return {"git_head": None, "git_dirty": None, "source": "unavailable"}


def file_identity(path: str) -> dict[str, object]:
    """Return a user-selected firmware package identity without trusting its filename."""
    if not path:
        return {"path": None, "sha256": None, "size": None, "status": "not_provided"}
    source = Path(path)
    digest = hashlib.sha256()
    try:
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return {"path": str(source.resolve()), "sha256": digest.hexdigest(),
                "size": source.stat().st_size, "status": "provided"}
    except OSError as exc:
        return {"path": str(source), "sha256": None, "size": None,
                "status": "unreadable", "error": str(exc)}


def discover_serial_ports() -> list[tuple[str, str]]:
    """返回 [(device, display)]；无 pyserial 或无设备时返回空列表。"""
    try:
        from serial.tools import list_ports  # type: ignore
    except ImportError:
        return []

    def key(item) -> tuple[str, int, str]:
        match = re.fullmatch(r"([A-Za-z]+)(\d+)", item.device or "")
        if match:
            return match.group(1).upper(), int(match.group(2)), item.device
        return item.device.upper(), 0, item.device

    found = []
    for item in sorted(list_ports.comports(), key=key):
        description = (item.description or "串口设备").strip()
        found.append((item.device, f"{item.device} — {description}"))
    return found


def print_summary(session: Session) -> None:
    caps = session.capabilities()
    if session.adv_records:
        metrics = session.monitor_metrics()
        print(session.signature.compact())
        print(f"能力：{caps['label']}")
        print(f"adv={metrics['n']} devices={len(metrics['devices'])} sequence={metrics['sequence']}")
        return
    metrics = session.metrics()
    def counter(key: str) -> str:
        value = metrics[key]
        return "—" if value is None else str(value)
    print(session.signature.compact())
    print(f"能力：{caps['label']}")
    print(
        f"n={metrics['n']} p50={metrics['p50_us']}us p90={metrics['p90_us']}us "
        f"p99={metrics['p99_us']}us p999={metrics['p999_us']}us max={metrics['max_us']}us "
        f"to={counter('to')} dup={counter('dup')} ooo={counter('ooo')} "
        f"bad={counter('bad')} drop={counter('drop')}"
    )


def session_evidence_record(session: Session) -> dict:
    """CLI 与单窗口导出没有 run.json，用会话内已有事实跑同一套自检。"""
    return {
        "analysis_samples": len(session.samples),
        "pc_sequence": session.sample_sequence_metrics(),
        "parse_failures": session.parse_failures,
        "platform_signature": asdict(session.signature),
        "sum_frames_seen": session.sum_frames_seen,
        "board_restarts": session.board_restarts,
    }


def export_cli(session: Session, directory: Path, stem: str) -> int:
    """CLI 导出：拒绝覆盖是正常路径（返回码 5），不能以 traceback 结束。"""
    try:
        paths = session.export(directory, stem)
    except FileExistsError as exc:
        print(f"导出失败：{exc}", file=sys.stderr)
        return 5
    print("\n".join(str(path) for path in paths))
    print(format_evidence(evaluate_evidence(session_evidence_record(session),
                                            count_csv_rows(paths[1]))))
    return 0


def reproduce_markdown(session: Session, run_dir: Path, recorded: dict | None,
                       source: Path, recorded_metrics: dict | None = None,
                       csv_rows: int | None = None) -> str:
    """把"这一轮凭什么得出这个结论"压成一份可独立阅读的报告。"""
    metrics = session.metrics()
    caps = session.capabilities()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    source_record = (recorded or {}).get("source_identity") or {}
    firmware = (recorded or {}).get("firmware_identity") or {}
    spec = (recorded or {}).get("experiment_spec") or {}
    saved_metrics = recorded_metrics or {}

    def identity_block(role: str) -> str:
        item = firmware.get(role) or {}
        if not item:
            return f"- {role}: 未登记"
        name = Path(item.get("path") or "?").name
        return (f"- {role}: `{name}` sha256={item.get('sha256') or '?'} "
                f"status={item.get('status') or '?'}"
                + ("（沿用上一会话登记）" if item.get("inherited") else ""))

    rows = []
    for key, label, unit in (("n", "有效样本", ""), ("p50_us", "p50", " µs"),
                             ("p90_us", "p90", " µs"), ("p99_us", "p99", " µs"),
                             ("max_us", "max", " µs")):
        replay = metrics.get(key)
        saved = saved_metrics.get(key)
        if saved is None:
            verdict = "无记录（没找到 initiator_*.json）" if recorded_metrics is None else "记录缺失"
        elif replay == saved:
            verdict = "一致"
        else:
            verdict = f"**不一致**（差 {None if replay is None else replay - saved}）"
        rows.append(f"| {label} | {saved if saved is not None else '—'}{unit} | "
                    f"{replay if replay is not None else '—'}{unit} | {verdict} |")

    counters = []
    for key in ("to", "dup", "ooo", "bad", "drop", "rx_drop", "send_err", "cfm_err"):
        saved = saved_metrics.get(key)
        replay = metrics.get(key)
        counters.append(f"{key}={replay if replay is not None else '—'}"
                        + ("" if saved is None or saved == replay else f"（记录 {saved}）"))

    capability_rows = "\n".join(
        f"| {item['label']} | {'可用' if item['available'] else '不可用'} | {item['reason'] or '—'} |"
        for item in caps["items"])
    if csv_rows is None:
        sibling = next(iter(sorted(run_dir.glob("initiator_*.csv"))), None)
        csv_rows = count_csv_rows(sibling) if sibling is not None else None
    raw_file = next(iter(sorted(run_dir.glob("initiator_*.raw"))), None)
    raw_bytes = raw_file.stat().st_size if raw_file is not None else None
    # 自检用"记录 + 重放"合并后的 record：身份来自 run.json，样本数与序号来自这次重放。
    checks = format_evidence(evaluate_evidence({
        "analysis_samples": len(session.samples),
        "pc_sequence": session.sample_sequence_metrics(),
        "parse_failures": session.parse_failures,
        "platform_signature": asdict(session.signature),
        "sum_frames_seen": session.sum_frames_seen,
        "board_restarts": session.board_restarts,
        "capabilities": caps,
        "source_identity": source_record,
        "firmware_identity": firmware,
        "analysis_raw_bytes": raw_bytes,
    }, csv_rows))
    return f"""# 复现报告：{run_dir.name}

本报告由 `tools/nlscope.py --reproduce` 生成，只读：不修改被复现目录里的任何文件。

## 源

| 项 | 值 |
|---|---|
| 日志 | `{source.name}` |
| 字节数 | {source.stat().st_size} |
| SHA-256 | `{digest}` |
| 记录轮次 | `{run_dir}` |

## 当时的身份（来自 run.json）

- 程序版本：`{(recorded or {}).get('app_version', '未记录')}`
- 代码身份：`{(source_record.get('git_head') or '未记录')}`，dirty=`{source_record.get('git_dirty')}`
- 实验规格：`{spec.get('name', '未记录')}`（mode={spec.get('mode')}）
{identity_block('initiator')}
{identity_block('responder')}

## 本次复现环境

- 复现程序版本：`{APP_VERSION}`
- 当前代码身份：`{repository_identity().get('git_head', '?')}`，dirty=`{repository_identity().get('git_dirty')}`
- 解析器：`nlscope_core` 当前实现（线性分位数、只认序号回退判重启）

## 统计能力

**{caps['label']}**（会话类型：{caps['session_type']}）

| 能力 | 状态 | 说明 |
|---|---|---|
{capability_rows}

## 重放结果与记录值对照

| 指标 | 记录值 | 重放值 | 判定 |
|---|---:|---:|---|
{chr(10).join(rows)}

板端计数器：{'; '.join(counters)}

## 自检

`{checks}`

## 限制

- 重放只能看到落盘的文本，看不到 `.raw` 原始字节；PC 侧 `dropped_bytes` 等接收层计数不在重放范围；
- 记录值来自当时的 `run.json`，若当时版本的分位数口径与当前不同，差异属于**口径变化**而非复现失败；
- 本报告不评价链路好坏，只回答"这份数据能否复现出同样的数字"。

## 复现命令

```
.venv\\Scripts\\python.exe tools\\nlscope.py --reproduce "{run_dir}"
```
"""


def reproduce_run(run_dir: Path, out_dir: Path | None = None) -> int:
    """对某个证据目录做只读复现：读日志与 run.json，把报告写到独立目录。"""
    candidates = sorted(run_dir.glob("initiator_*.log")) or sorted(run_dir.glob("*.log"))
    if not candidates:
        print(f"复现失败：{run_dir} 下没有 .log", file=sys.stderr)
        return 4
    source = candidates[0]
    recorded = None
    run_json = run_dir / "run.json"
    if run_json.exists():
        try:
            recorded = json.loads(run_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"警告：run.json 读不了（{exc}），只做重放不做对照", file=sys.stderr)
    session = load_log(source)
    # 记录值不在 run.json 里，而在同目录的 initiator_*.json["pc_exact_metrics"]。
    recorded_metrics = None
    metric_files = sorted(run_dir.glob("initiator_*.json"))
    if metric_files:
        try:
            recorded_metrics = json.loads(
                metric_files[0].read_text(encoding="utf-8")).get("pc_exact_metrics")
        except (OSError, ValueError) as exc:
            print(f"警告：{metric_files[0].name} 读不了（{exc}），只做重放不做对照", file=sys.stderr)
    report = reproduce_markdown(session, run_dir, recorded, source, recorded_metrics)
    target_dir = out_dir if out_dir is not None else Path("out") / "reproduce"
    target = target_dir / f"{run_dir.name}-reproduce.md"
    if target.exists():
        print(f"复现失败：{target} 已存在，拒绝覆盖", file=sys.stderr)
        return 5
    target_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(report, encoding="utf-8")
    print_summary(session)
    print(f"已写出 {target}")
    return 0 if (session.samples or session.adv_records) else 2


class FirmwareRegistry:
    """固件登记的"是否已用过"判断。

    `provided` 只说明本进程登记过某个路径；但同一份登记连续导出两轮，第二轮并不能
    证明板子上还是那个包。这里记下每次选择的时间戳，并在导出后把该次选择标记为
    "已消费"：下一轮若没重新选择，导出的 `inherited` 才为真，由自检给出 warn。
    """

    def __init__(self) -> None:
        self._selected: dict[str, tuple[datetime, int]] = {}
        self._consumed: set[tuple[str, str, int]] = set()
        self._serial = 0

    def select(self, role: str, now: datetime | None = None) -> None:
        self._serial += 1
        self._selected[role] = (now or datetime.now(), self._serial)

    def selected_at(self, role: str) -> datetime | None:
        entry = self._selected.get(role)
        return entry[0] if entry else None

    def identity(self, role: str, path: str) -> dict:
        item = file_identity(path)
        stamp = self.selected_at(role)
        item["selected_at"] = stamp.isoformat(timespec="seconds") if stamp else None
        item["inherited"] = bool(path) and self._key(role, path) in self._consumed
        return item

    def consume(self, role: str, path: str) -> None:
        if path:
            self._consumed.add(self._key(role, path))

    def _key(self, role: str, path: str) -> tuple[str, str, int]:
        entry = self._selected.get(role)
        # 序号进 key：同一个路径重新选一次就算"重新确认"，不再算继承。
        return (role, path, entry[1] if entry else 0)


class ScopeApp:
    def __init__(self, root) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self.root.title(f"NearLink LinkScope · {APP_VERSION}")
        self.root.geometry("1280x820")
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)
        self.experiment_specs = dict(DEFAULT_EXPERIMENT_SPECS)
        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.source_id = 0
        self.source_label = "idle"
        self.strict_live_gate = False
        self.pre_ready_samples = 0
        self.capture_started_monotonic: float | None = None
        self.capture_frozen_seconds = 0.0
        self.paused = False
        self.active_port = ""
        self.session = Session()
        self.parser = LineParser()
        self.wizard = ExperimentWizard()
        self.wizard_window = None
        self.wizard_text = None
        self.last_draw_n = -1
        self.last_draw_monitor_n = -1
        self.last_draw_at = 0.0
        self.aux_terminals = []
        self.serial_workbench = None
        self.serial_workbench_tree = None
        self.close_aux_button = None
        self.serial_workbench_polling = False
        self.analysis_console = None
        self.main_serial_handle = None
        self.main_serial_lock = threading.Lock()
        self.main_worker = None
        self.main_raw_journal = None
        self.main_recovery_file = None
        self.main_boot_gate = BootSessionGate()

        controls = ttk.Frame(root, padding=8)
        controls.pack(fill="x")
        ttk.Label(controls, text="串口").pack(side="left")
        self.port = tk.StringVar(value="")
        self.port_map: dict[str, str] = {}
        self.port_combo = ttk.Combobox(controls, textvariable=self.port, width=31, state="readonly")
        self.port_combo.pack(side="left", padx=(4, 4))
        ttk.Button(controls, text="刷新", command=self.refresh_ports, width=6).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="开始串口", command=self.start_serial).pack(side="left")
        ttk.Button(controls, text="开始前检查", command=self.show_preflight).pack(side="left", padx=6)
        ttk.Button(controls, text="实验向导", command=self.open_wizard).pack(side="left")
        ttk.Button(controls, text="打开日志", command=self.open_log).pack(side="left", padx=6)
        ttk.Button(controls, text="停止", command=self.stop).pack(side="left")
        ttk.Button(controls, text="清空", command=self.clear).pack(side="left", padx=6)
        ttk.Button(controls, text="导出", command=self.export).pack(side="left")
        ttk.Button(controls, text="串口工作台", command=self.open_serial_workbench).pack(side="left", padx=6)
        self.status = tk.StringVar(value="就绪：可打开 M1/M2 QCOM 日志回放")
        ttk.Label(controls, textvariable=self.status).pack(side="right")

        experiment = ttk.Frame(root, padding=(10, 2, 10, 5))
        experiment.pack(fill="x")
        ttk.Label(experiment, text="预期实验").pack(side="left")
        self.expected_profile_name = tk.StringVar(value="M5 cfg500 基线 40B/40Hz")
        self.expected_profile_combo = ttk.Combobox(
            experiment, textvariable=self.expected_profile_name, values=list(M5_PROFILES),
            state="readonly", width=34,
        )
        self.expected_profile_combo.pack(side="left", padx=6)
        ttk.Button(experiment, text="导入规格", command=self.import_experiment_specs).pack(side="left")
        self.expected_status = tk.StringVar(value="将校验role/link/cfg/payload/rate/window/timeout")
        ttk.Label(experiment, textvariable=self.expected_status).pack(side="left", padx=8)
        self.require_boot_gate = tk.BooleanVar(value=True)
        ttk.Checkbutton(experiment, text="等待 boot. 边界",
                        variable=self.require_boot_gate).pack(side="left", padx=(4, 0))
        self.initiator_firmware = tk.StringVar(value="")
        self.responder_firmware = tk.StringVar(value="")
        # 固件登记时间与会话起点用于判断"本轮是否真的重新确认过固件"：
        # 只在本进程启动时创建一次，新会话不清空（否则每轮都要点两次）。
        self.firmware_registry = FirmwareRegistry()
        self.session_started_at: datetime | None = None
        ttk.Button(experiment, text="发起端固件", command=lambda: self.select_firmware(
            self.initiator_firmware)).pack(side="left", padx=(8, 2))
        ttk.Button(experiment, text="响应端固件", command=lambda: self.select_firmware(
            self.responder_firmware)).pack(side="left")

        self.signature = tk.StringVar(value="平台签名：等待数据")
        ttk.Label(root, textvariable=self.signature, padding=(12, 8), background="#0d344f", foreground="white").pack(fill="x")

        metrics = ttk.Frame(root, padding=(8, 6))
        metrics.pack(fill="x")
        self.metric_vars = {}
        self.metric_labels = {}
        metric_names = ("采集时长", "n", "p50精确", "p90精确", "p99精确", "p99.9精确", "max",
                        "jitter", "timeout", "dup", "ooo", "bad", "drop")
        for index, name in enumerate(metric_names):
            box = ttk.Frame(metrics, padding=5)
            box.grid(row=index // 7, column=index % 7, sticky="ew")
            label = tk.StringVar(value=name)
            ttk.Label(box, textvariable=label, foreground="#587184").pack()
            var = tk.StringVar(value="—")
            ttk.Label(box, textvariable=var, font=("Segoe UI", 11, "bold")).pack()
            self.metric_vars[name] = var
            self.metric_labels[name] = label
        for column in range(7):
            metrics.columnconfigure(column, weight=1)

        charts = ttk.Frame(root, padding=8)
        charts.pack(fill="both", expand=True)
        self.time_canvas = tk.Canvas(charts, background="#f7fafc", highlightthickness=1, highlightbackground="#cad8e3")
        self.hist_canvas = tk.Canvas(charts, background="#f7fafc", highlightthickness=1, highlightbackground="#cad8e3")
        self.cdf_canvas = tk.Canvas(charts, background="#f7fafc", highlightthickness=1, highlightbackground="#cad8e3")
        self.time_canvas.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=4, pady=4)
        self.hist_canvas.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        self.cdf_canvas.grid(row=1, column=1, sticky="nsew", padx=4, pady=4)
        charts.rowconfigure((0, 1), weight=1)
        charts.columnconfigure((0, 1), weight=1)
        for canvas in (self.time_canvas, self.hist_canvas, self.cdf_canvas):
            canvas.bind("<Configure>", lambda _e: self.draw())

        self.root.after(100, self.poll)
        self.refresh_ports(preferred="COM3")

    def _live_aux_terminals(self):
        self.aux_terminals = [
            terminal for terminal in self.aux_terminals
            if terminal.window.winfo_exists()
        ]
        return self.aux_terminals

    def _reserved_serial_ports(self, current=None) -> set[str]:
        ports = set()
        if self.main_serial_handle is not None and self.active_port:
            ports.add(self.active_port)
        for terminal in self._live_aux_terminals():
            if terminal is not current and terminal.is_open and terminal.active_port:
                ports.add(terminal.active_port)
        return ports

    def open_aux_terminal(self):
        from serial_terminal import SerialTerminalWindow
        number = len(self._live_aux_terminals()) + 1
        holder = {}
        terminal = SerialTerminalWindow(
            self.root, discover_serial_ports, terminal_name=f"辅助串口 {number}",
            reserved_ports=lambda: self._reserved_serial_ports(holder.get("terminal")),
        )
        holder["terminal"] = terminal
        self.aux_terminals.append(terminal)
        terminal.window.bind(
            "<FocusIn>", lambda _event, selected=terminal: self._select_aux_terminal(selected), add="+"
        )
        self._refresh_workbench()
        return terminal

    def open_serial_workbench(self):
        if self.serial_workbench is not None and self.serial_workbench.winfo_exists():
            self.serial_workbench.lift()
            self.serial_workbench.focus_force()
            self._refresh_workbench()
            return self.serial_workbench

        window = self.tk.Toplevel(self.root)
        self.serial_workbench = window
        window.title("LinkScope · 串口工作台")
        window.geometry("760x430")
        toolbar = self.ttk.Frame(window, padding=10)
        toolbar.pack(fill="x")
        self.ttk.Button(toolbar, text="新建辅助串口", command=self.open_aux_terminal).pack(side="left")
        self.ttk.Button(toolbar, text="分析端终端", command=self.open_analysis_console).pack(side="left", padx=6)
        self.ttk.Button(toolbar, text="全部开始", command=self.start_dual).pack(side="left")
        self.ttk.Button(toolbar, text="全部停止", command=self.stop_dual).pack(side="left", padx=6)
        self.ttk.Button(toolbar, text="联合导出", command=self.export_dual).pack(side="left")
        self.ttk.Button(toolbar, text="刷新状态", command=self._refresh_workbench).pack(side="right")

        self.ttk.Label(
            window,
            text="主分析串口由主窗口选择；辅助串口可新建多个，每个端口只能被一个窗口占用。",
            padding=(12, 3),
        ).pack(fill="x")
        columns = ("name", "port", "state", "blocks", "bytes")
        tree = self.ttk.Treeview(window, columns=columns, show="headings", height=12)
        self.serial_workbench_tree = tree
        for key, title, width in (
            ("name", "终端", 150), ("port", "端口", 120), ("state", "状态", 110),
            ("blocks", "接收块", 100), ("bytes", "字节数", 110),
        ):
            tree.heading(key, text=title)
            tree.column(key, width=width, anchor="center")
        tree.pack(fill="both", expand=True, padx=10, pady=8)
        tree.bind("<Button-1>", self._toggle_aux_selection)
        tree.bind("<<TreeviewSelect>>", lambda _event: self._update_aux_actions())
        footer = self.ttk.Frame(window, padding=(10, 0, 10, 10))
        footer.pack(fill="x")
        self.close_aux_button = self.ttk.Button(
            footer, text="关闭选中终端", command=self._close_selected_aux, state="disabled"
        )
        self.close_aux_button.pack(side="left")
        self._refresh_workbench()
        self.serial_workbench_polling = True
        window.after(500, self._poll_workbench)
        return window

    def _refresh_workbench(self) -> None:
        tree = self.serial_workbench_tree
        if tree is None or not tree.winfo_exists():
            return
        selection = set(tree.selection())
        for item in tree.get_children():
            tree.delete(item)
        for terminal in self._live_aux_terminals():
            item_id = str(id(terminal))
            selected = terminal.active_port or terminal.port_map.get(terminal.port.get(), "未选择")
            tree.insert("", "end", iid=item_id, values=(
                terminal.terminal_name, selected, "已打开" if terminal.is_open else "未打开",
                terminal.rx_chunks, terminal.rx_bytes,
            ))
            if item_id in selection:
                tree.selection_add(item_id)
        self._update_aux_actions()

    def _poll_workbench(self) -> None:
        if self.serial_workbench is None or not self.serial_workbench.winfo_exists():
            self.serial_workbench_polling = False
            return
        self._refresh_workbench()
        self.serial_workbench.after(500, self._poll_workbench)

    def _selected_aux(self):
        tree = self.serial_workbench_tree
        if tree is None:
            return None
        selection = tree.selection()
        if not selection:
            return None
        selected_id = selection[0]
        return next(
            (terminal for terminal in self._live_aux_terminals() if str(id(terminal)) == selected_id), None
        )

    def _toggle_aux_selection(self, event):
        """让列表行支持再次单击取消选择，空白处单击也清除选择。"""
        tree = self.serial_workbench_tree
        if tree is None:
            return None
        item_id = tree.identify_row(event.y)
        selection = tree.selection()
        if not item_id:
            if selection:
                tree.selection_remove(*selection)
                self._update_aux_actions()
            return "break"
        if item_id in selection:
            tree.selection_remove(item_id)
            tree.focus("")
            self._update_aux_actions()
            return "break"
        return None

    def _select_aux_terminal(self, terminal) -> None:
        tree = self.serial_workbench_tree
        if tree is None or not tree.winfo_exists() or not terminal.window.winfo_exists():
            return
        item_id = str(id(terminal))
        if tree.exists(item_id):
            tree.selection_set(item_id)
            tree.focus(item_id)
            tree.see(item_id)
        self._update_aux_actions()

    def _update_aux_actions(self) -> None:
        if self.close_aux_button is not None and self.close_aux_button.winfo_exists():
            self.close_aux_button.configure(state="normal" if self._selected_aux() is not None else "disabled")

    def _close_selected_aux(self) -> None:
        terminal = self._selected_aux()
        if terminal is None:
            return
        terminal.close()
        self._refresh_workbench()

    def open_analysis_console(self):
        from serial_terminal import AnalysisSerialConsoleWindow
        if self.analysis_console is not None and self.analysis_console.window.winfo_exists():
            self.analysis_console.window.lift()
            self.analysis_console.window.focus_force()
            return self.analysis_console
        self.analysis_console = AnalysisSerialConsoleWindow(
            self.root, self.send_main_serial,
            port_callback=lambda: self.active_port if self.main_serial_handle is not None else "",
        )
        self._prepare_analysis_console()
        return self.analysis_console

    def _prepare_analysis_console(self) -> None:
        """Attach the raw mirror without ever changing or depending on the COM3 boot gate."""
        if self.analysis_console is None or not self.analysis_console.window.winfo_exists():
            return
        # 镜像终端不回填历史，但从打开窗口起无条件显示后续字节；boot门只约束主解析/统计链。
        self.analysis_console.wait_for_boot(clear=True)

    def send_main_serial(self, payload: bytes) -> int:
        with self.main_serial_lock:
            handle = self.main_serial_handle
            if handle is None or not getattr(handle, "is_open", False):
                raise RuntimeError("主分析串口未打开")
            return int(handle.write(payload))

    def start_dual(self) -> None:
        """Open responder terminal first, then start the gated analysis serial."""
        terminals = self._live_aux_terminals()
        if not terminals:
            terminals = [self.open_aux_terminal()]
        for terminal in terminals:
            # 每轮实验都重开辅助端口，清除驱动缓存并让旧worker消息彻底失效。
            if terminal.is_open:
                terminal.close_port()
            terminal.open_port()
            if not terminal.is_open:
                self.status.set(f"全部启动失败：{terminal.terminal_name} 未打开")
                return
        self.start_serial()
        if not self.stop_event.is_set():
            self.status.set("串口工作台已启动：请按实验顺序复位各板卡，等待完整平台签名")

    def stop_dual(self) -> None:
        self.stop()
        for terminal in self._live_aux_terminals():
            if terminal.is_open:
                terminal.close_port()
        self.status.set(
            f"双串口已停止，数据与时长已保留：{self._format_duration(self._capture_seconds())}"
        )

    def export_dual(self) -> None:
        from tkinter import filedialog, messagebox
        terminals = self._live_aux_terminals()
        if not terminals:
            messagebox.showerror("没有辅助日志", "请先打开辅助串口终端并完成一次双串口采集。")
            return
        directory = filedialog.askdirectory(title="选择本轮实验导出目录")
        if not directory:
            return
        target = Path(directory)
        analysis_port = re.sub(r"[^A-Za-z0-9_-]", "_", self.active_port or "unknown")
        analysis_stem = f"initiator_{analysis_port}"
        planned = [target / f"{analysis_stem}.{suffix}" for suffix in ("log", "csv", "json", "md")]
        planned.append(target / f"{analysis_stem}.raw")
        terminal_plans = []
        for index, terminal in enumerate(terminals, start=1):
            port = terminal.active_port or terminal.port_map.get(terminal.port.get(), "unknown")
            safe_port = re.sub(r"[^A-Za-z0-9_-]", "_", port or f"aux{index}")
            log_path = target / f"aux_{safe_port}.log"
            terminal_plans.append((terminal, port, log_path))
            planned.extend((log_path, log_path.with_suffix(".raw")))
        planned.append(target / "run.json")
        existing = [path for path in planned if path.exists()]
        if existing:
            messagebox.showerror("拒绝覆盖", "目标目录已有本轮文件：\n" + "\n".join(map(str, existing)))
            return
        try:
            paths = list(self.session.export(target, analysis_stem))
        except FileExistsError as exc:
            messagebox.showerror("拒绝覆盖", str(exc))
            return
        # 行数与样本数的一致性只能在文件落盘后核对（导出函数本身按内存写入）。
        csv_rows = count_csv_rows(target / f"{analysis_stem}.csv")
        # 分析端的原始字节也必须进本轮证据目录：渲染后的 .log 会改写行边界，
        # 逐字节复核只能靠 .raw；来源是采集期间持续 flush 的恢复日志。
        raw_source = getattr(self, "main_recovery_file", None)
        raw_bytes = b""
        if raw_source is not None and Path(raw_source).exists():
            raw_bytes = Path(raw_source).read_bytes()
        raw_path = target / f"{analysis_stem}.raw"
        with raw_path.open("xb") as handle:
            handle.write(raw_bytes)
        paths.append(raw_path)
        auxiliary = []
        auxiliary_paths = []
        for terminal, port, log_path in terminal_plans:
            terminal.write_log(log_path)
            auxiliary_paths.extend((log_path, log_path.with_suffix(".raw")))
            auxiliary.append({
                "name": terminal.terminal_name, "port": port,
                "baudrate": int(terminal.baudrate.get()), "byte_size": int(terminal.byte_size.get()),
                "stop_bits": float(terminal.stop_bits.get()), "parity": terminal.parity.get(),
                "flow_control": terminal.flow_control.get(),
                "dtr": terminal.dtr.get(), "rts": terminal.rts.get(),
                "rx_bytes": terminal.rx_bytes, "rx_chunks": terminal.rx_chunks,
                "log": log_path.name,
            })
        run_path = target / "run.json"
        spec = self.experiment_specs.get(self.expected_profile_name.get())
        run_payload = {
            "app_version": APP_VERSION,
            "source_identity": repository_identity(),
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "analysis_port": self.active_port,
            "auxiliary_serials": auxiliary,
            "platform_signature": asdict(self.session.signature),
            "experiment_spec": ({"schema_version": 1, "name": spec.name, "mode": spec.mode,
                                 "expect": spec.expect} if spec is not None else None),
            "firmware_identity": self._firmware_identity(),
            "capture_seconds": self._capture_seconds(),
            "analysis_samples": len(self.session.samples),
            "monitor_reports": len(self.session.adv_records),
            "auxiliary_rx_bytes": sum(item["rx_bytes"] for item in auxiliary),
            "auxiliary_rx_chunks": sum(item["rx_chunks"] for item in auxiliary),
            "pre_ready_samples": self.pre_ready_samples,
            "sum_frames_seen": self.session.sum_frames_seen,
            "samples_since_latest_sum": self.session.metrics()["samples_since_latest_sum"],
            "parse_failures": self.session.parse_failures,
            "pc_sequence": self.session.sample_sequence_metrics(),
            "board_restarts": self.session.board_restarts,
            "analysis_raw_bytes": len(raw_bytes),
            "capabilities": self.session.capabilities(),
        }
        checks = evaluate_evidence(run_payload, csv_rows)
        run_payload["evidence_checks"] = checks
        self.wizard.see_export(checks.get("status", "warn"))
        self._refresh_wizard()
        with run_path.open("x", encoding="utf-8") as handle:
            json.dump(run_payload, handle, ensure_ascii=False, indent=2)
        paths.extend((*auxiliary_paths, run_path))
        self._consume_firmware()
        messagebox.showinfo("联合导出完成", "\n".join(str(path) for path in paths)
                            + "\n\n" + format_evidence(checks))

    def refresh_ports(self, preferred: str | None = None) -> None:
        current_display = self.port.get()
        current_device = self.port_map.get(current_display, preferred or "")
        ports = discover_serial_ports()
        self.port_map = {display: device for device, display in ports}
        displays = list(self.port_map)
        self.port_combo["values"] = displays
        if not displays:
            self.port.set("未检测到串口")
            self.status.set("未检测到串口：请连接开发板后点击刷新")
            return
        selected = next((display for display, device in self.port_map.items() if device == current_device), displays[0])
        self.port.set(selected)
        self.status.set(f"已识别 {len(displays)} 个串口")

    def _new_session(self, source: str) -> None:
        self.session = Session()
        self.parser = LineParser(source=source)
        self.source_label = source
        self.session_started_at = datetime.now()
        self.pre_ready_samples = 0
        self.capture_started_monotonic = None
        self.capture_frozen_seconds = 0.0
        self.paused = False
        self.last_draw_n = -1
        self.last_draw_monitor_n = -1
        self.last_draw_at = 0.0

    def _begin_source(self, source: str) -> tuple[int, threading.Event]:
        self.stop()
        self.source_id += 1
        self.stop_event = threading.Event()
        self._new_session(source)
        # 新会话必须立刻反映到UI。否则旧曲线/指标会一直保留到首个新样本到达，
        # 当boot门尚未放行时看起来就像“全部开始没有清空、程序也没有重启采集”。
        self.draw(force=True)
        return self.source_id, self.stop_event

    def clear(self) -> None:
        self.stop()
        self.source_id += 1  # 让旧线程队列消息失效
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                break
        self._new_session("cleared")
        self.active_port = ""
        self.status.set("已清空")
        self.draw(force=True)

    def stop(self) -> None:
        self._freeze_capture()
        self.stop_event.set()
        # 与辅助串口完全相同：停止时同步关闭句柄，不等待readline超时后异步释放。
        with self.main_serial_lock:
            handle = self.main_serial_handle
            self.main_serial_handle = None
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        worker, self.main_worker = getattr(self, "main_worker", None), None
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=0.5)
        if getattr(self, "main_raw_journal", None) is not None:
            self.main_raw_journal.close()
            self.main_raw_journal = None
        # 立即使旧 worker 已排队但尚未处理的尾部消息失效；否则尾部 S 行会再次启动计时并清零冻结值。
        self.source_id += 1
        self.paused = bool(self.session.raw_lines or self.session.samples or self.session.adv_records)
        self.status.set(
            f"已停止，数据与时长已保留：{self._format_duration(self._capture_seconds())}；"
            "重新开始会创建新会话并清空当前数据，请先导出"
        )

    def _freeze_capture(self) -> None:
        if self.capture_started_monotonic is not None:
            self.capture_frozen_seconds = time.monotonic() - self.capture_started_monotonic
            self.capture_started_monotonic = None

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total = max(0, int(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def _capture_seconds(self) -> float:
        if not self.strict_live_gate:
            return (self.session.monitor_span_seconds() if self.session.adv_records
                    else self.session.sample_span_seconds())
        if self.capture_started_monotonic is not None:
            return time.monotonic() - self.capture_started_monotonic
        return self.capture_frozen_seconds

    def _resume_capture_clock(self) -> None:
        if self.capture_started_monotonic is None and self.capture_frozen_seconds > 0:
            self.capture_started_monotonic = time.monotonic() - self.capture_frozen_seconds

    def _apply_live_gate(self, event) -> bool:
        """Drop data emitted by a board that predates the current verified boot."""
        spec = getattr(self, "experiment_specs", {}).get(self.expected_profile_name.get())
        if spec is not None and spec.mode == "raw" and (event.sample or event.adv):
            event.sample = None
            event.adv = None
            return False
        if (self.strict_live_gate and (event.sample or event.adv)
                and (not self.parser.signature.measurement_ready or self._signature_mismatches())):
            self.pre_ready_samples += 1
            event.sample = None
            event.adv = None
            # Do not let ignored traffic make the raw-line count/export look like capture started.
            event.raw_line = ""
            return False
        return True

    def _signature_mismatches(self) -> list[str]:
        spec = getattr(self, "experiment_specs", {}).get(self.expected_profile_name.get())
        expected = (dict(spec.expect) if spec is not None and spec.mode == "measure"
                    else M5_PROFILES.get(self.expected_profile_name.get()))
        if not expected:
            return []
        signature = self.parser.signature
        mismatches = []
        for field, wanted in expected.items():
            if not hasattr(signature, field):
                mismatches.append(f"{field}=<规格里的未知字段>（期望{wanted!s}）")
                continue
            actual = getattr(signature, field)
            if actual != wanted:
                mismatches.append(f"{field}={actual!s}（期望{wanted!s}）")
        return mismatches

    def import_experiment_specs(self) -> None:
        from tkinter import filedialog, messagebox
        path = filedialog.askopenfilename(
            title="导入实验规格JSON", filetypes=(("JSON", "*.json"), ("All", "*.*")),
        )
        if not path:
            return
        try:
            imported = load_specs(path)
            self.experiment_specs.update(imported)
            self.expected_profile_combo.configure(values=list(self.experiment_specs))
            self.expected_profile_name.set(next(iter(imported)))
            self.expected_status.set(f"已导入 {len(imported)} 个规格：{Path(path).name}")
        except Exception as exc:
            messagebox.showerror("导入实验规格失败", str(exc))

    def _firmware_identity(self) -> dict:
        """登记路径 + 哈希；被上一次导出用过的路径标 inherited，供自检提示"没重新确认"。

        `provided` 只说明"本进程某轮登记过"，不等于"这一轮用的就是它"：同一份登记连
        续导出两轮，第二轮并不能证明板子上还是那个包。判断依据是"这次选择是否已经
        被某次导出消费过"，而不是"选择时间是否早于会话开始"——先选固件再开始采集是
        正常操作顺序，不该因此报警。
        """
        identity = {}
        for role, variable in (("initiator", getattr(self, "initiator_firmware", None)),
                               ("responder", getattr(self, "responder_firmware", None))):
            path = variable.get() if variable is not None and hasattr(variable, "get") else ""
            identity[role] = self.firmware_registry.identity(role, path)
        return identity

    def _consume_firmware(self) -> None:
        for role, variable in (("initiator", getattr(self, "initiator_firmware", None)),
                               ("responder", getattr(self, "responder_firmware", None))):
            path = variable.get() if variable is not None and hasattr(variable, "get") else ""
            self.firmware_registry.consume(role, path)

    def select_firmware(self, variable) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="选择本轮实际烧录的固件包",
            filetypes=(("固件包", "*.fwpkg *.bin *.hex"), ("所有文件", "*.*")),
        )
        if path:
            variable.set(path)
            role = ("initiator" if variable is getattr(self, "initiator_firmware", None)
                    else "responder")
            stamps = getattr(self, "firmware_registry", None)
            if stamps is not None:
                stamps.select(role)
            self.status.set(f"已登记固件：{Path(path).name}；联合导出将计算 SHA-256")

    def open_log(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="选择 LinkScope 日志或 CSV",
            filetypes=(("LinkScope data", "*.txt *.log *.csv"), ("Text logs", "*.txt *.log"),
                       ("CSV samples", "*.csv"), ("All", "*.*")),
        )
        if not path:
            return
        source_id, stop_event = self._begin_source(path)
        self.active_port = ""
        self.strict_live_gate = False
        self.status.set(f"正在回放：{Path(path).name}")
        if Path(path).suffix.lower() == ".csv":
            threading.Thread(target=self._csv_worker, args=(Path(path), source_id, stop_event), daemon=True).start()
        else:
            threading.Thread(target=self._file_worker, args=(Path(path), source_id, stop_event), daemon=True).start()

    def _csv_worker(self, path: Path, source_id: int, stop_event: threading.Event) -> None:
        try:
            loaded = load_log(path)
            if stop_event.is_set():
                return
            # CSV 已是准入后的正式样本，直接交给 UI；同名 JSON 提供签名与 SUM。
            self.events.put((source_id, ("csv_session", loaded)))
        except Exception as exc:
            self.events.put((source_id, exc))
        finally:
            self.events.put((source_id, None))

    def _file_worker(self, path: Path, source_id: int, stop_event: threading.Event) -> None:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if stop_event.is_set():
                        break
                    self.events.put((source_id, line))
        except Exception as exc:
            self.events.put((source_id, exc))
        finally:
            self.events.put((source_id, None))

    def _preflight_checks(self, port: str) -> list[dict]:
        spec = self.experiment_specs.get(self.expected_profile_name.get())
        expect = None
        if spec is not None:
            expect = expected_mapping({spec.name: spec}).get(spec.name)
        live_aux = [terminal.active_port for terminal in self._live_aux_terminals()
                    if terminal.is_open and getattr(terminal, "active_port", "")]
        firmware = bool((self.initiator_firmware.get() or "").strip()
                        and (self.responder_firmware.get() or "").strip())
        writable = False
        try:
            probe = Path("out")
            probe.mkdir(parents=True, exist_ok=True)
            handle = probe / ".preflight.tmp"
            handle.write_text("x", encoding="utf-8")
            handle.unlink()
            writable = True
        except OSError:
            writable = False
        return preflight_items(self.session.signature, expect, port,
                               live_aux[0] if live_aux else "", firmware, writable)

    def show_preflight(self) -> None:
        from tkinter import messagebox
        port = self.port_map.get(self.port.get().strip(), "")
        checks = self._preflight_checks(port)
        lines = [f"{'✓' if item['status'] == 'pass' else ('!' if item['status'] == 'warn' else '×')} "
                 f"{item['name']}：{item['detail']}" for item in checks]
        messagebox.showinfo("采样前自检", format_preflight(checks) + "\n\n" + "\n".join(lines))

    def open_wizard(self) -> None:
        if self.wizard_window is not None and self.wizard_window.winfo_exists():
            self.wizard_window.lift()
            self._refresh_wizard()
            return
        window = self.tk.Toplevel(self.root)
        window.title(f"实验向导 · {APP_VERSION}")
        window.geometry("620x420")
        text = self.tk.Text(window, wrap="word", padx=12, pady=10)
        text.pack(fill="both", expand=True)
        text.configure(state="disabled")
        self.wizard_window = window
        self.wizard_text = text
        self._refresh_wizard()

    def _refresh_wizard(self) -> None:
        if self.wizard_text is None or self.wizard_window is None:
            return
        if not self.wizard_window.winfo_exists():
            self.wizard_window = None
            self.wizard_text = None
            return
        session = self.session
        sequence = session.sample_sequence_metrics() or {}
        self.wizard.see_samples(len(session.samples))
        self.wizard.see_health(gaps=int(sequence.get("gaps") or 0),
                               restarts=session.board_restarts,
                               parse_failures=sum(session.parse_failures.values()))
        if session.signature.role not in ("", "unknown"):
            self.wizard.see_signature(session.signature.role,
                                      session.signature.measurement_ready)
        self.wizard.tick(self._capture_seconds())
        self.wizard_text.configure(state="normal")
        self.wizard_text.delete("1.0", "end")
        self.wizard_text.insert("1.0", self.wizard.format_text())
        self.wizard_text.configure(state="disabled")

    def start_serial(self) -> None:
        try:
            import serial  # type: ignore
        except ImportError:
            from tkinter import messagebox
            messagebox.showerror("缺少依赖", "实时串口需要 pyserial：\npython -m pip install -r tools/requirements.txt\n历史日志回放不受影响。")
            return
        display = self.port.get().strip()
        port = self.port_map.get(display, "")
        if not port:
            from tkinter import messagebox
            messagebox.showerror("没有可用串口", "请连接开发板并点击“刷新”。")
            return
        if any(terminal.is_open and terminal.active_port == port
               for terminal in self._live_aux_terminals()):
            from tkinter import messagebox
            messagebox.showerror("串口已占用", f"{port} 已被辅助串口终端打开。")
            return
        self.main_boot_gate.reset()
        if self.analysis_console is not None and self.analysis_console.window.winfo_exists():
            self.analysis_console.wait_for_boot(clear=True)
        # 每次开始都是独立实验会话：不恢复旧Session、旧时长或旧平台签名。
        source_id, stop_event = self._begin_source(port)
        self.strict_live_gate = True
        self.active_port = port
        self.wizard = ExperimentWizard(
            spec_name=self.expected_profile_name.get() or "",
            target_seconds=self.wizard.target_seconds)
        self.wizard.see_spec(self.expected_profile_name.get() or "")
        live_aux = [terminal.active_port for terminal in self._live_aux_terminals()
                    if terminal.is_open and getattr(terminal, "active_port", "")]
        self.wizard.see_ports(port, live_aux[0] if live_aux else "")
        self._refresh_wizard()
        self.main_recovery_file = recovery_path(port, "analysis")
        # 只读目录或无写权限时跳过 .raw，采集不能因为落盘失败而中断。
        self.main_raw_journal = RawByteSink(self.main_recovery_file) if self.main_recovery_file is not None else None
        try:
            self.status.set(format_preflight(self._preflight_checks(port))
                            + f" | 已打开 {port}，请先复位 COM4，再复位 COM3")
        except Exception:  # 自检不能挡住采集
            self.status.set(f"已打开 {port}，等待启动签名：请先复位 COM4，再复位 COM3")
        self.main_worker = threading.Thread(
            target=self._serial_worker, args=(serial, port, source_id, stop_event), daemon=True
        )
        self.main_worker.start()

    def _serial_worker(self, serial_module, port: str, source_id: int, stop_event: threading.Event) -> None:
        handle = None
        try:
            # 快速停止再启动时，旧句柄最多还需一个 timeout 才释放；有限重试避免假性占用失败。
            last_error = None
            for _ in range(10):
                if stop_event.is_set():
                    return
                try:
                    # 与 QCOM 的 115200/8N1/无流控、DTR/RTS 未勾选保持一致。
                    # CH340 的控制线可能接入下载/复位电路，不能依赖 pyserial 的默认有效电平。
                    handle = serial_module.Serial(
                        port=port,
                        baudrate=115200,
                        bytesize=serial_module.EIGHTBITS,
                        parity=serial_module.PARITY_NONE,
                        stopbits=serial_module.STOPBITS_ONE,
                        timeout=SERIAL_READ_TIMEOUT,
                        xonxoff=False,
                        rtscts=False,
                        dsrdtr=False,
                        exclusive=True,
                    )
                    handle.dtr = False
                    handle.rts = False
                    # 不接受端口关闭期间积压的历史字节；本轮必须在打开后重新复位板卡。
                    handle.reset_input_buffer()
                    break
                except Exception as exc:
                    last_error = exc
                    if handle is not None:
                        try:
                            handle.close()
                        except Exception:
                            pass
                        handle = None
                    time.sleep(.1)
            if handle is None:
                raise last_error or RuntimeError("无法打开串口")
            with handle:
                with self.main_serial_lock:
                    self.main_serial_handle = handle
                pump_serial_lines(
                    handle, stop_event,
                    lambda stamp, line: self._emit_main_serial(source_id, stamp, line),
                )
        except Exception as exc:  # 串口异常必须回到 UI 线程显示
            self.events.put((source_id, exc))
        finally:
            with self.main_serial_lock:
                if self.main_serial_handle is handle:
                    self.main_serial_handle = None
            self.events.put((source_id, None))

    def _emit_main_serial(self, source_id: int, stamp: datetime, line: bytes) -> None:
        journal = self.main_raw_journal
        if journal is not None:
            journal.append(line)
        self.events.put((source_id, ("serial_bytes", stamp, line)))

    def poll(self) -> None:
        drained = 0
        redraw_metadata = False
        while drained < 4000:
            try:
                source_id, item = self.events.get_nowait()
            except queue.Empty:
                break
            drained += 1
            if source_id != self.source_id:
                continue
            if item is None:
                self._freeze_capture()
                if self.paused:
                    self.status.set(
                        f"已停止，数据与时长已保留：{self._format_duration(self._capture_seconds())}；"
                        "重新开始会创建新会话并清空当前数据，请先导出"
                    )
                else:
                    self.status.set(
                        f"数据源结束，共 {len(self.session.samples) + len(self.session.adv_records)} 个样本"
                    )
            elif isinstance(item, Exception):
                self.status.set(f"数据源错误：{item}")
            elif isinstance(item, tuple) and item and item[0] == "csv_session":
                self.session = item[1]
                self.parser.signature = self.session.signature
                self.last_draw_n = -1
            else:
                received_at = datetime.now()
                line = item
                if isinstance(item, tuple) and item and item[0] == "serial_bytes":
                    received_at, raw = item[1], item[2]
                    line = raw.decode("utf-8", errors="replace")
                    # QCOM式原始镜像：先无条件显示，再由boot门决定是否进入解析与统计链。
                    if self.analysis_console is not None and self.analysis_console.window.winfo_exists():
                        self.analysis_console.feed(received_at, raw)
                    require_boot = getattr(getattr(self, "require_boot_gate", None), "get", lambda: True)()
                    decision = (self.main_boot_gate.admit(line) if require_boot
                                else BootGateDecision(accepted=True))
                    if not decision.accepted:
                        # 复位前数据不进入解析器、Session或样本导出，但始终保留在原始镜像窗口。
                        preview = line.strip().replace("\r", "")[-40:]
                        self.status.set(
                            f"已打开 {self.active_port}，等待boot. | "
                            f"已收完整行 {self.main_boot_gate.rejected_lines} | 末行 {preview!r}"
                        )
                        continue
                    if decision.activated:
                        self.wizard.see_boot()
                        if self.analysis_console is not None and self.analysis_console.window.winfo_exists():
                            self.analysis_console.begin_boot_session()
                event = self.parser.feed(line, received_at=received_at)
                self._apply_live_gate(event)
                redraw_metadata = bool(
                    redraw_metadata or event.signature_changed
                    or event.counters is not None or event.monitor_counters is not None
                )
                if ((event.sample or event.adv) and self.capture_started_monotonic is None and self.strict_live_gate
                        and not self.paused and not self.stop_event.is_set()):
                    self.capture_started_monotonic = time.monotonic()
                    self.capture_frozen_seconds = 0.0
                self.session.add(event, self.parser.signature)
                mismatches = self._signature_mismatches()
                if self.strict_live_gate and (not self.parser.signature.measurement_ready or mismatches):
                    detail = "；".join(mismatches[:3]) if mismatches else "等待ready"
                    self.expected_status.set("未准入：" + detail)
                    self.status.set(
                        f"等待本次启动就绪：请复位 COM4→COM3 | 原始行 {len(self.session.raw_lines)} | "
                        f"已忽略中途样本 {self.pre_ready_samples}"
                    )
                else:
                    self.expected_status.set("签名匹配，允许正式采集")
                    self.status.set(
                        f"正式采集中 {self.source_label} | 原始行 {len(self.session.raw_lines)} | "
                        f"有效样本 {len(self.session.samples) + len(self.session.adv_records)}"
                    )
        if (len(self.session.samples) != self.last_draw_n
                or len(self.session.adv_records) != self.last_draw_monitor_n
                or redraw_metadata):
            self.draw(force=redraw_metadata)
        self.metric_vars["采集时长"].set(self._format_duration(self._capture_seconds()))
        self.root.after(100, self.poll)

    def shutdown(self) -> None:
        """Deterministically release every COM handle before the Tk process exits."""
        self.stop()
        for terminal in list(self._live_aux_terminals()):
            terminal.close_port()
        with self.main_serial_lock:
            handle = self.main_serial_handle
            self.main_serial_handle = None
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        self.root.destroy()

    def export(self) -> None:
        from tkinter import filedialog, messagebox
        directory = filedialog.askdirectory(title="选择导出目录")
        if not directory:
            return
        stem = time.strftime("linkscope-%Y%m%d-%H%M%S")
        try:
            paths = self.session.export(Path(directory), stem)
        except FileExistsError as exc:
            messagebox.showerror("拒绝覆盖", str(exc))
            return
        checks = evaluate_evidence(session_evidence_record(self.session), count_csv_rows(paths[1]))
        self._consume_firmware()
        self.wizard.see_export(checks.get("status", "warn"))
        self._refresh_wizard()
        messagebox.showinfo("导出完成", "\n".join(str(p) for p in paths) + "\n\n" + format_evidence(checks))

    @staticmethod
    def _line_chart(canvas, values, title: str, color: str = "#168fa1") -> None:
        canvas.delete("all")
        width, height = max(10, canvas.winfo_width()), max(10, canvas.winfo_height())
        canvas.create_text(12, 10, anchor="nw", text=title, fill="#17324d", font=("Segoe UI", 11, "bold"))
        if not values:
            canvas.create_text(width / 2, height / 2, text="等待样本", fill="#7890a3")
            return
        left, top, right, bottom = 48, 38, width - 15, height - 28
        low, high = min(values), max(values)
        if high <= low:
            high = low + 1
        points = []
        for i, value in enumerate(values):
            x = left + (right - left) * i / max(1, len(values) - 1)
            y = bottom - (bottom - top) * (value - low) / (high - low)
            points.extend((x, y))
        canvas.create_line(left, bottom, right, bottom, fill="#bed0dd")
        canvas.create_line(left, top, left, bottom, fill="#bed0dd")
        if len(points) >= 4:
            canvas.create_line(*points, fill=color, width=2)
        canvas.create_text(8, top, anchor="nw", text=f"{high:.2f}", fill="#607d95")
        canvas.create_text(8, bottom, anchor="sw", text=f"{low:.2f}", fill="#607d95")

    def draw(self, force: bool = False) -> None:
        if (not force and len(self.session.samples) == self.last_draw_n
                and len(self.session.adv_records) == self.last_draw_monitor_n):
            return
        now = time.monotonic()
        if not force and now - self.last_draw_at < 0.5:
            return
        self.last_draw_at = now
        self.last_draw_n = len(self.session.samples)
        self.last_draw_monitor_n = len(self.session.adv_records)
        if self.session.adv_records or self.session.signature.role == "monitor":
            self._draw_monitor()
            return
        m = self.session.metrics()
        caps = self.session.capabilities()
        self.signature.set("平台签名：" + self.session.signature.compact() + " ｜ 能力：" + caps["label"])
        for key, label in zip(self.metric_labels, (
                "采集时长", "n", "p50精确", "p90精确", "p99精确", "p99.9精确", "max",
                "jitter", "timeout", "dup", "ooo", "bad", "drop")):
            self.metric_labels[key].set(label)
        self.metric_vars["采集时长"].set(self._format_duration(self._capture_seconds()))
        for key, metric in (("n", "n"), ("timeout", "to"), ("dup", "dup"), ("ooo", "ooo"), ("bad", "bad"), ("drop", "drop")):
            value = m[metric]
            self.metric_vars[key].set("—" if value is None else str(value))
        for key, metric in (("p50精确", "p50_us"), ("p90精确", "p90_us"), ("p99精确", "p99_us"), ("p99.9精确", "p999_us"), ("max", "max_us"), ("jitter", "jitter_us")):
            value = m[metric]
            self.metric_vars[key].set("—" if value is None else f"{float(value) / 1000:.3f} ms")
        recent = [row.rtt_us / 1000 for row in self.session.samples[-1200:]]
        self._line_chart(self.time_canvas, recent, "RTT 时间序列 · 最近 1200 点")
        hist = self.session.histogram(24)
        self._draw_hist(hist)
        self._draw_cdf()

    def _draw_monitor(self) -> None:
        metrics = self.session.monitor_metrics()
        devices = metrics["devices"]
        sequence = metrics["sequence"]
        all_rssi = [row.rssi_dbm for row in self.session.adv_records if row.rssi_dbm != 127]
        labels = ("采集时长", "ADV", "设备", "RSSI p10", "RSSI p50", "RSSI p90", "RSSI max",
                  "无效RSSI", "gap", "dup", "ooo", "reset", "queue drop")
        for key, label in zip(self.metric_labels, labels):
            self.metric_labels[key].set(label)
        primary = next(iter(devices.values()), {})
        values = (
            self._format_duration(self._capture_seconds()), str(metrics["n"]), str(len(devices)),
            self._dbm(primary.get("rssi_p10_dbm")), self._dbm(primary.get("rssi_p50_dbm")),
            self._dbm(primary.get("rssi_p90_dbm")), self._dbm(primary.get("rssi_max_dbm")),
            str(sum(device["invalid_rssi"] for device in devices.values())),
            str(sequence["gaps"]), str(sequence["duplicates"]), str(sequence["out_of_order"]),
            str(sequence["resets"]), str(metrics["board_latest_mon"].get("drop", 0)),
        )
        for key, value in zip(self.metric_vars, values):
            self.metric_vars[key].set(value)
        caps = self.session.capabilities()
        self.signature.set("平台签名：" + self.session.signature.compact() + " ｜ 能力：" + caps["label"])
        self._line_chart(self.time_canvas, all_rssi[-1200:], "RSSI 时间序列 · 最近 1200 点 (dBm)")
        self._draw_monitor_devices(devices)
        self._draw_monitor_intervals()

    @staticmethod
    def _dbm(value) -> str:
        return "—" if value is None else f"{value} dBm"

    def _draw_monitor_devices(self, devices) -> None:
        c = self.hist_canvas
        c.delete("all")
        width = max(10, c.winfo_width())
        c.create_text(12, 10, anchor="nw", text="监测设备", fill="#17324d", font=("Segoe UI", 11, "bold"))
        if not devices:
            c.create_text(width / 2, max(10, c.winfo_height()) / 2, text="等待ADV报告", fill="#7890a3")
            return
        c.create_text(18, 42, anchor="nw", text="地址ID", fill="#587184")
        c.create_text(170, 42, anchor="nw", text="报告数", fill="#587184")
        c.create_text(265, 42, anchor="nw", text="RSSI min / p50 / max", fill="#587184")
        c.create_text(475, 42, anchor="nw", text="events", fill="#587184")
        for index, (addr_id, device) in enumerate(list(devices.items())[:10]):
            y = 68 + index * 24
            events = "  ".join(f"{key}:{item['n']}" for key, item in device["events"].items())
            c.create_text(18, y, anchor="nw", text=addr_id, fill="#17324d", font=("Consolas", 10))
            c.create_text(170, y, anchor="nw", text=str(device["n"]), fill="#17324d")
            rssi = f"{device['rssi_min_dbm']} / {device['rssi_p50_dbm']} / {device['rssi_max_dbm']}"
            c.create_text(265, y, anchor="nw", text=rssi, fill="#17324d")
            c.create_text(475, y, anchor="nw", text=events, fill="#17324d")

    def _draw_monitor_intervals(self) -> None:
        c = self.cdf_canvas
        c.delete("all")
        width, height = max(10, c.winfo_width()), max(10, c.winfo_height())
        c.create_text(12, 10, anchor="nw", text="同设备、同event报告间隔 CDF",
                      fill="#17324d", font=("Segoe UI", 11, "bold"))
        groups = {}
        previous = {}
        for row in self.session.adv_records:
            key = (row.addr_id, row.event)
            prior = previous.get(key)
            if prior is not None and row.seq > prior.seq:
                groups.setdefault(key, []).append(((row.t_us - prior.t_us) & 0xFFFFFFFF) / 1000.0)
            previous[key] = row
        groups = {key: values for key, values in groups.items() if values}
        if not groups:
            c.create_text(width / 2, height / 2, text="等待间隔样本", fill="#7890a3")
            return
        left, top, right, bottom = 45, 42, width - 15, height - 30
        all_values = [value for values in groups.values() for value in values]
        low, high = min(all_values), max(all_values)
        if high <= low:
            high = low + 1
        colors = ("#20aa92", "#526de0", "#e08632", "#a657c7")
        for group_index, (key, values) in enumerate(sorted(groups.items())):
            ordered = sorted(values)
            if len(ordered) > 1000:
                step = len(ordered) / 1000
                ordered = [ordered[int(i * step)] for i in range(1000)]
            points = []
            for index, value in enumerate(ordered):
                points.extend((left + (right - left) * (value - low) / (high - low),
                               bottom - (bottom - top) * index / max(1, len(ordered) - 1)))
            color = colors[group_index % len(colors)]
            if len(points) >= 4:
                c.create_line(*points, fill=color, width=2)
            c.create_text(left + group_index * 150, 25, anchor="nw", text=f"{key[0]} e{key[1]}", fill=color)
        c.create_text(left, bottom + 4, anchor="nw", text=f"{low:.2f} ms", fill="#607d95")
        c.create_text(right, bottom + 4, anchor="ne", text=f"{high:.2f} ms", fill="#607d95")

    def _draw_hist(self, hist) -> None:
        c = self.hist_canvas
        c.delete("all")
        w, h = max(10, c.winfo_width()), max(10, c.winfo_height())
        c.create_text(12, 10, anchor="nw", text="RTT 直方图", fill="#17324d", font=("Segoe UI", 11, "bold"))
        if not hist:
            c.create_text(w / 2, h / 2, text="等待样本", fill="#7890a3")
            return
        left, top, right, bottom = 45, 38, w - 15, h - 28
        maximum = max(row[2] for row in hist) or 1
        bar_w = (right - left) / len(hist)
        for i, (_a, _b, count) in enumerate(hist):
            x0 = left + i * bar_w
            y0 = bottom - (bottom - top) * count / maximum
            c.create_rectangle(x0 + 1, y0, x0 + bar_w - 1, bottom, fill="#526de0", outline="")
        c.create_text(left, bottom + 4, anchor="nw", text=f"{hist[0][0]:.2f} ms", fill="#607d95")
        c.create_text(right, bottom + 4, anchor="ne", text=f"{hist[-1][1]:.2f} ms", fill="#607d95")

    def _draw_cdf(self) -> None:
        values = sorted(row.rtt_us / 1000 for row in self.session.samples)
        if len(values) > 2000:
            step = len(values) / 2000
            values = [values[int(i * step)] for i in range(2000)]
        c = self.cdf_canvas
        c.delete("all")
        w, h = max(10, c.winfo_width()), max(10, c.winfo_height())
        c.create_text(12, 10, anchor="nw", text="RTT CDF", fill="#17324d", font=("Segoe UI", 11, "bold"))
        if not values:
            c.create_text(w / 2, h / 2, text="等待样本", fill="#7890a3")
            return
        left, top, right, bottom = 45, 38, w - 15, h - 28
        low, high = values[0], values[-1]
        if high <= low:
            high = low + 1
        points = []
        for i, value in enumerate(values):
            points.extend((left + (right - left) * (value - low) / (high - low), bottom - (bottom - top) * i / max(1, len(values) - 1)))
        c.create_line(*points, fill="#20aa92", width=2)
        c.create_text(left, bottom + 4, anchor="nw", text=f"{low:.2f} ms", fill="#607d95")
        c.create_text(right, bottom + 4, anchor="ne", text=f"{values[-1]:.2f} ms", fill="#607d95")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", type=Path, help="无界面解析历史 QCOM 日志")
    parser.add_argument("--serial", metavar="COM", help="无界面采集串口，例如 COM3")
    parser.add_argument("--duration", type=float, default=10.0, help="--serial 采集秒数，默认10秒")
    parser.add_argument("--no-boot-gate", action="store_true",
                        help="串口采集不等待本次复位的 boot. 边界（仅接入无该标记的第三方固件时使用）")
    parser.add_argument("--export", type=Path, help="与 --replay/--serial 配合导出 CSV/JSON/Markdown")
    parser.add_argument("--reproduce", type=Path, metavar="RUN_DIR",
                        help="只读复现某个证据目录：重放 initiator 日志并与 run.json 逐项对照")
    parser.add_argument("--reproduce-out", type=Path,
                        help="复现报告输出目录，默认 out/reproduce")
    parser.add_argument("--import-csv", type=Path, metavar="CSV",
                        help="按适配器把第三方 CSV 映射成规范化样本（只读原文件）")
    parser.add_argument("--adapter", type=Path, metavar="JSON",
                        help="配 --import-csv 使用的适配器描述文件")
    args = parser.parse_args()
    if args.import_csv:
        if not args.adapter:
            parser.error("--import-csv 需要同时给 --adapter")
        try:
            spec = AdapterSpec.from_json(args.adapter)
            session, report = import_csv(args.import_csv, spec)
        except (OSError, ValueError, KeyError) as exc:
            print(f"导入失败：{exc}", file=sys.stderr)
            return 6
        print(format_import_report(report))
        print_summary(session)
        if args.export:
            code = export_cli(session, args.export, args.import_csv.stem)
            if code:
                return code
        return 0 if session.samples else 2
    if args.reproduce:
        if not args.reproduce.is_dir():
            parser.error(f"--reproduce 需要已存在的证据目录：{args.reproduce}")
        return reproduce_run(args.reproduce, args.reproduce_out)
    if args.replay and args.serial:
        parser.error("--replay 与 --serial 不能同时使用")
    if args.replay:
        session = load_log(args.replay)
        print_summary(session)
        if args.export:
            code = export_cli(session, args.export, args.replay.stem)
            if code:
                return code
        return 0 if (session.samples or session.adv_records) else 2

    if args.serial:
        try:
            import serial  # type: ignore
        except ImportError:
            print("缺少 pyserial：python -m pip install -r tools/requirements.txt", file=sys.stderr)
            return 3
        line_parser = LineParser(source=args.serial)
        session = Session()
        pre_ready_samples = 0
        boot_gate = BootSessionGate()
        deadline = time.monotonic() + max(0.1, args.duration)
        try:
            with serial.Serial(
                port=args.serial,
                baudrate=115200,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=SERIAL_READ_TIMEOUT,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
                exclusive=True,
            ) as handle:
                handle.dtr = False
                handle.rts = False
                handle.reset_input_buffer()
                stop_event = threading.Event()

                def accept_line(received_at, raw):
                    nonlocal pre_ready_samples
                    text = raw.decode("utf-8", errors="replace")
                    if not args.no_boot_gate and not boot_gate.admit(text).accepted:
                        return
                    event = line_parser.feed(text, received_at=received_at)
                    if event.sample and not line_parser.signature.measurement_ready:
                        pre_ready_samples += 1
                        event.sample = None
                    session.add(event, line_parser.signature)

                pump_serial_lines(handle, stop_event, accept_line, deadline=deadline)
        except Exception as exc:
            print(f"串口采集失败：{exc}", file=sys.stderr)
            return 4
        print_summary(session)
        if pre_ready_samples:
            print(f"ignored_pre_ready_samples={pre_ready_samples}")
        if not args.no_boot_gate:
            print(f"boot_gate_active={boot_gate.active} rejected_lines={boot_gate.rejected_lines}")
        if args.export:
            stem = f"linkscope-{args.serial.lower()}-{time.strftime('%Y%m%d-%H%M%S')}"
            code = export_cli(session, args.export, stem)
            if code:
                return code
        return 0 if (session.samples or session.adv_records) else 2

    import tkinter as tk
    root = tk.Tk()
    ScopeApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
