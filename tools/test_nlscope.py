import tempfile
import time
import threading
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
    def test_monitor_adv_parse_and_grouped_metrics(self):
        lines = [
            "[2026-11-11_18:00:00:000][linkscope] role=monitor\n",
            "[2026-11-11_18:00:00:001][linkscope] link=sle-passive-seek seek_intv=100 seek_win=100 filter_dup=0\n",
            "[2026-11-11_18:00:00:010]ADV,1,4294967290,3,867EC3DF,-50,0,2,0102\n",
            "[2026-11-11_18:00:00:011]ADV,2,7,11,867ec3df,127,0,1,aa\n",
            "[2026-11-11_18:00:00:012]ADV,3,14,3,867ec3df,-40,0,2,0304\n",
            "[2026-11-11_18:00:00:013]# MON n=3 drop=0 invalid_rssi=1\n",
        ]
        session = feed_lines(lines)
        self.assertEqual(session.signature.role, "monitor")
        self.assertEqual(len(session.adv_records), 3)
        metrics = session.monitor_metrics()
        self.assertEqual(metrics["n"], 3)
        device = metrics["devices"]["867ec3df"]
        self.assertEqual(device["rssi_p50_dbm"], -50)
        self.assertEqual(device["invalid_rssi"], 1)
        # Event 3 crosses a uint32 timer wrap: (14 - 0xfffffffA) mod 2^32 = 20 us.
        self.assertEqual(device["events"]["3"]["interval_p50_us"], 20)
        self.assertEqual(device["events"]["11"]["interval_p50_us"], None)
        self.assertEqual(metrics["board_latest_mon"]["drop"], 0)

    def test_monitor_ready_follows_current_seek_enable(self):
        parser = LineParser()
        parser.signature.measurement_ready = True
        parser.feed("[linkscope] role=monitor\n")
        self.assertFalse(parser.signature.measurement_ready)
        parser.feed("[linkscope] [MON] seek enabled status=0x0\n")
        self.assertFalse(parser.signature.measurement_ready)
        parser.feed("[linkscope] [SELFTEST] pass=32 fail=0 -> PASS\n")
        self.assertFalse(parser.signature.measurement_ready)
        parser.feed("[linkscope] link=sle-passive-seek seek_intv=100 seek_win=100 filter_dup=0\n")
        self.assertTrue(parser.signature.measurement_ready)
        self.assertEqual(parser.signature.seek_interval, 100)
        self.assertEqual(parser.signature.seek_window, 100)
        self.assertEqual(parser.signature.filter_duplicates, 0)

    def test_live_monitor_drops_data_until_current_boot_is_ready(self):
        app = ScopeApp.__new__(ScopeApp)
        app.strict_live_gate = True
        app.pre_ready_samples = 0
        app.parser = LineParser()
        stale = app.parser.feed("ADV,99,100,3,12345678,-40,0,1,aa\n")
        self.assertFalse(app._apply_live_gate(stale))
        self.assertIsNone(stale.adv)
        self.assertEqual(stale.raw_line, "")
        self.assertEqual(app.pre_ready_samples, 1)

        app.parser.feed("[linkscope] role=monitor\n")
        app.parser.feed("[linkscope] [SELFTEST] pass=32 fail=0 -> PASS\n")
        app.parser.feed("[linkscope] link=sle-passive-seek seek_intv=100 seek_win=100 filter_dup=0\n")
        app.parser.feed("[linkscope] [MON] seek enabled status=0x0\n")
        fresh = app.parser.feed("ADV,1,200,3,12345678,-41,0,1,bb\n")
        self.assertTrue(app._apply_live_gate(fresh))
        self.assertIsNotNone(fresh.adv)
        self.assertNotEqual(fresh.raw_line, "")

    def test_malformed_adv_is_rejected(self):
        session = feed_lines([
            "ADV,1,10,3,nothex,-40,0,1,aa\n",
            "ADV,2,20,3,12345678,-40,0,2,aa\n",
            "ADV,3,30,3,12345678,-40,0,1,zz\n",
        ])
        self.assertEqual(session.adv_records, [])

    def test_monitor_reset_is_not_an_interval(self):
        session = feed_lines([
            "ADV,99,100000,3,12345678,-40,0,1,aa\n",
            "ADV,1,100,3,12345678,-41,0,1,aa\n",
            "ADV,2,250,3,12345678,-42,0,1,aa\n",
        ])
        metrics = session.monitor_metrics()
        self.assertEqual(metrics["sequence"]["resets"], 1)
        event = metrics["devices"]["12345678"]["events"]["3"]
        self.assertEqual(event["interval_max_us"], 150)

    def test_monitor_export_and_csv_replay(self):
        session = feed_lines([
            "[2026-11-11_18:00:00:000][linkscope] role=monitor\n",
            "[2026-11-11_18:00:00:010]ADV,1,100,3,12345678,-45,0,1,aa\n",
            "[2026-11-11_18:00:00:020]ADV,2,200,3,12345678,-44,0,1,bb\n",
        ])
        with tempfile.TemporaryDirectory() as directory:
            raw_path, csv_path, json_path, report_path = session.export(Path(directory), "monitor")
            self.assertIn("addr_id", csv_path.read_text(encoding="utf-8-sig"))
            self.assertIn("monitor_metrics", json_path.read_text(encoding="utf-8"))
            self.assertIn("SLE被动监测报告", report_path.read_text(encoding="utf-8"))
            loaded = load_log(csv_path)
            self.assertEqual(len(loaded.adv_records), 2)
            self.assertEqual(loaded.adv_records[1].payload_hex, "bb")
            self.assertTrue(raw_path.exists())

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

    def test_stop_freezes_duration_and_invalidates_queued_source(self):
        class Status:
            value = ""
            def set(self, value):
                self.value = value

        app = ScopeApp.__new__(ScopeApp)
        app.strict_live_gate = True
        app.capture_started_monotonic = time.monotonic() - 5.0
        app.capture_frozen_seconds = 0.0
        app.stop_event = threading.Event()
        app.source_id = 7
        app.session = feed_lines(["S,1,10,30,20,3,4\n"])
        app.paused = False
        app.status = Status()
        app.stop()
        self.assertTrue(app.stop_event.is_set())
        self.assertEqual(app.source_id, 8)
        self.assertTrue(app.paused)
        self.assertGreaterEqual(app.capture_frozen_seconds, 5.0)
        self.assertIn("时长已保留", app.status.value)

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
