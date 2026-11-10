#!/usr/bin/env python3
"""LinkScope 桌面端：串口实时分析、日志回放与报告导出。"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
from pathlib import Path

from nlscope_core import LineParser, Session, load_log


def print_summary(session: Session) -> None:
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
        self.root.title("NearLink LinkScope · M3")
        self.root.geometry("1280x820")
        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.source_id = 0
        self.source_label = "idle"
        self.session = Session()
        self.parser = LineParser()
        self.last_draw_n = -1

        controls = ttk.Frame(root, padding=8)
        controls.pack(fill="x")
        ttk.Label(controls, text="串口").pack(side="left")
        self.port = tk.StringVar(value="COM3")
        ttk.Entry(controls, textvariable=self.port, width=10).pack(side="left", padx=(4, 8))
        ttk.Button(controls, text="开始串口", command=self.start_serial).pack(side="left")
        ttk.Button(controls, text="打开日志", command=self.open_log).pack(side="left", padx=6)
        ttk.Button(controls, text="停止", command=self.stop).pack(side="left")
        ttk.Button(controls, text="清空", command=self.clear).pack(side="left", padx=6)
        ttk.Button(controls, text="导出", command=self.export).pack(side="left")
        self.status = tk.StringVar(value="就绪：可打开 M1/M2 QCOM 日志回放")
        ttk.Label(controls, textvariable=self.status).pack(side="right")

        self.signature = tk.StringVar(value="平台签名：等待数据")
        ttk.Label(root, textvariable=self.signature, padding=(12, 8), background="#0d344f", foreground="white").pack(fill="x")

        metrics = ttk.Frame(root, padding=(8, 6))
        metrics.pack(fill="x")
        self.metric_vars = {}
        for name in ("n", "p50精确", "p90精确", "p99精确", "p99.9精确", "max", "jitter", "timeout", "dup", "ooo", "bad", "drop"):
            box = ttk.Frame(metrics, padding=5)
            box.pack(side="left", expand=True)
            ttk.Label(box, text=name, foreground="#587184").pack()
            var = tk.StringVar(value="—")
            ttk.Label(box, textvariable=var, font=("Segoe UI", 11, "bold")).pack()
            self.metric_vars[name] = var

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

    def _new_session(self, source: str) -> None:
        self.session = Session()
        self.parser = LineParser(source=source)
        self.source_label = source
        self.last_draw_n = -1

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
        self.status.set("已清空")
        self.draw(force=True)

    def stop(self) -> None:
        self.stop_event.set()
        self.status.set("已停止")

    def open_log(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(title="选择 QCOM 日志", filetypes=(("Text logs", "*.txt *.log"), ("All", "*.*")))
        if not path:
            return
        source_id, stop_event = self._begin_source(path)
        self.status.set(f"正在回放：{Path(path).name}")
        threading.Thread(target=self._file_worker, args=(Path(path), source_id, stop_event), daemon=True).start()

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
        port = self.port.get().strip()
        source_id, stop_event = self._begin_source(port)
        self.status.set(f"正在读取 {port} @ 115200")
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
                while not stop_event.is_set():
                    raw = handle.readline()
                    if raw:
                        self.events.put((source_id, raw.decode("utf-8", errors="replace")))
        except Exception as exc:  # 串口异常必须回到 UI 线程显示
            self.events.put((source_id, exc))
        finally:
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
                self.status.set(f"数据源结束，共 {len(self.session.samples)} 个样本")
            elif isinstance(item, Exception):
                self.status.set(f"数据源错误：{item}")
            else:
                self.session.add(self.parser.feed(item), self.parser.signature)
                self.status.set(
                    f"正在读取 {self.source_label} | 原始行 {len(self.session.raw_lines)} | "
                    f"有效样本 {len(self.session.samples)}"
                )
            drained += 1
        if len(self.session.samples) != self.last_draw_n:
            self.draw()
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
        if not force and len(self.session.samples) == self.last_draw_n:
            return
        self.last_draw_n = len(self.session.samples)
        m = self.session.metrics()
        self.signature.set("平台签名：" + self.session.signature.compact())
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
        return 0 if session.samples else 2

    if args.serial:
        try:
            import serial  # type: ignore
        except ImportError:
            print("缺少 pyserial：python -m pip install -r tools/requirements.txt", file=sys.stderr)
            return 3
        line_parser = LineParser(source=args.serial)
        session = Session()
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
                        session.add(line_parser.feed(raw.decode("utf-8", errors="replace")), line_parser.signature)
        except Exception as exc:
            print(f"串口采集失败：{exc}", file=sys.stderr)
            return 4
        print_summary(session)
        if args.export:
            stem = f"linkscope-{args.serial.lower()}-{time.strftime('%Y%m%d-%H%M%S')}"
            print("\n".join(str(p) for p in session.export(args.export, stem)))
        return 0 if session.samples else 2

    import tkinter as tk
    root = tk.Tk()
    ScopeApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
