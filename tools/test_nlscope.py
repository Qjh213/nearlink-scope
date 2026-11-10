import tempfile
import time
import unittest
from pathlib import Path

from nlscope_core import LineParser, feed_lines, load_log
from nlscope import ScopeApp, discover_serial_ports


LINES = [
    "[2026-11-10_20:00:00:000][linkscope] role=initiator (A)\n",
    "[2026-11-10_20:00:00:001][linkscope] link=ble-write_req cfg=0 payload=40B rate=40Hz win=1 timeout=50ms\n",
    "[2026-11-10_20:00:00:002][linkscope] [SELFTEST] pass=32 fail=0 -> PASS\n",
    "[2026-11-10_20:00:00:003][linkscope] mtu=247\n",
    "[2026-11-10_20:00:00:004][linkscope] param updated intv=6 latency=0 status=0x0\n",
    "[2026-11-10_20:00:00:005][linkscope] measurement ready after param update\n",
    "[2026-11-10_20:00:00:010]S,1,1000,11000,10000,12,3\n",
    "[2026-11-10_20:00:00:020]S,2,12000,24000,12000,14,5\n",
    "[2026-11-10_20:00:00:030]S,3,25000,40000,15000,10,4\n",
    "[2026-11-10_20:00:00:031]# SUM n=3 p50=12000 sent=4 recv=3 to=1 dup=1 ooo=0 bad=0 drop=0 loss_ppm=250000\n",
]


class ParserTests(unittest.TestCase):
    def test_port_discovery_shape(self):
        for device, display in discover_serial_ports():
            self.assertTrue(device)
            self.assertTrue(display.startswith(device + " — "))

    def test_duration_format(self):
        self.assertEqual(ScopeApp._format_duration(0), "00:00:00")
        self.assertEqual(ScopeApp._format_duration(3661.9), "01:01:01")

    def test_resume_clock_keeps_frozen_duration(self):
        app = ScopeApp.__new__(ScopeApp)
        app.strict_live_gate = True
        app.capture_started_monotonic = None
        app.capture_frozen_seconds = 12.0
        app._resume_capture_clock()
        self.assertIsNotNone(app.capture_started_monotonic)
        self.assertGreaterEqual(app._capture_seconds(), 12.0)
        self.assertLess(app._capture_seconds(), 12.2)

    def test_signature_and_metrics(self):
        session = feed_lines(LINES)
        self.assertEqual(session.signature.role, "initiator")
        self.assertEqual(session.signature.link, "ble-write_req")
        self.assertEqual(session.signature.payload_b, 40)
        self.assertEqual(session.signature.mtu, 247)
        self.assertEqual(session.signature.interval, 6)
        self.assertTrue(session.signature.measurement_ready)
        metrics = session.metrics()
        self.assertEqual(metrics["n"], 3)
        self.assertEqual(metrics["p50_us"], 12000)
        self.assertEqual(metrics["p99_us"], 15000)
        self.assertEqual(metrics["jitter_us"], 2500.0)
        self.assertEqual(metrics["to"], 1)
        self.assertEqual(metrics["dup"], 1)
        self.assertEqual(metrics["sample_span_s"], 0.02)

    def test_plain_csv_without_qcom_timestamp(self):
        parser = LineParser()
        event = parser.feed("S,7,10,30,20,3,4\n")
        self.assertIsNotNone(event.sample)
        self.assertEqual(event.sample.seq, 7)
        self.assertEqual(event.sample.rtt_us, 20)

    def test_live_line_gets_receive_timestamp(self):
        from datetime import datetime
        parser = LineParser()
        event = parser.feed("S,7,10,30,20,3,4\n", received_at=datetime(2026, 11, 10, 1, 2, 3, 456000))
        self.assertTrue(event.raw_line.startswith("[2026-11-10_01:02:03:456]S,7"))

    def test_export(self):
        session = feed_lines(LINES)
        with tempfile.TemporaryDirectory() as directory:
            raw_path, csv_path, json_path, report_path = session.export(Path(directory), "demo")
            self.assertIn("measurement ready", raw_path.read_text(encoding="utf-8"))
            self.assertTrue(csv_path.exists())
            self.assertIn("ble-write_req", json_path.read_text(encoding="utf-8"))
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("RTT p99", report)
            self.assertIn("15.000 ms", report)
            loaded = load_log(csv_path)
            self.assertEqual(len(loaded.samples), 3)
            self.assertEqual(loaded.signature.link, "ble-write_req")
            self.assertEqual(loaded.metrics()["sample_span_s"], 0.02)


if __name__ == "__main__":
    unittest.main()
