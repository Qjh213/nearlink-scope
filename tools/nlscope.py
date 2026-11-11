#!/usr/bin/env python3
"""LinkScope 桌面端：串口实时分析、日志回放与报告导出。"""

from __future__ import annotations

import argparse
import json
import queue
import re
import sys
import threading
import time
from datetime import datetime
from dataclasses import asdict
from pathlib import Path

from nlscope_core import LineParser, Session, load_log

APP_VERSION = "M5.0.0-dev"

M5_PROFILES = {
    "不校验预期实验": None,
    "M5 cfg500 基线 40B/40Hz": {"role": "initiator", "link": "sle-write_cmd", "cfg": "500",
                                  "payload_b": 40, "rate_hz": 40, "window": 1, "timeout_ms": 50},
    "M5 cfg511 Payload 12B": {"role": "initiator", "link": "sle-write_cmd", "cfg": "511",
                                 "payload_b": 12, "rate_hz": 40, "window": 1, "timeout_ms": 50},
    "M5 cfg512 Payload 100B": {"role": "initiator", "link": "sle-write_cmd", "cfg": "512",
                                  "payload_b": 100, "rate_hz": 40, "window": 1, "timeout_ms": 50},
    "M5 cfg513 Payload 250B": {"role": "initiator", "link": "sle-write_cmd", "cfg": "513",
                                  "payload_b": 250, "rate_hz": 40, "window": 1, "timeout_ms": 50},
    "M5 cfg521 Rate 10Hz": {"role": "initiator", "link": "sle-write_cmd", "cfg": "521",
                               "payload_b": 40, "rate_hz": 10, "window": 1, "timeout_ms": 50},
    "M5 cfg522 Rate 25Hz": {"role": "initiator", "link": "sle-write_cmd", "cfg": "522",
                               "payload_b": 40, "rate_hz": 25, "window": 1, "timeout_ms": 50},
    "M5 cfg523 Rate 60Hz": {"role": "initiator", "link": "sle-write_cmd", "cfg": "523",
                               "payload_b": 40, "rate_hz": 60, "window": 1, "timeout_ms": 50},
    "M5 cfg531 中距": {"role": "initiator", "link": "sle-write_cmd", "cfg": "531",
                          "payload_b": 40, "rate_hz": 40, "window": 1, "timeout_ms": 50},
    "M5 cfg532 远距": {"role": "initiator", "link": "sle-write_cmd", "cfg": "532",
                          "payload_b": 40, "rate_hz": 40, "window": 1, "timeout_ms": 50},
    "M5 cfg541 干扰": {"role": "initiator", "link": "sle-write_cmd", "cfg": "541",
                          "payload_b": 40, "rate_hz": 40, "window": 1, "timeout_ms": 50},
}


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
    if session.adv_records:
        metrics = session.monitor_metrics()
        print(session.signature.compact())
        print(f"adv={metrics['n']} devices={len(metrics['devices'])} sequence={metrics['sequence']}")
        return
    metrics = session.metrics()
    print(session.signature.compact())
    print(
        f"n={metrics['n']} p50={metrics['p50_us']}us p90={metrics['p90_us']}us "
        f"p99={metrics['p99_us']}us p999={metrics['p999_us']}us max={metrics['max_us']}us "
        f"to={metrics['to']} dup={metrics['dup']} ooo={metrics['ooo']} bad={metrics['bad']} drop={metrics['drop']}"
    )


class ScopeApp:
    def __init__(self, root) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self.root.title(f"NearLink LinkScope · {APP_VERSION}")
        self.root.geometry("1280x820")
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
        self.last_draw_n = -1
        self.last_draw_monitor_n = -1
        self.aux_terminal = None
        self.analysis_console = None
        self.main_serial_handle = None
        self.main_serial_lock = threading.Lock()

        controls = ttk.Frame(root, padding=8)
        controls.pack(fill="x")
        ttk.Label(controls, text="串口").pack(side="left")
        self.port = tk.StringVar(value="")
        self.port_map: dict[str, str] = {}
        self.port_combo = ttk.Combobox(controls, textvariable=self.port, width=31, state="readonly")
        self.port_combo.pack(side="left", padx=(4, 4))
        ttk.Button(controls, text="刷新", command=self.refresh_ports, width=6).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="开始串口", command=self.start_serial).pack(side="left")
        ttk.Button(controls, text="打开日志", command=self.open_log).pack(side="left", padx=6)
        ttk.Button(controls, text="停止", command=self.stop).pack(side="left")
        ttk.Button(controls, text="清空", command=self.clear).pack(side="left", padx=6)
        ttk.Button(controls, text="导出", command=self.export).pack(side="left")
        ttk.Button(controls, text="辅助串口", command=self.open_aux_terminal).pack(side="left", padx=6)
        ttk.Button(controls, text="分析终端", command=self.open_analysis_console).pack(side="left")
        ttk.Button(controls, text="双串口开始", command=self.start_dual).pack(side="left")
        ttk.Button(controls, text="双串口停止", command=self.stop_dual).pack(side="left", padx=6)
        ttk.Button(controls, text="联合导出", command=self.export_dual).pack(side="left")
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
        self.expected_status = tk.StringVar(value="将校验role/link/cfg/payload/rate/window/timeout")
        ttk.Label(experiment, textvariable=self.expected_status).pack(side="left", padx=8)

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

    def open_aux_terminal(self):
        from serial_terminal import SerialTerminalWindow
        if self.aux_terminal is not None and self.aux_terminal.window.winfo_exists():
            self.aux_terminal.window.lift()
            self.aux_terminal.window.focus_force()
            return self.aux_terminal
        self.aux_terminal = SerialTerminalWindow(
            self.root, discover_serial_ports,
            reserved_ports=lambda: ({self.active_port} if self.active_port and not self.stop_event.is_set() else set()),
        )
        return self.aux_terminal

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
        return self.analysis_console

    def send_main_serial(self, payload: bytes) -> int:
        with self.main_serial_lock:
            handle = self.main_serial_handle
            if handle is None or not getattr(handle, "is_open", False):
                raise RuntimeError("主分析串口未打开")
            return int(handle.write(payload))

    def start_dual(self) -> None:
        """Open responder terminal first, then start the gated analysis serial."""
        terminal = self.open_aux_terminal()
        if not terminal.is_open:
            terminal.open_port()
        if not terminal.is_open:
            self.status.set("双串口启动失败：辅助串口未打开")
            return
        self.start_serial()
        if not self.stop_event.is_set():
            self.status.set("双串口已打开：请先复位COM4，再复位COM3，等待完整平台签名")

    def stop_dual(self) -> None:
        self.stop()
        if self.aux_terminal is not None and self.aux_terminal.is_open:
            self.aux_terminal.close_port()
        self.status.set(
            f"双串口已停止，数据与时长已保留：{self._format_duration(self._capture_seconds())}"
        )

    def export_dual(self) -> None:
        from tkinter import filedialog, messagebox
        if self.aux_terminal is None:
            messagebox.showerror("没有辅助日志", "请先打开辅助串口终端并完成一次双串口采集。")
            return
        directory = filedialog.askdirectory(title="选择本轮实验导出目录")
        if not directory:
            return
        target = Path(directory)
        paths = list(self.session.export(target, "initiator_COM3"))
        responder_path = target / "responder_COM4.log"
        self.aux_terminal.write_log(responder_path)
        settings = {
            "port": self.aux_terminal.active_port or self.aux_terminal.port_map.get(self.aux_terminal.port.get(), ""),
            "baudrate": int(self.aux_terminal.baudrate.get()),
            "byte_size": int(self.aux_terminal.byte_size.get()),
            "stop_bits": float(self.aux_terminal.stop_bits.get()),
            "parity": self.aux_terminal.parity.get(),
            "flow_control": self.aux_terminal.flow_control.get(),
            "dtr": self.aux_terminal.dtr.get(), "rts": self.aux_terminal.rts.get(),
        }
        run_path = target / "run.json"
        run_path.write_text(json.dumps({
            "app_version": APP_VERSION,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "analysis_port": self.active_port,
            "auxiliary_serial": settings,
            "platform_signature": asdict(self.session.signature),
            "capture_seconds": self._capture_seconds(),
            "analysis_samples": len(self.session.samples),
            "monitor_reports": len(self.session.adv_records),
            "auxiliary_rx_bytes": self.aux_terminal.rx_bytes,
            "auxiliary_rx_chunks": self.aux_terminal.rx_chunks,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        paths.extend((responder_path, run_path))
        messagebox.showinfo("联合导出完成", "\n".join(str(path) for path in paths))

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
        self.pre_ready_samples = 0
        self.capture_started_monotonic = None
        self.capture_frozen_seconds = 0.0
        self.paused = False
        self.last_draw_n = -1
        self.last_draw_monitor_n = -1

    def _begin_source(self, source: str) -> tuple[int, threading.Event]:
        self.stop()
        self.source_id += 1
        self.stop_event = threading.Event()
        self._new_session(source)
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
        # 立即使旧 worker 已排队但尚未处理的尾部消息失效；否则尾部 S 行会再次启动计时并清零冻结值。
        self.source_id += 1
        self.paused = bool(self.session.raw_lines or self.session.samples or self.session.adv_records)
        self.status.set(
            f"已暂停，数据与时长已保留：{self._format_duration(self._capture_seconds())}；"
            "点击开始串口可继续，点击清空才会归零"
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
        expected = M5_PROFILES.get(self.expected_profile_name.get())
        if not expected:
            return []
        signature = self.parser.signature
        mismatches = []
        for field, wanted in expected.items():
            actual = getattr(signature, field)
            if actual != wanted:
                mismatches.append(f"{field}={actual!s}（期望{wanted!s}）")
        # M5 fixed conditions that are learned later in the startup chain.
        if signature.mtu is not None and signature.mtu != 520:
            mismatches.append(f"mtu={signature.mtu}（期望520）")
        if signature.interval is not None and signature.interval != 24:
            mismatches.append(f"intv={signature.interval}（期望24）")
        return mismatches

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
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if stop_event.is_set():
                    break
                self.events.put((source_id, line))
        self.events.put((source_id, None))

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
        if self.aux_terminal is not None and self.aux_terminal.is_open and self.aux_terminal.active_port == port:
            from tkinter import messagebox
            messagebox.showerror("串口已占用", f"{port} 已被辅助串口终端打开。")
            return
        resume = (self.paused and self.active_port == port
                  and bool(self.session.raw_lines or self.session.samples or self.session.adv_records))
        if resume:
            self.source_id += 1
            self.stop_event = threading.Event()
            source_id, stop_event = self.source_id, self.stop_event
            self.strict_live_gate = True
            # Reopening a live source requires a fresh board boot handshake; otherwise an already-running
            # monitor would immediately continue feeding data before the user resets the board.
            self.parser.signature.measurement_ready = False
            self.paused = False
            self.status.set(
                f"已重新打开 {port}，历史数据与时长已保留；等待复位板卡和本次启动就绪"
            )
        else:
            source_id, stop_event = self._begin_source(port)
            self.strict_live_gate = True
            self.active_port = port
            self.status.set(f"已打开 {port}，等待启动签名：请先复位 COM4，再复位 COM3")
        threading.Thread(target=self._serial_worker, args=(serial, port, source_id, stop_event), daemon=True).start()

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
                        timeout=.2,
                        xonxoff=False,
                        rtscts=False,
                        dsrdtr=False,
                    )
                    handle.dtr = False
                    handle.rts = False
                    break
                except Exception as exc:
                    last_error = exc
                    time.sleep(.1)
            if handle is None:
                raise last_error or RuntimeError("无法打开串口")
            with handle:
                with self.main_serial_lock:
                    self.main_serial_handle = handle
                while not stop_event.is_set():
                    raw = handle.readline()
                    if raw:
                        self.events.put((source_id, ("serial_bytes", datetime.now(), raw)))
        except Exception as exc:  # 串口异常必须回到 UI 线程显示
            self.events.put((source_id, exc))
        finally:
            with self.main_serial_lock:
                if self.main_serial_handle is handle:
                    self.main_serial_handle = None
            self.events.put((source_id, None))

    def poll(self) -> None:
        drained = 0
        while drained < 4000:
            try:
                source_id, item = self.events.get_nowait()
            except queue.Empty:
                break
            if source_id != self.source_id:
                continue
            if item is None:
                self._freeze_capture()
                if self.paused:
                    self.status.set(
                        f"已暂停，数据与时长已保留：{self._format_duration(self._capture_seconds())}；"
                        "点击开始串口可继续，点击清空才会归零"
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
                    if self.analysis_console is not None and self.analysis_console.window.winfo_exists():
                        self.analysis_console.feed(received_at, raw)
                    line = raw.decode("utf-8", errors="replace")
                event = self.parser.feed(line, received_at=received_at)
                self._apply_live_gate(event)
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
            drained += 1
        if (len(self.session.samples) != self.last_draw_n
                or len(self.session.adv_records) != self.last_draw_monitor_n):
            self.draw()
        self.metric_vars["采集时长"].set(self._format_duration(self._capture_seconds()))
        self.root.after(100, self.poll)

    def export(self) -> None:
        from tkinter import filedialog, messagebox
        directory = filedialog.askdirectory(title="选择导出目录")
        if not directory:
            return
        stem = time.strftime("linkscope-%Y%m%d-%H%M%S")
        paths = self.session.export(Path(directory), stem)
        messagebox.showinfo("导出完成", "\n".join(str(p) for p in paths))

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
        self.last_draw_n = len(self.session.samples)
        self.last_draw_monitor_n = len(self.session.adv_records)
        if self.session.adv_records or self.session.signature.role == "monitor":
            self._draw_monitor()
            return
        m = self.session.metrics()
        self.signature.set("平台签名：" + self.session.signature.compact())
        for key, label in zip(self.metric_labels, (
                "采集时长", "n", "p50精确", "p90精确", "p99精确", "p99.9精确", "max",
                "jitter", "timeout", "dup", "ooo", "bad", "drop")):
            self.metric_labels[key].set(label)
        self.metric_vars["采集时长"].set(self._format_duration(self._capture_seconds()))
        for key, metric in (("n", "n"), ("timeout", "to"), ("dup", "dup"), ("ooo", "ooo"), ("bad", "bad"), ("drop", "drop")):
            self.metric_vars[key].set(str(m[metric]))
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
        self.signature.set("平台签名：" + self.session.signature.compact())
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
    parser.add_argument("--export", type=Path, help="与 --replay/--serial 配合导出 CSV/JSON/Markdown")
    args = parser.parse_args()
    if args.replay and args.serial:
        parser.error("--replay 与 --serial 不能同时使用")
    if args.replay:
        session = load_log(args.replay)
        print_summary(session)
        if args.export:
            print("\n".join(str(p) for p in session.export(args.export, args.replay.stem)))
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
        deadline = time.monotonic() + max(0.1, args.duration)
        try:
            with serial.Serial(
                port=args.serial,
                baudrate=115200,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=.2,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            ) as handle:
                handle.dtr = False
                handle.rts = False
                while time.monotonic() < deadline:
                    raw = handle.readline()
                    if raw:
                        event = line_parser.feed(raw.decode("utf-8", errors="replace"), received_at=datetime.now())
                        if event.sample and not line_parser.signature.measurement_ready:
                            pre_ready_samples += 1
                            event.sample = None
                        session.add(event, line_parser.signature)
        except Exception as exc:
            print(f"串口采集失败：{exc}", file=sys.stderr)
            return 4
        print_summary(session)
        if pre_ready_samples:
            print(f"ignored_pre_ready_samples={pre_ready_samples}")
        if args.export:
            stem = f"linkscope-{args.serial.lower()}-{time.strftime('%Y%m%d-%H%M%S')}"
            print("\n".join(str(p) for p in session.export(args.export, stem)))
        return 0 if (session.samples or session.adv_records) else 2

    import tkinter as tk
    root = tk.Tk()
    ScopeApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
