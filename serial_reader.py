"""Raw serial reader: CR is the line boundary, no content filtering, DTR/RTS off."""

from __future__ import annotations

import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import serial


class LineSplitter:
    """Split byte streams on CR while preserving every non-delimiter byte."""

    def __init__(self, max_pending: int = 64 * 1024, idle_ms: int = 300, split_lf: bool = False) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be positive")
        if idle_ms < 0:
            raise ValueError("idle_ms must be non-negative")
        self.max_pending = max_pending
        self.idle_seconds = idle_ms / 1000.0
        self.split_lf = split_lf
        self._pending = bytearray()
        self._last_byte_at: float | None = None
        self.forced_flushes = 0
        self.dropped_bytes = 0

    @property
    def pending(self) -> bytes:
        return bytes(self._pending)

    @property
    def last_byte_at(self) -> float | None:
        return self._last_byte_at

    def feed(self, data: bytes) -> list[bytes]:
        if not data:
            return []
        self._last_byte_at = time.monotonic()
        self._pending.extend(data)
        lines: list[bytes] = []
        while True:
            cr = self._pending.find(b"\r")
            lf = self._pending.find(b"\n") if self.split_lf else -1
            candidates = [index for index in (cr, lf) if index >= 0]
            boundary = min(candidates) if candidates else -1
            if boundary < 0:
                break
            lines.append(bytes(self._pending[: boundary + 1]))
            del self._pending[: boundary + 1]
        if len(self._pending) > self.max_pending:
            lines.append(bytes(self._pending))
            self._pending.clear()
            self.forced_flushes += 1
        return lines

    def flush_idle(self, now: float | None = None) -> bytes | None:
        if not self._pending or self._last_byte_at is None:
            return None
        current = time.monotonic() if now is None else now
        # 阈值比较留 1µs 容差：monotonic 的基数很大时，(last + 0.3) - last 会
        # 略小于 0.3，恰好落在 idle_ms 上的调用不应被判成“还没到”。
        if current - self._last_byte_at < self.idle_seconds - 1e-6:
            return None
        return self._flush_pending()

    def flush(self) -> bytes | None:
        if not self._pending:
            return None
        return self._flush_pending()

    def _flush_pending(self) -> bytes:
        data = bytes(self._pending)
        self._pending.clear()
        self.forced_flushes += 1
        return data


class TimestampFormatter:
    def stamp(self, now: datetime | None = None) -> str:
        value = datetime.now() if now is None else now
        return value.strftime("[%Y-%m-%d_%H:%M:%S:") + f"{value.microsecond // 1000:03d}]"


class LogSink:
    """Append displayed records, including timestamp and original CR terminator."""

    def __init__(self, path: str | Path, formatter: TimestampFormatter | None = None) -> None:
        self.path = Path(path)
        self.formatter = formatter or TimestampFormatter()
        self._lock = threading.Lock()
        self._handle = None

    def append(self, line: bytes, now: datetime | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = self.formatter.stamp(now).encode("ascii") + line + b"\r\n"
        with self._lock:
            if self._handle is None:
                self._handle = self.path.open("ab")
            self._handle.write(record)
            self._handle.flush()

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                self._handle.close()
                self._handle = None

    def __enter__(self) -> "LogSink":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


class SerialReader(threading.Thread):
    """Read raw blocks on one worker thread and publish framed bytes to a queue."""

    def __init__(
        self,
        port: str,
        out: queue.Queue[bytes | None],
        stop: threading.Event,
        *,
        baudrate: int = 115200,
        idle_ms: int = 300,
        max_pending: int = 64 * 1024,
        errors: queue.Queue[BaseException] | None = None,
        serial_factory: Callable[..., object] | None = None,
    ) -> None:
        super().__init__(daemon=True, name=f"SerialReader-{port}")
        self.port = port
        self.out = out
        self.stop_event = stop
        self.baudrate = baudrate
        self.errors = errors
        self.splitter = LineSplitter(max_pending=max_pending, idle_ms=idle_ms)
        self.serial_factory = serial_factory or serial.Serial
        self.handle = None

    def run(self) -> None:
        try:
            self.handle = self.serial_factory(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.05,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
                exclusive=True,
            )
            self.handle.dtr = False
            self.handle.rts = False
            self.handle.reset_input_buffer()
            while not self.stop_event.is_set():
                try:
                    chunk = self.handle.read(256)
                except serial.SerialTimeoutException:
                    chunk = b""
                if chunk:
                    for line in self.splitter.feed(chunk):
                        self.out.put(line)
                else:
                    tail = self.splitter.flush_idle()
                    if tail is not None:
                        self.out.put(tail)
        except BaseException as exc:
            if self.errors is not None:
                self.errors.put(exc)
        finally:
            tail = self.splitter.flush()
            if tail is not None:
                self.out.put(tail)
            handle, self.handle = self.handle, None
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass
            self.out.put(None)

