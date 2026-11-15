import queue
import csv
import hashlib
import json
import tempfile
import time
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from nlscope_core import (LineParser, PlatformSignature, Session, count_csv_rows, evaluate_evidence,
                          feed_lines, format_evidence, format_preflight, load_csv, load_log,
                          preflight_items)
from nlscope import (M5_PROFILES, ScopeApp, discover_serial_ports, export_cli, file_identity,
                     reproduce_markdown, reproduce_run, repository_identity)
from experiment_specs import ExperimentSpec, expected_mapping, load_specs
import serial_terminal
from serial_terminal import (BootSessionGate, SerialLineAccumulator, SerialSettings, SerialTerminalWindow,
                             encode_outbound, format_received, is_board_boot_line,
                             pump_serial_lines)


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
    def test_analysis_history_starts_at_explicit_board_boot(self):
        self.assertTrue(is_board_boot_line("boot.\r\n"))
        self.assertTrue(is_board_boot_line("[2026-11-12_10:00:00:000]boot."))
        self.assertTrue(is_board_boot_line("\x10oboot."))
        self.assertTrue(is_board_boot_line("q襥R'boot."))
        self.assertFalse(is_board_boot_line("Flashboot Uart Init Succ!"))
        self.assertFalse(is_board_boot_line("flashboot."))
        self.assertFalse(is_board_boot_line("loaderboot."))
        self.assertFalse(is_board_boot_line("reboot."))

    def test_serial_line_accumulator_reassembles_timeout_split_boot(self):
        framer = SerialLineAccumulator()
        noisy_prefix = bytes.fromhex("71 e8 a5 a5 52 27")
        self.assertEqual(framer.feed(noisy_prefix + b"boo"), [])
        lines = framer.feed(b"t.\r\nFlash Init Succ!\r\npartial")
        self.assertEqual(lines, [noisy_prefix + b"boot.\r", b"\nFlash Init Succ!\r"])
        self.assertTrue(is_board_boot_line(lines[0].decode("utf-8", errors="replace")))
        self.assertEqual(framer.feed(b" tail\r\n"), [b"\npartial tail\r"])

    def test_serial_line_accumulator_accepts_every_two_way_split(self):
        payload = bytes.fromhex("71 e8 a5 a5 52 27") + b"boot.\r\nnext\r\n"
        expected = [bytes.fromhex("71 e8 a5 a5 52 27") + b"boot.\r", b"\nnext\r"]
        for split in range(len(payload) + 1):
            with self.subTest(split=split):
                framer = SerialLineAccumulator()
                lines = framer.feed(payload[:split]) + framer.feed(payload[split:])
                self.assertEqual(lines, expected)
                self.assertEqual(framer.pending, b"\n")

        framer = SerialLineAccumulator()
        bytewise = []
        for value in payload:
            bytewise.extend(framer.feed(bytes((value,))))
        self.assertEqual(bytewise, expected)
        self.assertEqual(framer.pending, b"\n")

    def test_shared_serial_pump_reassembles_lines_for_main_and_aux(self):
        stop_event = threading.Event()
        class Handle:
            def __init__(self):
                self.chunks = [b"bo", b"ot.\r", b"\nS,1,10,30,", b"20,3,4\r\n"]
            def read(self, _size):
                chunk = self.chunks.pop(0)
                if not self.chunks:
                    stop_event.set()
                return chunk
        received = []
        pump_serial_lines(Handle(), stop_event, lambda _stamp, line: received.append(line))
        self.assertEqual(received, [b"boot.\r", b"\nS,1,10,30,20,3,4\r", b"\n"])

    def test_shared_serial_pump_timestamps_each_line_separately(self):
        stop_event = threading.Event()
        class Handle:
            def read(self, _size):
                stop_event.set()
                return b"a\rb\r"
        class FakeDateTime:
            calls = 0
            @classmethod
            def now(cls):
                cls.calls += 1
                return cls.calls

        received = []
        original_datetime = serial_terminal.datetime
        try:
            serial_terminal.datetime = FakeDateTime
            pump_serial_lines(Handle(), stop_event, lambda stamp, line: received.append((stamp, line)))
        finally:
            serial_terminal.datetime = original_datetime
        self.assertEqual(received, [(1, b"a\r"), (2, b"b\r")])

    def test_serial_line_accumulator_flushes_idle_tail_without_loss(self):
        framer = SerialLineAccumulator(idle_ms=300)
        self.assertEqual(framer.feed(b"partial diagnostic"), [])
        self.assertIsNone(framer.flush_idle(framer.last_byte_at + 0.299))
        self.assertEqual(framer.flush_idle(framer.last_byte_at + 0.300), b"partial diagnostic")
        self.assertEqual(framer.dropped_bytes, 0)

    def test_serial_line_accumulator_surfaces_oversized_unterminated_burst(self):
        framer = SerialLineAccumulator(max_pending=64)
        payload = b"x" * 65
        self.assertEqual(framer.feed(payload), [payload])
        self.assertEqual(framer.pending, bytearray())
        self.assertEqual(framer.dropped_bytes, 0)
        self.assertEqual(framer.forced_flushes, 1)

    def test_boot_gate_state_machine_only_reset_rearms_it(self):
        gate = BootSessionGate()
        self.assertFalse(gate.admit("old sample").accepted)
        self.assertEqual(gate.rejected_lines, 1)
        decision = gate.admit("q襥R'boot.\r\n")
        self.assertTrue(decision.accepted)
        self.assertTrue(decision.activated)
        self.assertTrue(gate.active)
        self.assertTrue(gate.admit("S,1,10,30,20,3,4\r\n").accepted)
        self.assertTrue(gate.active)
        gate.reset()
        self.assertFalse(gate.active)
        self.assertEqual(gate.rejected_lines, 0)
        self.assertFalse(gate.admit("S,2,40,60,20,3,4\r\n").accepted)
        for false_boundary in ("flashboot.\r\n", "loaderboot.\r\n", "reboot.\r\n"):
            self.assertFalse(gate.admit(false_boundary).accepted)
            self.assertFalse(gate.active)

    def test_aux_terminal_displays_lines_before_boot(self):
        class Window:
            @staticmethod
            def winfo_exists():
                return True
            @staticmethod
            def after(_delay, _callback):
                return None
        class Status:
            def set(self, _value):
                return None

        terminal = SerialTerminalWindow.__new__(SerialTerminalWindow)
        terminal.events = queue.Queue()
        terminal.source_id = 1
        terminal.boot_gate = BootSessionGate()
        terminal.window = Window()
        terminal.status = Status()
        terminal.open_button = None
        terminal.handle = None
        received = []
        terminal._append_received = lambda stamp, data: received.append((stamp, data))
        terminal.events.put((1, "data", datetime.now(), b"pre-boot diagnostic\r\n"))

        terminal.poll()

        self.assertEqual(received[0][1], b"pre-boot diagnostic\r\n")
        self.assertFalse(terminal.boot_gate.active)

    def test_serial_terminal_payload_encoding(self):
        self.assertEqual(encode_outbound("AT", False, True), b"AT\r\n")
        self.assertEqual(encode_outbound("01 aB ff", True, False), b"\x01\xab\xff")
        with self.assertRaises(ValueError):
            encode_outbound("ABC", True, False)

    def test_serial_terminal_format_and_settings(self):
        self.assertEqual(format_received(b"A\xff", False), "A�")
        self.assertEqual(format_received(b"\x01\xab", True), "01 AB")
        SerialSettings("COM4", 921600, 4, 1.5, "Mark", "HW Ctrl Flow").validate()
        with self.assertRaises(ValueError):
            SerialSettings("", 115200).validate()

    def test_main_serial_send_reuses_analysis_handle(self):
        class Handle:
            is_open = True
            def __init__(self):
                self.payload = b""
            def write(self, payload):
                self.payload = payload
                return len(payload)
        app = ScopeApp.__new__(ScopeApp)
        app.main_serial_lock = threading.Lock()
        app.main_serial_handle = Handle()
        self.assertEqual(app.send_main_serial(b"hello"), 5)
        self.assertEqual(app.main_serial_handle.payload, b"hello")
        app.main_serial_handle = None
        with self.assertRaises(RuntimeError):
            app.send_main_serial(b"x")

    def test_opening_analysis_mirror_is_raw_and_never_rearms_main_boot_gate(self):
        class Window:
            @staticmethod
            def winfo_exists():
                return True
        class Console:
            window = Window()
            begun = waited = False
            def begin_boot_session(self):
                self.begun = True
            def wait_for_boot(self, clear=True):
                self.waited = True

        app = ScopeApp.__new__(ScopeApp)
        app.analysis_console = Console()
        app.main_boot_gate = BootSessionGate()
        app.main_boot_gate.admit("boot.\r\n")
        app._prepare_analysis_console()
        self.assertTrue(app.main_boot_gate.active)
        self.assertFalse(app.analysis_console.begun)
        self.assertTrue(app.analysis_console.waited)

        app.analysis_console = Console()
        app.main_boot_gate.reset()
        app._prepare_analysis_console()
        self.assertFalse(app.main_boot_gate.active)
        self.assertFalse(app.analysis_console.begun)
        self.assertTrue(app.analysis_console.waited)

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
        self.assertEqual(device["rssi_p50_dbm"], -45)
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
        class Expected:
            def get(self):
                return "自动识别平台签名"
        app.expected_profile_name = Expected()
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

    def test_expected_m5_signature_rejects_wrong_role(self):
        app = ScopeApp.__new__(ScopeApp)
        app.strict_live_gate = True
        app.pre_ready_samples = 0
        app.parser = LineParser()
        class Expected:
            def get(self):
                return "M5 cfg500 基线 40B/40Hz"
        app.expected_profile_name = Expected()
        signature = app.parser.signature
        signature.role, signature.link, signature.cfg = "monitor", "sle-passive-seek", "unknown"
        signature.measurement_ready = True
        stale = app.parser.feed("S,1,10,30,20,3,4\n")
        self.assertFalse(app._apply_live_gate(stale))
        self.assertTrue(app._signature_mismatches())

        expected = M5_PROFILES["M5 cfg500 基线 40B/40Hz"]
        for field, value in expected.items():
            setattr(signature, field, value)
        signature.mtu, signature.interval = 520, 24
        fresh = app.parser.feed("S,2,40,60,20,3,4\n")
        self.assertTrue(app._apply_live_gate(fresh))

    def test_versioned_experiment_specs_are_external_and_valid(self):
        specs = load_specs(Path(__file__).with_name("experiment_specs.json"))
        self.assertEqual(specs["仅原始日志（不进入统计）"].mode, "raw")
        self.assertEqual(specs["自动识别平台签名"].mode, "auto")
        self.assertEqual(specs["M5 cfg522 Rate 25Hz"].expect["rate_hz"], 25)

    def test_repository_identity_is_explicit(self):
        identity = repository_identity()
        self.assertIn(identity["source"], ("git", "unavailable"))
        self.assertIn("git_head", identity)
        self.assertIn("git_dirty", identity)

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
        class Handle:
            def __init__(self):
                self.closed = False
            def close(self):
                self.closed = True
        handle = Handle()
        app.main_serial_lock = threading.Lock()
        app.main_serial_handle = handle
        app.stop()
        self.assertTrue(app.stop_event.is_set())
        self.assertEqual(app.source_id, 8)
        self.assertTrue(app.paused)
        self.assertGreaterEqual(app.capture_frozen_seconds, 5.0)
        self.assertIn("时长已保留", app.status.value)
        self.assertTrue(handle.closed)
        self.assertIsNone(app.main_serial_handle)

    def test_begin_source_immediately_redraws_empty_session(self):
        app = ScopeApp.__new__(ScopeApp)
        app.source_id = 4
        app.stop_event = threading.Event()
        app.stop = lambda: None
        draws = []
        app.draw = lambda force=False: draws.append(force)

        source_id, stop_event = app._begin_source("COM3")

        self.assertEqual(source_id, 5)
        self.assertIs(stop_event, app.stop_event)
        self.assertEqual(app.source_label, "COM3")
        self.assertEqual(len(app.session.samples), 0)
        self.assertEqual(draws, [True])

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
        self.assertEqual(metrics["p99_us"], 14940)
        self.assertEqual(metrics["jitter_us"], 2500.0)
        self.assertEqual(metrics["to"], 1)
        self.assertEqual(metrics["dup"], 1)
        self.assertEqual(metrics["sample_span_s"], 0.02)
        self.assertEqual(metrics["sum_frames_seen"], 1)
        self.assertEqual(metrics["samples_since_latest_sum"], 0)

    def test_missing_sum_is_unknown_not_zero(self):
        session = feed_lines(["S,1,10,30,20,3,4\n"])
        metrics = session.metrics()
        for key in ("sent", "recv", "to", "dup", "ooo", "bad", "drop", "rx_drop", "send_err",
                    "cfm_err", "dlt_neg"):
            self.assertIsNone(metrics[key], key)
        self.assertEqual(metrics["sum_frames_seen"], 0)
        self.assertIsNone(metrics["samples_since_latest_sum"])
        self.assertIn("未收到板端 SUM", session.markdown_report())

    def test_percentile_uses_linear_interpolation(self):
        from nlscope_core import percentile
        self.assertEqual([percentile([1, 2, 3, 4], p) for p in (.5, .9, .99)], [2, 4, 4])
        self.assertEqual(percentile([0, 100], .9), 90)

    def test_missing_counter_in_sum_remains_unknown(self):
        session = feed_lines([
            "S,1,10,30,20,3,4\n",
            "# SUM n=1 sent=1 recv=1 to=0 dup=0 bad=0 drop=0 loss_ppm=0\n",
            "S,2,40,60,20,3,4\n",
        ])
        metrics = session.metrics()
        self.assertEqual(metrics["to"], 0)
        self.assertIsNone(metrics["ooo"])
        self.assertIsNone(metrics["rx_drop"])
        self.assertIsNone(metrics["send_err"])
        self.assertEqual(metrics["sum_frames_seen"], 1)
        self.assertEqual(metrics["samples_since_latest_sum"], 1)

    def test_plain_csv_without_qcom_timestamp(self):
        parser = LineParser()
        event = parser.feed("S,7,10,30,20,3,4\n")
        self.assertIsNotNone(event.sample)
        self.assertEqual(event.sample.seq, 7)
        self.assertEqual(event.sample.rtt_us, 20)

    def test_numeric_but_corrupt_sample_is_rejected(self):
        parser = LineParser()
        bad = parser.feed("S,1,100,120,999,3,4\n")
        self.assertIsNone(bad.sample)
        self.assertEqual(bad.parse_failure, "sample")
        self.assertIsNone(parser.feed("S,70000,100,120,20,3,4\n").sample)
        self.assertIsNone(parser.feed("S,1,100,120,20,30,4\n").sample)
        # uint32 timer wrap remains valid.
        wrapped = parser.feed("S,2,4294967290,14,20,3,4\n").sample
        self.assertIsNotNone(wrapped)
        self.assertEqual(wrapped.rtt_us, 20)

    def test_sample_span_tolerates_out_of_order_file_timestamps(self):
        session = feed_lines([
            "[2026-11-10_20:00:02:000]S,1,10,30,20,3,4\n",
            "[2026-11-10_20:00:00:000]S,2,40,60,20,3,4\n",
            "[2026-11-10_20:00:01:000]S,3,70,90,20,3,4\n",
        ])
        self.assertEqual(session.sample_span_seconds(), 2.0)

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

    def test_exported_log_lines_all_start_with_timestamp(self):
        # 真实串口按 CR 切分，"\r\n" 的 LF 会留在下一行开头；如果只 rstrip，
        # raw_line 变成 "[stamp]\n正文"，导出按行 join 后一半行没有时间戳。
        parser = LineParser()
        session = Session()
        stamp = datetime(2026, 11, 10, 20, 0, 0)
        for line in ("\nboot.\r", "\nFlash Init Fail!\r", "\nS,1,1000,11000,10000,12,3\r"):
            session.add(parser.feed(line, received_at=stamp), parser.signature)
        with tempfile.TemporaryDirectory() as directory:
            raw_path, *_ = session.export(Path(directory), "demo")
            lines = raw_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines)
        self.assertEqual([line for line in lines if not line.startswith("[")], [])
        self.assertIn("[2026-11-10_20:00:00:000]boot.", lines)
        self.assertIn("[2026-11-10_20:00:00:000]S,1,1000,11000,10000,12,3", lines)

    def test_export_refuses_to_overwrite_evidence(self):
        session = feed_lines(LINES)
        with tempfile.TemporaryDirectory() as directory:
            session.export(Path(directory), "demo")
            with self.assertRaises(FileExistsError):
                session.export(Path(directory), "demo")

    def test_csv_replay_counts_and_rejects_corrupt_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(("seq", "t1_us", "t4_us", "rtt_us", "bproc_us", "dlt_us", "wall_time"))
                writer.writerow((1, 10, 30, 20, 3, 4, ""))
                writer.writerow((2, 40, 60, 999, 3, 4, ""))
                writer.writerow((70000, 40, 60, 20, 3, 4, ""))
                writer.writerow((3, "bad", 60, 20, 3, 4, ""))
            session = load_log(path)
            self.assertEqual(len(session.samples), 1)
            self.assertEqual(session.metrics()["csv_rows_total"], 4)
            self.assertEqual(session.metrics()["csv_rows_skipped"], 3)

    def test_sum_slash_fields_preserve_max_and_decimal_leading_zero(self):
        session = feed_lines(["# SUM n=050 bproc=41/77 dlt=08/09 to=0\n"])
        self.assertEqual(session.counters["n"], 50)
        self.assertEqual(session.counters["bproc"], 41)
        self.assertEqual(session.counters["bproc_max"], 77)
        self.assertEqual(session.counters["dlt"], 8)
        self.assertEqual(session.counters["dlt_max"], 9)

    def test_sample_sequence_metrics_handle_uint16_wrap(self):
        session = feed_lines([
            "S,65534,10,30,20,3,4\n",
            "S,65535,40,60,20,3,4\n",
            "S,0,70,90,20,3,4\n",
            "S,2,100,120,20,3,4\n",
        ])
        self.assertEqual(session.sample_sequence_metrics(), {
            "gaps": 1, "duplicates": 0, "out_of_order": 0, "wraps": 1,
        })

    def test_sample_sequence_metrics_report_out_of_order_and_duplicates(self):
        session = feed_lines([
            "S,10,10,30,20,3,4\n",
            "S,10,40,60,20,3,4\n",
            "S,2,70,90,20,3,4\n",
        ])
        self.assertEqual(session.sample_sequence_metrics(), {
            "gaps": 0, "duplicates": 1, "out_of_order": 1, "wraps": 0,
        })

    def test_board_span_uses_board_clock_and_ignores_backwards_steps(self):
        session = feed_lines([
            "S,1,1000,2000,1000,3,4\n",
            "S,2,101000,102000,1000,3,4\n",
            "S,1,103000,104000,1000,3,4\n",
            "S,3,105000,106000,1000,3,4\n",
        ])
        # 100 ms 前进 + 回退步不计 + 回退后再前进的 2 ms（t1 从 103000 到 105000）
        self.assertAlmostEqual(session.board_sample_span_seconds(), 0.102, places=6)

    def test_board_span_is_zero_with_a_single_sample(self):
        session = feed_lines(["S,1,1000,2000,1000,3,4\n"])
        self.assertEqual(session.board_sample_span_seconds(), 0.0)

    def test_board_restarts_counts_sample_sequence_backjump_and_blanks_board_span(self):
        session = feed_lines([
            "[2026-11-10_20:00:00:010]S,1200,1000,11000,10000,12,3\n",
            "[2026-11-10_20:00:00:011]S,1201,14000,24000,10000,12,3\n",
            "[2026-11-10_20:00:00:020]S,1,27000,37000,10000,12,3\n",
            "[2026-11-10_20:00:00:021]S,2,40000,50000,10000,12,3\n",
        ])
        self.assertEqual(session.board_restarts, 1)
        self.assertEqual(session.metrics()["board_restarts"], 1)
        # 跨重启累加板端时长没有物理意义，必须是 None 而不是一个看起来合理的数。
        self.assertIsNone(session.board_sample_span_seconds())

    def test_sum_regression_alone_is_not_a_restart(self):
        # 打开串口时缓冲区会残留上一次上电的 SUM（n 很大），随后才是本次的 n=200。
        # 样本序号连续说明只有一次上电，不能因为 SUM 回退就判成会话混杂。
        session = feed_lines([
            "[2026-11-10_20:00:00:010]# SUM n=2886200 p50=14867 sent=9 recv=9 to=0\n",
            "[2026-11-10_20:00:00:011]# SUM n=200 p50=14867 sent=9 recv=9 to=0\n",
            "[2026-11-10_20:00:00:020]S,1,1000,11000,10000,12,3\n",
            "[2026-11-10_20:00:00:030]S,2,20000,30000,10000,12,3\n",
        ])
        self.assertEqual(session.board_restarts, 0)
        self.assertIsNotNone(session.board_sample_span_seconds())

    def test_file_identity_hashes_bytes_not_names(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "linkscope-fake.fwpkg"
            package.write_bytes(b"firmware-bytes")
            identity = file_identity(str(package))
            self.assertEqual(identity["status"], "provided")
            self.assertEqual(identity["sha256"], hashlib.sha256(b"firmware-bytes").hexdigest())
            self.assertEqual(identity["size"], 14)
            self.assertEqual(identity["path"], str(package.resolve()))

    def test_file_identity_is_explicit_when_not_provided_or_missing(self):
        self.assertEqual(file_identity("")["status"], "not_provided")
        missing = file_identity(str(Path(tempfile.gettempdir()) / "no-such-package.fwpkg"))
        self.assertEqual(missing["status"], "unreadable")
        self.assertIsNone(missing["sha256"])

    def test_specs_reject_bad_schema_and_duplicate_names(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / "bad.json"
            bad.write_text('{"schema_version": 99, "profiles": []}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_specs(bad)
            duplicate = Path(directory) / "dup.json"
            duplicate.write_text(
                '{"schema_version": 1, "profiles": ['
                '{"name": "a", "mode": "auto"}, {"name": "a", "mode": "auto"}]}',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_specs(duplicate)
            measure_without_expect = Path(directory) / "empty.json"
            measure_without_expect.write_text(
                '{"schema_version": 1, "profiles": [{"name": "m", "mode": "measure", "expect": {}}]}',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_specs(measure_without_expect)

    def test_expected_mapping_leaves_raw_and_auto_unconstrained(self):
        mapping = expected_mapping({"r": ExperimentSpec("r", "raw", {}),
                                    "a": ExperimentSpec("a", "auto", {}),
                                    "m": ExperimentSpec("m", "measure", {"cfg": "500"})})
        self.assertIsNone(mapping["r"])
        self.assertIsNone(mapping["a"])
        self.assertEqual(mapping["m"], {"cfg": "500"})

    def test_export_cli_reports_overwrite_with_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            session = feed_lines(["S,1,1000,2000,1000,3,4\n"])
            self.assertEqual(export_cli(session, Path(directory), "run1"), 0)
            self.assertEqual(export_cli(session, Path(directory), "run1"), 5)

    @staticmethod
    def complete_record() -> dict:
        return {
            "analysis_samples": 2,
            "pc_sequence": {"samples": 2, "gaps": 0, "duplicates": 0, "out_of_order": 0, "wraps": 0},
            "parse_failures": {"decode": 0, "sample": 0, "adv": 0},
            "platform_signature": {"cfg": 500, "role": "initiator"},
            "sum_frames_seen": 3,
            "firmware_identity": {"initiator": {"status": "provided"},
                                  "responder": {"status": "provided"}},
            "source_identity": {"head": "0123456789abcdef", "dirty": False},
            "analysis_raw_bytes": 1024,
        }

    def test_evidence_checks_pass_when_everything_is_present(self):
        checks = evaluate_evidence(self.complete_record(), 2)
        self.assertEqual(checks["status"], "pass")
        self.assertTrue(all(item["status"] == "pass" for item in checks["items"]))
        self.assertIn("全部通过", format_evidence(checks))

    def test_source_identity_accepts_run_json_git_head_shape(self):
        # run.json 写的是 git_head/git_dirty；早期自检只认 head/dirty，会把实机导出误判成 warn。
        record = self.complete_record()
        record["source_identity"] = {
            "git_head": "12dac95d4f9d8d98b6ddeaca04f77f1808031dec",
            "git_dirty": True,
            "source": "git",
        }
        checks = evaluate_evidence(record, 2)
        by_name = {item["name"]: item for item in checks["items"]}
        self.assertEqual(by_name["source"]["status"], "pass")
        self.assertIn("12dac95", by_name["source"]["detail"])
        self.assertIn("未提交", by_name["source"]["detail"])
        self.assertEqual(checks["status"], "pass")

    def test_evidence_checks_fail_when_csv_rows_disagree(self):
        checks = evaluate_evidence(self.complete_record(), 1)
        self.assertEqual(checks["status"], "fail")
        detail = {item["name"]: item for item in checks["items"]}["csv_rows"]
        self.assertEqual(detail["status"], "fail")
        self.assertIn("1", detail["detail"])

    def test_evidence_checks_warn_on_missing_board_sum_and_firmware(self):
        record = self.complete_record()
        record["sum_frames_seen"] = 0
        record["firmware_identity"]["responder"] = {"status": "not_provided"}
        checks = evaluate_evidence(record, 2)
        self.assertEqual(checks["status"], "warn")
        by_name = {item["name"]: item for item in checks["items"]}
        self.assertEqual(by_name["board_sum"]["status"], "warn")
        self.assertEqual(by_name["firmware"]["status"], "warn")
        self.assertIn("responder", by_name["firmware"]["detail"])

    def test_evidence_checks_warn_on_sequence_gaps(self):
        record = self.complete_record()
        record["pc_sequence"] = {"samples": 2, "gaps": 3, "duplicates": 0, "out_of_order": 0}
        checks = evaluate_evidence(record, 2)
        by_name = {item["name"]: item for item in checks["items"]}
        self.assertEqual(by_name["sequence"]["status"], "warn")
        self.assertIn("gaps=3", by_name["sequence"]["detail"])
        self.assertEqual(checks["status"], "warn")

    def test_evidence_warns_on_inherited_firmware(self):
        record = self.complete_record()
        record["firmware_identity"]["initiator"] = {
            "status": "provided", "sha256": "a" * 64, "inherited": True,
        }
        checks = evaluate_evidence(record, 2)
        by_name = {item["name"]: item for item in checks["items"]}
        self.assertEqual(by_name["firmware"]["status"], "warn")
        self.assertIn("initiator", by_name["firmware"]["detail"])
        self.assertIn("沿用", by_name["firmware"]["detail"])

    def test_count_csv_rows_ignores_header(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.csv"
            path.write_text("seq,t1_us\n1,10\n2,20\n", encoding="utf-8")
            self.assertEqual(count_csv_rows(path), 2)
            self.assertEqual(count_csv_rows(Path(directory) / "missing.csv"), -1)


class UnsyncedClockSampleTests(unittest.TestCase):
    """断链后 t4-t3 会变成几千万微秒，这类样本不能被 rtt 上限判死。"""

    LINE = "S,1921,69308672,69324869,16197,78,49755686"

    def test_sample_with_dlt_far_above_rtt_is_accepted(self):
        event = LineParser("COM3").feed(self.LINE + "\r\n")
        self.assertIsNone(event.parse_failure)
        self.assertIsNotNone(event.sample)
        self.assertEqual(event.sample.rtt_us, 16197)
        self.assertEqual(event.sample.dlt_us, 49755686)

    def test_csv_replay_accepts_the_same_row(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.csv"
            path.write_text("seq,t1_us,t4_us,rtt_us,bproc_us,dlt_us\n"
                            "1921,69308672,69324869,16197,78,49755686\n", encoding="utf-8")
            session = load_csv(path)
            self.assertEqual(len(session.samples), 1)
            self.assertEqual(session.csv_rows_skipped, 0)
            self.assertEqual(session.samples[0].dlt_us, 49755686)

    def test_csv_without_dlt_column_still_loads_and_reports_none(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.csv"
            path.write_text("seq,t1_us,t4_us,rtt_us,bproc_us,wall_time\n"
                            "5,1000,11000,10000,12,2026-11-15T11:00:00.000\n", encoding="utf-8")
            session = load_csv(path)
            self.assertEqual(len(session.samples), 1)
            self.assertIsNone(session.samples[0].dlt_us)
            self.assertEqual(session.csv_rows_skipped, 0)

    def test_export_omits_dlt_column_but_keeps_it_in_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            session = feed_lines(["S,5,1000,11000,10000,12,987654\n"])
            self.assertEqual(session.samples[0].dlt_us, 987654)
            session.export(Path(directory), "demo")
            header = (Path(directory) / "demo.csv").read_text(encoding="utf-8").splitlines()[0]
            self.assertNotIn("dlt", header)
            self.assertIn("rtt_us", header)

    def test_rtt_inconsistent_row_is_still_rejected(self):
        event = LineParser("COM3").feed("S,7,1000,11000,9999,10,0\r\n")
        self.assertEqual(event.parse_failure, "sample")
        self.assertIsNone(event.sample)


class RecoveryDirectoryTests(unittest.TestCase):
    """恢复目录不可写时必须降级，而不是让串口打不开。"""

    def test_prefers_localappdata_when_writable(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {"LOCALAPPDATA": str(Path(directory) / "local"),
                   "TEMP": str(Path(directory) / "temp")}
            with mock.patch.dict(serial_terminal.os.environ, env, clear=False):
                root = serial_terminal.recovery_root()
                self.assertEqual(root, Path(directory) / "local" / "NearLinkLinkScope" / "recovery")
                self.assertTrue(root.is_dir())

    def test_falls_back_to_temp_when_localappdata_is_not_a_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            blocker = Path(directory) / "local"
            blocker.write_bytes(b"not a directory")
            env = {"LOCALAPPDATA": str(blocker), "TEMP": str(Path(directory) / "temp")}
            with mock.patch.dict(serial_terminal.os.environ, env, clear=False):
                root = serial_terminal.recovery_root()
                self.assertEqual(root, Path(directory) / "temp" / "NearLinkLinkScope" / "recovery")
                self.assertTrue(root.is_dir())

    def test_returns_none_when_every_candidate_is_unwritable(self):
        with tempfile.TemporaryDirectory() as directory:
            blocker = Path(directory) / "local"
            blocker.write_bytes(b"not a directory")
            workdir = Path(directory) / "cwd"
            workdir.mkdir()
            (workdir / "out").write_bytes(b"not a directory")
            env = {"LOCALAPPDATA": str(blocker), "TEMP": str(blocker), "TMP": str(blocker)}
            with mock.patch.dict(serial_terminal.os.environ, env, clear=False):
                with mock.patch.object(serial_terminal.Path, "cwd", return_value=workdir):
                    self.assertIsNone(serial_terminal.recovery_root())
                    self.assertIsNone(serial_terminal.recovery_path("COM3", "analysis"))


class CapabilityTests(unittest.TestCase):
    """能力分级：#5 要求界面/报告说清"这份数据允许下什么结论"。"""

    SAMPLES = [
        "S,1,1000,11000,10000,12,3\n",
        "S,2,26000,37000,11000,13,3\n",
        "S,3,51000,62000,11000,14,3\n",
    ]

    def test_active_session_without_sum_can_only_reach_rtt(self):
        caps = feed_lines(self.SAMPLES).capabilities()
        self.assertEqual(caps["level"], "rtt")
        self.assertEqual(caps["session_type"], "active")
        reliability = next(item for item in caps["items"] if item["key"] == "reliability")
        self.assertFalse(reliability["available"])
        self.assertIn("未收到板端 SUM", reliability["reason"])
        self.assertIn("不可算可靠性计数", caps["label"])

    def test_active_session_with_sum_reaches_reliability(self):
        caps = feed_lines(self.SAMPLES + ["# SUM n=3 sent=3 recv=3 to=0 dup=0\n"]).capabilities()
        self.assertEqual(caps["level"], "reliability")
        reliability = next(item for item in caps["items"] if item["key"] == "reliability")
        self.assertTrue(reliability["available"])
        self.assertEqual(reliability["reason"], "")

    def test_sum_missing_counters_blocks_reliability(self):
        caps = feed_lines(self.SAMPLES + ["# SUM n=3 sent=3 recv=3\n"]).capabilities()
        self.assertEqual(caps["level"], "rtt")
        reliability = next(item for item in caps["items"] if item["key"] == "reliability")
        self.assertIn("缺字段", reliability["reason"])
        self.assertIn("to", reliability["reason"])

    def test_monitor_session_stops_at_interval(self):
        caps = feed_lines([
            "ADV,1,1000,0,aabbccdd,-40,0,2,1122\n",
            "ADV,2,61000,0,aabbccdd,-41,0,2,3344\n",
        ]).capabilities()
        self.assertEqual(caps["session_type"], "monitor")
        self.assertEqual(caps["level"], "interval")
        rtt = next(item for item in caps["items"] if item["key"] == "rtt")
        self.assertIn("被动监听", rtt["reason"])

    def test_export_payload_and_report_describe_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, meta_path, report_path = feed_lines(self.SAMPLES).export(Path(directory), "demo")
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            report = report_path.read_text(encoding="utf-8")
        self.assertEqual(payload["capabilities"]["level"], "rtt")
        self.assertIn("## 统计能力", report)
        self.assertIn("可算RTT", report)
        self.assertIn("未收到板端 SUM", report)


    def test_mixed_session_keeps_monitor_records_out_of_sample_metrics(self):
        # 串口没关就换了固件时，同一份日志里会同时出现 S 行和 ADV 行；两者不能混算。
        session = feed_lines(self.SAMPLES + ["ADV,1,1000,0,aabbccdd,-40,0,2,1122\n"])
        caps = session.capabilities()
        self.assertEqual(caps["session_type"], "active+monitor")
        with tempfile.TemporaryDirectory() as directory:
            _, _, meta_path, report_path = session.export(Path(directory), "demo")
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            report = report_path.read_text(encoding="utf-8")
        self.assertEqual(payload["pc_exact_metrics"]["n"], 3)
        self.assertEqual(payload["monitor_metrics"]["n"], 1)
        self.assertIn("附带监控记录", report)
        self.assertIn("不计入上面的 active 指标", report)


    def test_replay_reattaches_standalone_timestamp_lines(self):
        # M5 时期的导出让时间戳单独占一行、正文在下一行；重放时必须把两者接回去，
        # 否则 wall_time 全空、样本记录时长恒为 0（旧证据会被误读成"零跨度"）。
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.log"
            path.write_text(
                "[2026-11-10_20:00:00:000]\n"
                "S,1,1000,11000,10000,12,3\n"
                "[2026-11-10_20:00:01:000]\n"
                "S,2,26000,37000,11000,13,3\n",
                encoding="utf-8")
            session = load_log(path)
            self.assertEqual(len(session.samples), 2)
            self.assertEqual(session.metrics()["sample_span_s"], 1.0)
            self.assertEqual(session.samples[0].wall_time,
                             datetime(2026, 11, 10, 20, 0, 0))
            with tempfile.TemporaryDirectory() as out:
                raw_path, *_ = session.export(Path(out), "legacy")
                lines = raw_path.read_text(encoding="utf-8").splitlines()
        self.assertIn("[2026-11-10_20:00:00:000]S,1,1000,11000,10000,12,3", lines)
        self.assertNotIn("[2026-11-10_20:00:00:000]", lines)


class ReproduceReportTests(unittest.TestCase):
    """`--reproduce` 的契约：只读证据目录、数字对得上、缺件不崩。"""

    @staticmethod
    def make_run(directory: Path) -> Path:
        run_dir = directory / "run1"
        run_dir.mkdir()
        rows = [("1", 1000, 11000, 10000, 12), ("2", 2000, 13000, 11000, 13),
                ("3", 3000, 14500, 11500, 11)]
        with (run_dir / "initiator_COM3.log").open("w", encoding="utf-8") as handle:
            for seq, t1, t4, rtt, bproc in rows:
                handle.write(f"[2026-11-15_20:00:00:000]S,{seq},{t1},{t4},{rtt},{bproc},3\r")
            handle.write("[2026-11-15_20:00:00:010]# SUM n=3 sent=3 recv=3 to=0 dup=0 "
                         "ooo=0 bad=0 drop=0 rx_drop=0 send_err=0 cfm_err=0\r")
        (run_dir / "initiator_COM3.raw").write_bytes(b"\x00" * 96)
        (run_dir / "initiator_COM3.csv").write_text(
            "seq,t1_us,t4_us,rtt_us,bproc_us,wall_time\n"
            "1,1000,11000,10000,12,2026-11-15T20:00:00.000\n"
            "2,2000,13000,11000,13,2026-11-15T20:00:00.001\n"
            "3,3000,14500,11500,11,2026-11-15T20:00:00.002\n",
            encoding="utf-8")
        (run_dir / "run.json").write_text(json.dumps({
            "app_version": "M6.0.0-rc1",
            "source_identity": {"git_head": "0" * 40, "git_dirty": False},
            "experiment_spec": {"name": "unit", "mode": "measure"},
            "firmware_identity": {
                "initiator": {"path": "a.fwpkg", "sha256": "1" * 64, "status": "provided"},
                "responder": {"path": "b.fwpkg", "sha256": "2" * 64, "status": "provided"},
            },
            "analysis_samples": 3,
        }), encoding="utf-8")
        (run_dir / "initiator_COM3.json").write_text(json.dumps({
            "pc_exact_metrics": {"n": 3, "p50_us": 11000, "p90_us": 11500,
                                 "p99_us": 11500, "max_us": 11500,
                                 "to": 0, "dup": 0, "ooo": 0, "bad": 0, "drop": 0},
        }), encoding="utf-8")
        return run_dir

    def test_report_matches_recorded_numbers_and_passes_self_check(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = self.make_run(Path(directory))
            session = load_log(run_dir / "initiator_COM3.log")
            report = reproduce_markdown(session, run_dir,
                                        json.loads((run_dir / "run.json").read_text(encoding="utf-8")),
                                        run_dir / "initiator_COM3.log",
                                        {"n": 3, "p50_us": 11000, "p90_us": 11500,
                                         "p99_us": 11500, "max_us": 11500})
        self.assertIn("| 有效样本 | 3 | 3 | 一致 |", report)
        self.assertIn("自检：PASS", report)
        self.assertIn("M6.0.0-rc1", report)
        self.assertIn("a.fwpkg", report)

    def test_report_flags_mismatched_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = self.make_run(Path(directory))
            session = load_log(run_dir / "initiator_COM3.log")
            report = reproduce_markdown(session, run_dir, None,
                                        run_dir / "initiator_COM3.log",
                                        {"n": 3, "p50_us": 99999})
        self.assertIn("**不一致**", report)
        self.assertIn("记录缺失", report)  # 记录里没有 p90/p99/max

    def test_run_writes_outside_evidence_dir_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = self.make_run(root)
            before = sorted(path.name for path in run_dir.iterdir())
            out = root / "reports"
            self.assertEqual(reproduce_run(run_dir, out), 0)
            self.assertEqual(sorted(path.name for path in run_dir.iterdir()), before)
            report = out / f"{run_dir.name}-reproduce.md"
            self.assertTrue(report.exists())
            self.assertEqual(reproduce_run(run_dir, out), 5)

    def test_run_without_log_returns_4(self):
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory)
            self.assertEqual(reproduce_run(empty, empty / "out"), 4)


class PreflightTests(unittest.TestCase):
    """采样前自检：fail 只用于"现在开始一定白采"。"""

    @staticmethod
    def signature(**overrides) -> PlatformSignature:
        values = dict(role="initiator", link="sle-write_cmd", cfg="500", payload_b=40,
                      rate_hz=40, window=1, timeout_ms=50, interval=24,
                      selftest="PASS", measurement_ready=True)
        values.update(overrides)
        return PlatformSignature(**values)

    MATCHING = {"role": "initiator", "link": "sle-write_cmd", "cfg": "500",
                "payload": 40, "rate": 40, "window": 1, "timeout": 50, "intv": 24}

    def test_all_good_is_pass(self):
        items = preflight_items(self.signature(), self.MATCHING, "COM3", "COM4", True, True)
        self.assertEqual(format_preflight(items).split("；")[0], "采样前自检：PASS")

    def test_same_aux_port_is_fail(self):
        items = preflight_items(self.signature(), self.MATCHING, "COM3", "com3", True, True)
        aux = next(item for item in items if item["name"] == "辅助串口")
        self.assertEqual(aux["status"], "fail")
        self.assertIn("同一个口不能开两次", aux["detail"])
        self.assertTrue(format_preflight(items).startswith("采样前自检：FAIL"))

    def test_spec_mismatch_is_warn_and_names_the_field(self):
        items = preflight_items(self.signature(), {"cfg": "521", "rate": 25}, "COM3", "COM4", True, True)
        spec = next(item for item in items if item["name"] == "实验规格")
        self.assertEqual(spec["status"], "warn")
        self.assertIn("cfg=500（期望 521）", spec["detail"])
        self.assertIn("rate=40（期望 25）", spec["detail"])

    def test_unknown_signature_is_warn_not_fail(self):
        items = preflight_items(PlatformSignature(), self.MATCHING, "COM3", "", False, True)
        names = {item["name"]: item["status"] for item in items}
        self.assertEqual(names["平台签名"], "warn")
        self.assertEqual(names["实验规格"], "warn")
        self.assertEqual(names["固件登记"], "warn")
        self.assertFalse(any(item["status"] == "fail" for item in items))

    def test_missing_port_is_fail(self):
        items = preflight_items(self.signature(), self.MATCHING, "", "", True, True)
        self.assertEqual(items[0]["status"], "fail")


if __name__ == "__main__":
    unittest.main()
