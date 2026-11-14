# 交给 Codex 的交接包：LinkScope 串口接收模块

> 用法：把本文件 + `LinkScope_QCOM_行为规格.md` 一起交给 Codex，第一段「任务」整段粘贴给它即可。

---

## 一、任务（可直接粘贴给 Codex）

你在为 NearLink LinkScope 实现一个串口接收模块，行为必须与 QCOM 1.6 一致，并额外满足"半行不隐藏"。

**权威依据**：同目录的 `LinkScope_QCOM_行为规格.md`。该文档由二进制逆向得出，是唯一事实来源。**文档没写的，按文档隐含的行为推导；文档写了的，不许"优化"。**

**交付物**（Python 3.10+，只允许标准库 + pyserial）：

1. `serial_reader.py`
   - `class LineSplitter`：纯逻辑、无 IO、可单测
     - `feed(data: bytes) -> list[bytes]`：以 `\r` 为唯一行界，返回完整行（含行尾 `\r`）
     - `flush_idle(now: float) -> bytes | None`：距上次收到字节超过 `idle_ms` 时，把滞留缓冲作为一行返回
     - `pending: bytes` 属性，暴露未成行的残留
     - 行缓冲上限 `max_pending`（默认 64 KB），超出时**整段吐出并清空**，不静默丢弃
   - `class TimestampFormatter`：`stamp(now: datetime | None = None) -> str`，格式 `[YYYY-MM-DD_HH:MM:SS:mmm]`
   - `class SerialReader(threading.Thread)`：打开串口、读字节、喂 `LineSplitter`、把完整行放进 `queue.Queue`；不接触 UI
   - `class LogSink`：可选，把显示行原文追加到文件（含时间戳、含行尾 `\r`）
2. `tests/test_splitter.py`：覆盖下面「验收用例」全部 8 条
3. `FakeSerial`（放 tests 里即可）：可注入字节序列、可模拟超时返回空、可模拟 `SerialException`

**不要现在写 Tk 界面**。先交付可测的纯逻辑与读线程，界面由下一轮做。

---

## 二、硬性禁止清单

以下任何一条出现，视为不合格：

1. 禁止 `readline()`、禁止 `for line in ser`、禁止按 `\n` 分行。**行界只有 `\r`。**
2. 禁止在接收路径上过滤内容（不判 `boot.`/`ready`/空行/关键字，不做协议解析）。
3. 禁止在读线程里调用 Tk/UI 代码（只允许 `queue.put`）。
4. 禁止用 `asyncio` / `select` 重写架构；就用一条阻塞读线程。
5. 禁止打开串口后不显式设置 `dtr=False`、`rts=False`。
6. 禁止打开后不清输入缓冲（必须 `reset_input_buffer()`）。
7. 禁止静默丢数据；任何丢弃都必须可计数、可观测（计数器或日志）。
8. 禁止引入 pyserial 之外的新依赖。

---

## 三、接口契约

```python
class LineSplitter:
    def __init__(self, max_pending: int = 64 * 1024, idle_ms: int = 300) -> None: ...
    def feed(self, data: bytes) -> list[bytes]: ...
    def flush_idle(self, now: float | None = None) -> bytes | None: ...
    @property
    def pending(self) -> bytes: ...

class TimestampFormatter:
    def stamp(self, now: datetime | None = None) -> str: ...
    # 例： "[2026-11-06_19:52:54:767]"

class SerialReader(threading.Thread):
    def __init__(self, port: str, out: queue.Queue[bytes],
                 stop: threading.Event, *, baudrate: int = 115200,
                 idle_ms: int = 300, max_pending: int = 64 * 1024) -> None: ...
    # run(): 打开 -> reset_input_buffer -> dtr/rts=False -> 循环 read(256)
    #        -> splitter.feed() -> 逐行 out.put() -> 每轮调 splitter.flush_idle()
    # 打开参数固定：8N1、无流控、timeout=0.05、独占
    # 出错：out.put(b"") 之外，通过 errors 回调或 out 放哨兵，由调用方决定
```

---

## 四、验收用例（必须全绿）

| # | 输入 | 期望 |
|---|---|---|
| 1 | `feed(b"hello\r")` | 返回 `[b"hello\r"]`，pending 为空 |
| 2 | `feed(b"a\rb\r")` | 返回 `[b"a\r", b"b\r"]`，pending 为空 |
| 3 | `feed(b"boot.")`，不调用 flush | 返回 `[]`，pending 为 `b"boot."` |
| 4 | 同上，300 ms 后 `flush_idle()` | 返回 `b"boot."`（不带 `\r`），pending 清空 |
| 5 | `feed(b"\x00\xff\xfe\r")` | 返回 1 行，不抛异常，字节原样保留 |
| 6 | `feed(b"x" * 5000)`（无 CR） | 不静默丢：pending 或返回行里累计 5000 字节，计数器为 0 |
| 7 | `feed(b"x" * 70000)`（超过 64 KB） | 返回的整段累计 70000 字节，无字节丢失（允许拆多行） |
| 8 | `feed(b"\r\n\r\n")` | 按 `\r` 切出 2 行：`b"\r"` 与 `b"\r"`；`\n` 留在下一行开头（pending 为 `b"\n\r\n"` 之类的自然结果，不得吞掉） |

另加 3 条集成测试（不接真串口）：

| # | 场景 | 期望 |
|---|---|---|
| 9 | `FakeSerial` 依次返回 `b"a"`、`b"\rb"`、`serial.SerialTimeoutException` | 收到 `b"a\r"`，pending 为 `b"b"`，线程不退出 |
| 10 | 连续 10 次 `start()/stop()+join()` | 无句柄泄漏、无异常 |
| 11 | 读线程运行中 `stop.set()` | 2 s 内退出，未成行的数据可被 flush 出来 |

---

## 五、完成定义

- `python -m pytest -q` 全绿；
- `python -c "import serial_reader"` 无副作用；
- 代码里 `grep -nE "readline|\| *for line in|asyncio|root\.|tk\."` 无命中（UI 相关）；
- `serial_reader.py` 顶部 10 行内写清"行界 = \r、不做内容过滤、DTR/RTS 默认关"三条约束；
- 提交时说明：哪些行为与 QCOM 完全一致、哪些是"半行 flush"增强。

---

## 六、给 Codex 的验收自检命令

```powershell
python -m pytest -q
python -c "from serial_reader import LineSplitter as L; s=L(); print(s.feed(b'a\rb\r'), s.pending)"
```

---

## 七、不要一并交给它东西

- `_rea\qcom\ghidra\decomp*.txt`（反编译转储）：噪声大、且属于对成品的衍生复制，没必要；
- `reaq.js` / 调用脚本：与实现无关；
- 原始 exe 与日志文件：日志里含你的板卡信息，Codex 用不上。

只给两份 md：本文件 + `LinkScope_QCOM_行为规格.md`。
