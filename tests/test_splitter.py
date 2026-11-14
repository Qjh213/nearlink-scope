import queue
import threading
import time
import unittest
import tempfile
from datetime import datetime
from pathlib import Path

import serial

from serial_reader import LineSplitter, LogSink, SerialReader


class FakeSerial:
    instances = []

    def __init__(self, responses=None, **settings):
        self.responses = list(responses or [])
        self.settings = settings
        self.dtr = True
        self.rts = True
        self.reset_called = False
        self.closed = False
        FakeSerial.instances.append(self)

    def reset_input_buffer(self):
        self.reset_called = True

    def read(self, _size):
        if not self.responses:
            time.sleep(0.005)
            return b""
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True


class SplitterAcceptanceTests(unittest.TestCase):
    def test_01_single_cr_line(self):
        splitter = LineSplitter()
        self.assertEqual(splitter.feed(b"hello\r"), [b"hello\r"])
        self.assertEqual(splitter.pending, b"")

    def test_02_two_cr_lines(self):
        splitter = LineSplitter()
        self.assertEqual(splitter.feed(b"a\rb\r"), [b"a\r", b"b\r"])
        self.assertEqual(splitter.pending, b"")

    def test_03_unterminated_tail_stays_pending(self):
        splitter = LineSplitter()
        self.assertEqual(splitter.feed(b"boot."), [])
        self.assertEqual(splitter.pending, b"boot.")

    def test_04_idle_flush_returns_unterminated_tail(self):
        splitter = LineSplitter(idle_ms=300)
        self.assertEqual(splitter.feed(b"boot."), [])
        self.assertEqual(splitter.flush_idle(splitter._last_byte_at + 0.300), b"boot.")
        self.assertEqual(splitter.pending, b"")

    def test_05_binary_bytes_are_preserved(self):
        splitter = LineSplitter()
        self.assertEqual(splitter.feed(b"\x00\xff\xfe\r"), [b"\x00\xff\xfe\r"])

    def test_06_5000_unterminated_bytes_are_not_lost(self):
        splitter = LineSplitter()
        payload = b"x" * 5000
        lines = splitter.feed(payload)
        self.assertEqual(b"".join(lines) + splitter.pending, payload)
        self.assertEqual(splitter.dropped_bytes, 0)

    def test_07_oversized_unterminated_bytes_are_surfaced_without_loss(self):
        splitter = LineSplitter()
        payload = b"x" * 70000
        lines = splitter.feed(payload)
        self.assertEqual(b"".join(lines) + splitter.pending, payload)
        self.assertEqual(splitter.dropped_bytes, 0)

    def test_08_only_cr_is_a_boundary_and_lf_is_preserved(self):
        splitter = LineSplitter()
        lines = splitter.feed(b"\r\n\r\n")
        self.assertEqual(lines, [b"\r", b"\n\r"])
        self.assertEqual(splitter.pending, b"\n")
        self.assertEqual(b"".join(lines) + splitter.pending, b"\r\n\r\n")

    def test_09_timeout_exception_does_not_stop_reader(self):
        out = queue.Queue()
        stop = threading.Event()
        fake = FakeSerial([b"a", b"\rb", serial.SerialTimeoutException("timeout")])
        reader = SerialReader("COM_TEST", out, stop, serial_factory=lambda **_kwargs: fake)
        reader.start()
        self.assertEqual(out.get(timeout=1), b"a\r")
        time.sleep(0.05)
        self.assertTrue(reader.is_alive())
        self.assertEqual(reader.splitter.pending, b"b")
        stop.set()
        reader.join(timeout=2)
        self.assertFalse(reader.is_alive())

    def test_10_repeated_start_stop_closes_every_handle(self):
        FakeSerial.instances.clear()
        for _ in range(10):
            out = queue.Queue()
            stop = threading.Event()
            reader = SerialReader("COM_TEST", out, stop, serial_factory=FakeSerial)
            reader.start()
            deadline = time.monotonic() + 1
            while reader.handle is None and time.monotonic() < deadline:
                time.sleep(0.005)
            stop.set()
            reader.join(timeout=2)
            self.assertFalse(reader.is_alive())
        self.assertEqual(len(FakeSerial.instances), 10)
        self.assertTrue(all(item.closed and item.reset_called for item in FakeSerial.instances))
        self.assertTrue(all(item.dtr is False and item.rts is False for item in FakeSerial.instances))

    def test_11_stop_flushes_pending_and_exits_within_two_seconds(self):
        out = queue.Queue()
        stop = threading.Event()
        fake = FakeSerial([b"unfinished"])
        reader = SerialReader("COM_TEST", out, stop, serial_factory=lambda **_kwargs: fake)
        reader.start()
        deadline = time.monotonic() + 1
        while reader.splitter.pending != b"unfinished" and time.monotonic() < deadline:
            time.sleep(0.005)
        stop.set()
        reader.join(timeout=2)
        self.assertFalse(reader.is_alive())
        self.assertEqual(out.get(timeout=1), b"unfinished")

    def test_12_lf_is_data_by_default_and_optional_boundary_when_enabled(self):
        default = LineSplitter()
        self.assertEqual(default.feed(b"a\nb"), [])
        self.assertEqual(default.pending, b"a\nb")
        compatible = LineSplitter(split_lf=True)
        self.assertEqual(compatible.feed(b"a\nb"), [b"a\n"])
        self.assertEqual(compatible.pending, b"b")

    def test_13_log_sink_preserves_body_and_terminates_every_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.log"
            fixed = datetime(2026, 11, 14, 8, 0, 0)
            with LogSink(path) as sink:
                sink.append(b"with-cr\r", fixed)
                sink.append(b"idle-half", fixed)
            stamp = b"[2026-11-14_08:00:00:000]"
            self.assertEqual(
                path.read_bytes(),
                stamp + b"with-cr\r\r\n" + stamp + b"idle-half\r\n",
            )


if __name__ == "__main__":
    unittest.main()
