#!/usr/bin/env python3
"""实验向导：把"这一轮该按什么顺序做、现在卡在哪、这轮能不能用"变成可读状态。

只做判断，不碰串口、不碰 Tk。界面每 100 ms 调一次 snapshot() 刷新显示；
测试可以直接喂事件，不需要硬件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

PASS, WARN, FAIL, TODO = "pass", "warn", "fail", "todo"


@dataclass
class Step:
    key: str
    title: str
    hint: str
    status: str = TODO
    detail: str = ""


def default_steps(target_seconds: float) -> list[Step]:
    return [
        Step("spec", "选预期实验", "在下拉框里选本次 cfg；导出时会把条件写进证据"),
        Step("ports", "开双串口", "COM3 分析、COM4 只记原始字节；同一个口不能开两次"),
        Step("boot", "复位 COM4 再复位 COM3", "等两块板各自打印 boot.，门只在“开始串口”后生效"),
        Step("signature", "等启动签名", "role=… cfg=… payload=… 出现后条件才算确认"),
        Step("ready", "等 ready=yes", "协调参数协商完成，之前不计入统计"),
        Step("samples", "开始出样本", "主窗口 n 开始增长，此刻起计时"),
        Step("duration", f"跑满 {target_seconds:g} s",
             "到点后先停止再导出；中间不要复位，复位会让这一轮作废"),
        Step("export", "联合导出 + 写 README", "导出弹窗会带自检结论，WARN 要写进 README"),
    ]


@dataclass
class ExperimentWizard:
    target_seconds: float = 180.0
    spec_name: str = ""
    steps: list[Step] = field(default_factory=list)
    started_at: Optional[float] = None
    last_elapsed: float = 0.0
    samples: int = 0
    gaps: int = 0
    restarts: int = 0
    parse_failures: int = 0
    exported: bool = False

    def __post_init__(self) -> None:
        if not self.steps:
            self.steps = default_steps(self.target_seconds)

    # ---- 事件 -------------------------------------------------------------
    def _find(self, key: str) -> Step:
        for step in self.steps:
            if step.key == key:
                return step
        raise KeyError(key)

    def _mark(self, key: str, status: str, detail: str = "") -> None:
        step = self._find(key)
        step.status = status
        step.detail = detail

    def see_spec(self, name: str) -> None:
        self.spec_name = name
        if name:
            self._mark("spec", PASS, name)
        else:
            self._mark("spec", WARN, "没选规格：导出里不会有实验条件")

    def see_ports(self, main: str, aux: str) -> None:
        if not main:
            self._mark("ports", FAIL, "没有选择分析串口")
        elif aux and aux.upper() == main.upper():
            self._mark("ports", FAIL, f"辅助串口与主串口都是 {main}")
        elif aux:
            self._mark("ports", PASS, f"{main} + {aux}")
        else:
            self._mark("ports", WARN, f"只有 {main}：responder 侧没有原始字节")

    def see_boot(self) -> None:
        self._mark("boot", PASS, "已收到 boot.")

    def see_signature(self, role: str, ready: Optional[bool]) -> None:
        if role in ("", "unknown"):
            self._mark("signature", TODO, "还没收到签名行")
            return
        self._mark("signature", PASS, role)
        if ready is True:
            self._mark("ready", PASS)
        elif ready is False:
            self._mark("ready", WARN, "签名已到但 ready=no：还在协商")

    def see_samples(self, samples: int) -> None:
        self.samples = samples
        if samples > 0:
            self._mark("samples", PASS, f"{samples} 条")

    def see_health(self, gaps: int = 0, restarts: int = 0, parse_failures: int = 0) -> None:
        self.gaps, self.restarts, self.parse_failures = gaps, restarts, parse_failures

    def tick(self, elapsed: float) -> None:
        self.last_elapsed = max(0.0, elapsed)
        if self.samples == 0:
            self._mark("duration", TODO, "还没出样本，不计时")
        elif self.last_elapsed >= self.target_seconds:
            self._mark("duration", PASS, f"{self.last_elapsed:.1f} s")
        else:
            self._mark("duration", TODO, f"{self.last_elapsed:.1f} / {self.target_seconds:g} s")

    def see_export(self, status: str) -> None:
        self.exported = True
        self._mark("export", PASS if status == "pass" else WARN, f"自检 {status}")

    # ---- 判断 -------------------------------------------------------------
    def verdict(self) -> tuple[str, str]:
        """返回 (状态, 一句话)。状态 ∈ {idle, blocked, running, usable, void}。"""
        failed = [step for step in self.steps if step.status == FAIL]
        if failed:
            return "blocked", "先处理：" + "；".join(step.detail or step.title for step in failed)
        if self.restarts:
            return "void", f"这一轮中途复位了 {self.restarts} 次，样本跨多次上电，不能当一轮用"
        if self.samples and self.parse_failures >= max(10, self.samples // 100):
            return "void", f"坏行 {self.parse_failures} 条（样本 {self.samples}），解析失败过多"
        duration = self._find("duration")
        if duration.status == PASS:
            if self.exported:
                return "usable", "已导出；README 里补上硬件位置与结论即可归档"
            return "usable", "时长已跑满，可以停止并联合导出"
        if self.samples:
            return "running", f"采集中 {self.last_elapsed:.1f} / {self.target_seconds:g} s，n={self.samples}"
        return "idle", "按顺序完成左侧步骤；出现样本后开始计时"

    def snapshot(self) -> dict:
        status, message = self.verdict()
        return {
            "status": status,
            "message": message,
            "spec": self.spec_name,
            "elapsed": self.last_elapsed,
            "target_seconds": self.target_seconds,
            "samples": self.samples,
            "items": [{"key": step.key, "title": step.title, "hint": step.hint,
                       "status": step.status, "detail": step.detail} for step in self.steps],
        }

    def format_text(self) -> str:
        marks = {PASS: "[x]", WARN: "[!]", FAIL: "[×]", TODO: "[ ]"}
        lines = [f"{marks[step.status]} {step.title}"
                 + (f" —— {step.detail}" if step.detail else "")
                 for step in self.steps]
        status, message = self.verdict()
        return "\n".join(lines) + f"\n\n{status.upper()}：{message}"
