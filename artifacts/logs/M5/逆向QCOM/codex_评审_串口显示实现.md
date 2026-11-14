# Codex 评审：串口原始显示实现（M5.0.8-rc4）

> 评审对象：`tools/serial_terminal.py`、`tools/nlscope.py`、`tools/test_nlscope.py` 的改动，以及新增的 `serial_reader.py` / `tests/test_splitter.py`。
> 参照基线：`LinkScope_QCOM_行为规格.md`（QCOM V1.6 实测行为）。
> 评审结论：**方向正确，测试全绿，但有 4 处需要收口，2 处会影响"与 QCOM 一致"这个目标。**

## 0. 先别动的部分（已确认正确）

- `pump_serial_lines` 改成 `read(256)` + 字节分帧，不再用 `readline`：`tools/serial_terminal.py:123`。
- 不再静默丢字节：超长未终止段整段吐出，`dropped_bytes` 保持 0：`tools/serial_terminal.py:65-71`。
- 半行不再无限等待：`idle_ms=300` 强制 flush：`tools/serial_terminal.py:73`。
- boot 门降级为"只标记会话边界"：辅助口 `poll()` 无条件显示（`tools/serial_terminal.py:386-391`）；主口先 `feed` 原始镜像、再 `admit` 门（`tools/nlscope.py:714-720`）；`begin_boot_session()` 不再清屏。
- 打开参数正确：115200 / 8N1 / 无软硬件流控 / `dtr=False` / `rts=False` / `reset_input_buffer()`（`serial_reader.py:120-134`、`serial_terminal.py:328-336`）。

以上行为不要回退。

## 1. 分帧语义和 QCOM 不一致（中）

**现状**：`SerialLineAccumulator.feed` 同时把 CR 和 LF 当行边界（`tools/serial_terminal.py:50`），并把孤立 CR 改写成 CRLF 再显示/落盘（`:56-61`）。

**QCOM 实测**：只认 CR（`\r`）。LF 是普通字节，留在行内；落盘保留原始 CR，再追加 CRLF，不做改写。

**后果**：
- 启动乱码 / 二进制段里若出现 `0x0A`，会多切一行（QCOM 不会）。
- 落盘文本不再是原始字节，`QCOM_LOG_*` 式的字节对照失效。

**期望**：
- 默认只把 CR 当边界，LF 作为普通字节保留。
- 需要容忍 LF 的场景用显式开关（默认关），不要混进默认路径。
- CR→CRLF 归一化只允许发生在 Tk 显示层（`_append_received` 内），落盘与测试用例必须拿到原始字节。

## 2. 交付了两套分帧器（中）

**现状**：`serial_reader.py` 的 `LineSplitter` 在生产代码里零引用（`grep` 只命中 `tests/test_splitter.py`）；主线用的是 `tools/serial_terminal.py` 的 `SerialLineAccumulator`。

**问题**：
- 两套默认值和边界规则不同：`serial_reader.py:41` 只认 CR，`serial_terminal.py:50` 认 CR/LF。
- `tests/test_splitter.py::test_08` 断言"仅 CR 为界"，与主线实现相反——测试通过但不描述线上行为。
- `serial_reader.LogSink.append` 直接写 `stamp + line`（`serial_reader.py:86-90`）：idle flush 出来的半行没有 CR 结尾，两条记录会粘在一起；QCOM 是"显示行 + CRLF"。

**期望**（二选一，不要并存）：
- A：删除 `serial_reader.py`，把它的验收用例迁到 `tools/test_nlscope.py` 打 `SerialLineAccumulator`；
- B：保留 `serial_reader.py` 但改成 `SerialLineAccumulator` 的薄封装，`pump_serial_lines` 也走它，全仓只留一套。

同时修 `LogSink.append`：每条记录写完补 `b"\r\n"`，且正文不做字节改写；高频日志不要每行 open/close 文件。

## 3. 时间戳粒度（低）

**现状**：`pump_serial_lines` 每个 chunk 取一次 `datetime.now()`，同 chunk 的多行共享时间戳（`tools/serial_terminal.py:129-131`）。

**QCOM**：逐行在显示前生成时间戳。

**期望**：按行生成（在 `emit` 前取时间），或者在文档里明确写"同一接收块共用到达时间"并接受这一差异——二选一，别默默不同。

## 4. CLI 路径没跟上（低）

**现状**：`nlscope.py --serial` 仍用 `handle.readline()`（`tools/nlscope.py:1012`），并丢弃 pre-ready 样本（`:1015-1017`）。

**后果**：GUI 与 CLI 对同一块板子给出不同行切分/不同样本集，排查问题时容易误判。

**期望**：CLI 改用 `pump_serial_lines` + 同一套分帧；要保留"仅 GUI 丢弃 pre-ready"就写进 help/日志。

## 5. 需要实测的风险：打开瞬态 DTR/RTS（待确认）

pyserial 打开端口时会先按默认置 DTR/RTS enable，随后才执行我们的 `dtr=False` / `rts=False`（`serial_reader.py:130-133`、`serial_terminal.py:328-336`）。QCOM 是在同一个 `SetCommState` 的 DCB 里直接 disable，没有这个瞬态。

如果板子带 DTR 自动复位电路，**打开端口这个动作本身就会复位板子**，恰好破坏"打开后从第一个字节开始看"的现场。

**期望**：实测一次（只开端口、不动板子，观察是否出现 `boot.`）。若会复位：
- 首选在文档与状态栏里明确"打开瞬间可能复位一次"；
- 若要彻底消除，需要绕过 pyserial 的打开序列（Win32 `CreateFile` + 自建 DCB 一次 `SetCommState`），成本较高，先记录结论再决定。

## 6. 验收门（改完必须满足）

- `python -m unittest discover -s tools -p "test_*.py"` 全绿（当前 30 项）。
- 新增用例至少覆盖：
  1. 落盘记录以 CRLF 结束、正文保留原始字节（含孤立 CR、无终止半行）；
  2. 同一 chunk 多行各自带时间戳（或明确共享时间戳的断言）；
  3. 只含 LF 的输入：默认不切行（QCOM 兼容），开关打开才切。
- 全仓 serial 读取路径 `grep -n "readline("` 为零命中（`tools/` 与根目录）。
- 不存在第二套分帧实现，或第二套只是薄封装且有注释说明。

## 7. 不要做

- 不要在接收路径加任何内容过滤（boot./ready/关键字）。
- 不要为了"看起来整齐"丢弃乱码、半行或二进制字节。
- 不要把 boot 门重新接回显示链。
- 不要引入新依赖。
