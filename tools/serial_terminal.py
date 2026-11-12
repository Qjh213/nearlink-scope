#!/usr/bin/env python3
"""LinkScope内置辅助串口终端。"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable


BAUDRATES = (4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)
BYTE_SIZES = (4, 5, 6, 7, 8)
STOP_BITS = (1, 1.5, 2)
PARITIES = ("None", "Odd", "Even", "Mark", "Space")
FLOW_CONTROLS = ("No Ctrl Flow", "SW Ctrl Flow", "HW Ctrl Flow")


@dataclass(frozen=True)
class SerialSettings:
    port: str
    baudrate: int = 115200
    byte_size: int = 8
    stop_bits: float = 1
    parity: str = "None"
    flow_control: str = "No Ctrl Flow"
    dtr: bool = False
    rts: bool = False

    def validate(self) -> None:
        if not self.port:
            raise ValueError("未选择串口")
        if self.baudrate not in BAUDRATES:
            raise ValueError(f"不支持的波特率：{self.baudrate}")
        if self.byte_size not in BYTE_SIZES:
            raise ValueError(f"不支持的数据位：{self.byte_size}")
        if self.stop_bits not in STOP_BITS:
            raise ValueError(f"不支持的停止位：{self.stop_bits}")
        if self.parity not in PARITIES:
            raise ValueError(f"不支持的校验方式：{self.parity}")
        if self.flow_control not in FLOW_CONTROLS:
            raise ValueError(f"不支持的流控方式：{self.flow_control}")


def encode_outbound(text: str, hex_input: bool, append_newline: bool) -> bytes:
    if hex_input:
        compact = "".join(text.split())
        if len(compact) % 2:
            raise ValueError("HEX输入必须包含偶数个十六进制字符")
        try:
            payload = bytes.fromhex(compact)
        except ValueError as exc:
            raise ValueError("HEX输入包含非十六进制字符") from exc
    else:
        payload = text.encode("utf-8")
    if append_newline:
        payload += b"\r\n"
    return payload


def format_received(data: bytes, hex_display: bool) -> str:
    if hex_display:
        return " ".join(f"{value:02X}" for value in data)
    return data.decode("utf-8", errors="replace")


class SerialTerminalWindow:
    """独立Toplevel窗口；串口worker只向队列写，Tk更新全部留在UI线程。"""

    def __init__(self, root, discover_ports: Callable[[], list[tuple[str, str]]],
                 reserved_ports: Callable[[], set[str]] | None = None,
                 terminal_name: str = "辅助串口") -> None:
        import tkinter as tk
        from tkinter import scrolledtext, ttk

        self.tk, self.ttk = tk, ttk
        self.discover_ports = discover_ports
        self.reserved_ports = reserved_ports or (lambda: set())
        self.terminal_name = terminal_name
        self.window = tk.Toplevel(root)
        self.window.title(f"LinkScope · {terminal_name}")
        self.window.geometry("1040x700")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.handle = None
        self.worker = None
        self.port_map: dict[str, str] = {}
        self.rx_bytes = 0
        self.rx_chunks = 0
        self.log_lines: list[str] = []

        settings = ttk.LabelFrame(self.window, text="COM Port Setting", padding=8)
        settings.pack(fill="x", padx=8, pady=(8, 4))
        self.port = tk.StringVar()
        self.baudrate = tk.StringVar(value="115200")
        self.stop_bits = tk.StringVar(value="1")
        self.parity = tk.StringVar(value="None")
        self.byte_size = tk.StringVar(value="8")
        self.flow_control = tk.StringVar(value="No Ctrl Flow")
        fields = (
            ("COM Port", self.port, (), 30), ("Baudrate", self.baudrate, BAUDRATES, 10),
            ("StopBits", self.stop_bits, STOP_BITS, 7), ("Parity", self.parity, PARITIES, 9),
            ("ByteSize", self.byte_size, BYTE_SIZES, 7),
            ("Flow Control", self.flow_control, FLOW_CONTROLS, 14),
        )
        self.combos = {}
        for index, (label, variable, values, width) in enumerate(fields):
            row, column = divmod(index, 4)
            ttk.Label(settings, text=label).grid(row=row, column=column * 2, sticky="e", padx=(5, 2), pady=3)
            combo = ttk.Combobox(settings, textvariable=variable, values=values, width=width, state="readonly")
            combo.grid(row=row, column=column * 2 + 1, sticky="w", padx=(0, 8), pady=3)
            self.combos[label] = combo
        ttk.Button(settings, text="刷新", command=self.refresh_ports).grid(row=1, column=6, padx=4)
        self.open_button = ttk.Button(settings, text="Open Port", command=self.toggle_port)
        self.open_button.grid(row=1, column=7, padx=4)

        self.console = scrolledtext.ScrolledText(self.window, wrap="none", font=("Consolas", 10), state="disabled")
        self.console.pack(fill="both", expand=True, padx=8, pady=4)

        options = ttk.Frame(self.window, padding=(8, 4))
        options.pack(fill="x")
        self.dtr = tk.BooleanVar(value=False)
        self.rts = tk.BooleanVar(value=False)
        self.show_time = tk.BooleanVar(value=True)
        self.hex_display = tk.BooleanVar(value=False)
        self.hex_input = tk.BooleanVar(value=False)
        self.send_enter = tk.BooleanVar(value=False)
        ttk.Checkbutton(options, text="DTR", variable=self.dtr, command=self._apply_control_lines).pack(side="left")
        ttk.Checkbutton(options, text="RTS", variable=self.rts, command=self._apply_control_lines).pack(side="left", padx=6)
        ttk.Checkbutton(options, text="Show Time", variable=self.show_time).pack(side="left", padx=6)
        ttk.Checkbutton(options, text="Show In HEX", variable=self.hex_display).pack(side="left", padx=6)
        ttk.Checkbutton(options, text="HEX String", variable=self.hex_input).pack(side="left", padx=6)
        ttk.Checkbutton(options, text="Send With Enter", variable=self.send_enter).pack(side="left", padx=6)
        ttk.Button(options, text="清空", command=self.clear).pack(side="right")
        ttk.Button(options, text="保存日志", command=self.save_log).pack(side="right", padx=6)

        send = ttk.Frame(self.window, padding=(8, 4))
        send.pack(fill="x")
        ttk.Label(send, text="Input String").pack(side="left")
        self.input_text = tk.StringVar()
        entry = ttk.Entry(send, textvariable=self.input_text)
        entry.pack(side="left", fill="x", expand=True, padx=6)
        entry.bind("<Return>", lambda _event: self.send())
        ttk.Button(send, text="Send Command", command=self.send).pack(side="right")

        self.status = tk.StringVar(value="未打开串口")
        ttk.Label(self.window, textvariable=self.status, padding=(10, 5)).pack(fill="x")
        self.refresh_ports(preferred="COM4")
        self.window.after(80, self.poll)

    @property
    def is_open(self) -> bool:
        return self.handle is not None and getattr(self.handle, "is_open", False)

    @property
    def active_port(self) -> str:
        return getattr(self.handle, "port", "") if self.is_open else ""

    def refresh_ports(self, preferred: str | None = None) -> None:
        current = self.port_map.get(self.port.get(), preferred or "")
        ports = self.discover_ports()
        self.port_map = {display: device for device, display in ports}
        values = list(self.port_map)
        self.combos["COM Port"]["values"] = values
        if values:
            selected = next((display for display, device in self.port_map.items() if device == current), values[0])
            self.port.set(selected)
        else:
            self.port.set("未检测到串口")

    def _settings(self) -> SerialSettings:
        settings = SerialSettings(
            port=self.port_map.get(self.port.get(), ""), baudrate=int(self.baudrate.get()),
            byte_size=int(self.byte_size.get()), stop_bits=float(self.stop_bits.get()),
            parity=self.parity.get(), flow_control=self.flow_control.get(),
            dtr=self.dtr.get(), rts=self.rts.get(),
        )
        settings.validate()
        return settings

    def toggle_port(self) -> None:
        if self.is_open:
            self.close_port()
        else:
            self.open_port()

    def open_port(self) -> None:
        from tkinter import messagebox
        try:
            import serial  # type: ignore
            settings = self._settings()
            if settings.port in self.reserved_ports():
                raise ValueError(f"{settings.port} 已被LinkScope主分析串口占用")
            parity = {"None": serial.PARITY_NONE, "Odd": serial.PARITY_ODD,
                      "Even": serial.PARITY_EVEN, "Mark": serial.PARITY_MARK,
                      "Space": serial.PARITY_SPACE}[settings.parity]
            handle = serial.Serial(
                port=settings.port, baudrate=settings.baudrate, bytesize=settings.byte_size,
                stopbits=settings.stop_bits, parity=parity, timeout=.15,
                xonxoff=settings.flow_control == "SW Ctrl Flow",
                rtscts=settings.flow_control == "HW Ctrl Flow", dsrdtr=False,
            )
            handle.dtr, handle.rts = settings.dtr, settings.rts
            self.handle = handle
            self.stop_event = threading.Event()
            self.worker = threading.Thread(target=self._reader, args=(handle, self.stop_event), daemon=True)
            self.worker.start()
            self.open_button.configure(text="Close Port")
            self.window.title(f"LinkScope · {self.terminal_name} · {settings.port}")
            self.status.set(f"已打开 {settings.port} @ {settings.baudrate}")
        except Exception as exc:
            messagebox.showerror("打开串口失败", str(exc))

    def close_port(self) -> None:
        self.stop_event.set()
        handle, self.handle = self.handle, None
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        self.open_button.configure(text="Open Port")
        self.window.title(f"LinkScope · {self.terminal_name}")
        self.status.set(f"串口已关闭；保留 {self.rx_chunks} 个接收块、{self.rx_bytes} bytes")

    def _reader(self, handle, stop_event: threading.Event) -> None:
        try:
            while not stop_event.is_set():
                data = handle.readline()
                if data:
                    self.events.put(("data", datetime.now(), data))
        except Exception as exc:
            if not stop_event.is_set():
                self.events.put(("error", exc))
        finally:
            self.events.put(("closed", None))

    def poll(self) -> None:
        try:
            while True:
                kind, *payload = self.events.get_nowait()
                if kind == "data":
                    self._append_received(payload[0], payload[1])
                elif kind == "error":
                    self.status.set(f"串口错误：{payload[0]}")
                elif kind == "closed" and not self.is_open:
                    self.open_button.configure(text="Open Port")
        except queue.Empty:
            pass
        if self.window.winfo_exists():
            self.window.after(80, self.poll)

    def _append_received(self, stamp: datetime, data: bytes) -> None:
        self.rx_chunks += 1
        self.rx_bytes += len(data)
        rendered = format_received(data, self.hex_display.get())
        prefix = f"[{stamp:%Y-%m-%d_%H:%M:%S}:{stamp.microsecond // 1000:03d}]" if self.show_time.get() else ""
        if self.hex_display.get():
            rendered = prefix + rendered + "\n"
        else:
            rendered = "".join(prefix + part for part in rendered.splitlines(keepends=True))
            if rendered and not rendered.endswith(("\n", "\r")):
                rendered += "\n"
        self.log_lines.append(rendered)
        self.console.configure(state="normal")
        self.console.insert("end", rendered)
        self.console.see("end")
        self.console.configure(state="disabled")
        self.status.set(f"正在读取 {self.active_port} | blocks={self.rx_chunks} bytes={self.rx_bytes}")

    def _apply_control_lines(self) -> None:
        if self.is_open:
            try:
                self.handle.dtr, self.handle.rts = self.dtr.get(), self.rts.get()
            except Exception as exc:
                self.status.set(f"控制线设置失败：{exc}")

    def send(self) -> None:
        from tkinter import messagebox
        if not self.is_open:
            messagebox.showerror("串口未打开", "请先打开辅助串口。")
            return
        try:
            payload = encode_outbound(self.input_text.get(), self.hex_input.get(), self.send_enter.get())
            self.handle.write(payload)
            self.status.set(f"已发送 {len(payload)} bytes 到 {self.active_port}")
        except Exception as exc:
            messagebox.showerror("发送失败", str(exc))

    def clear(self) -> None:
        self.log_lines.clear()
        self.rx_bytes = self.rx_chunks = 0
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")
        self.status.set("显示和辅助日志缓存已清空")

    def save_log(self) -> None:
        from tkinter import filedialog, messagebox
        path = filedialog.asksaveasfilename(
            title="保存辅助串口日志", defaultextension=".txt",
            filetypes=(("Text log", "*.txt"), ("All", "*.*")),
        )
        if not path:
            return
        self.write_log(Path(path))
        messagebox.showinfo("保存完成", path)

    def write_log(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(self.log_lines), encoding="utf-8")

    def close(self) -> None:
        self.close_port()
        self.window.destroy()


class AnalysisSerialConsoleWindow:
    """Mirror the main analysis handle without opening the same COM port twice."""

    def __init__(self, root, send_callback: Callable[[bytes], int], port_callback: Callable[[], str]) -> None:
        import tkinter as tk
        from tkinter import scrolledtext, ttk

        self.tk = tk
        self.send_callback = send_callback
        self.port_callback = port_callback
        self.window = tk.Toplevel(root)
        self.window.title("LinkScope · 分析端原始终端")
        self.window.geometry("960x650")
        self.log_lines: list[str] = []
        self.rx_bytes = 0
        self.rx_chunks = 0

        header = ttk.Frame(self.window, padding=8)
        header.pack(fill="x")
        ttk.Label(header, text="共享主分析串口，不重复打开COM端口").pack(side="left")
        self.status = tk.StringVar(value="等待主分析串口")
        ttk.Label(header, textvariable=self.status).pack(side="right")

        self.console = scrolledtext.ScrolledText(
            self.window, wrap="none", font=("Consolas", 10), state="disabled"
        )
        self.console.pack(fill="both", expand=True, padx=8, pady=4)

        options = ttk.Frame(self.window, padding=(8, 4))
        options.pack(fill="x")
        self.show_time = tk.BooleanVar(value=True)
        self.hex_display = tk.BooleanVar(value=False)
        self.hex_input = tk.BooleanVar(value=False)
        self.send_enter = tk.BooleanVar(value=False)
        ttk.Checkbutton(options, text="Show Time", variable=self.show_time).pack(side="left")
        ttk.Checkbutton(options, text="Show In HEX", variable=self.hex_display).pack(side="left", padx=6)
        ttk.Checkbutton(options, text="HEX String", variable=self.hex_input).pack(side="left", padx=6)
        ttk.Checkbutton(options, text="Send With Enter", variable=self.send_enter).pack(side="left", padx=6)
        ttk.Button(options, text="清空", command=self.clear).pack(side="right")
        ttk.Button(options, text="保存日志", command=self.save_log).pack(side="right", padx=6)

        send = ttk.Frame(self.window, padding=(8, 4))
        send.pack(fill="x")
        ttk.Label(send, text="Input String").pack(side="left")
        self.input_text = tk.StringVar()
        entry = ttk.Entry(send, textvariable=self.input_text)
        entry.pack(side="left", fill="x", expand=True, padx=6)
        entry.bind("<Return>", lambda _event: self.send())
        ttk.Button(send, text="Send Command", command=self.send).pack(side="right")

    def load_history(self, lines: list[str]) -> None:
        """回填主分析器已经接收的当前会话，解决终端中途打开时历史为空。"""
        if not lines or not self.window.winfo_exists():
            return
        rendered = "".join(lines)
        if rendered and not rendered.endswith(("\n", "\r")):
            rendered += "\n"
        banner = f"--- 已回填当前COM3会话历史：{len(lines)}行 ---\n"
        self.log_lines.append(banner + rendered)
        self.rx_chunks += len(lines)
        self.rx_bytes += len(rendered.encode("utf-8", errors="replace"))
        self.console.configure(state="normal")
        self.console.insert("end", banner + rendered)
        self.console.see("end")
        self.console.configure(state="disabled")
        self.status.set(
            f"{self.port_callback() or 'COM3'} | 已回填{len(lines)}行，等待实时数据"
        )

    def feed(self, stamp: datetime, data: bytes) -> None:
        if not self.window.winfo_exists():
            return
        self.rx_chunks += 1
        self.rx_bytes += len(data)
        rendered = format_received(data, self.hex_display.get())
        prefix = f"[{stamp:%Y-%m-%d_%H:%M:%S}:{stamp.microsecond // 1000:03d}]" if self.show_time.get() else ""
        if self.hex_display.get():
            rendered = prefix + rendered + "\n"
        else:
            rendered = "".join(prefix + part for part in rendered.splitlines(keepends=True))
            if rendered and not rendered.endswith(("\n", "\r")):
                rendered += "\n"
        self.log_lines.append(rendered)
        self.console.configure(state="normal")
        self.console.insert("end", rendered)
        self.console.see("end")
        self.console.configure(state="disabled")
        self.status.set(f"{self.port_callback() or 'COM?'} | blocks={self.rx_chunks} bytes={self.rx_bytes}")

    def send(self) -> None:
        from tkinter import messagebox
        try:
            payload = encode_outbound(self.input_text.get(), self.hex_input.get(), self.send_enter.get())
            count = self.send_callback(payload)
            self.status.set(f"已发送 {count} bytes 到 {self.port_callback() or '主分析串口'}")
        except Exception as exc:
            messagebox.showerror("发送失败", str(exc))

    def clear(self) -> None:
        self.log_lines.clear()
        self.rx_bytes = self.rx_chunks = 0
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")
        self.status.set("显示和镜像日志缓存已清空")

    def save_log(self) -> None:
        from tkinter import filedialog, messagebox
        path = filedialog.asksaveasfilename(
            title="保存分析端镜像日志", defaultextension=".txt",
            filetypes=(("Text log", "*.txt"), ("All", "*.*")),
        )
        if not path:
            return
        Path(path).write_text("".join(self.log_lines), encoding="utf-8")
        messagebox.showinfo("保存完成", path)

