# M3 · PC 实时分析、日志回放与报告导出

## 1. 目标

把 M1/M2 依赖 QCOM 窗口和人工整理的流程，收敛为同一套 PC 工具。PC 只显示、保存和分析，链路时间戳
仍全部来自 initiator 板端，USB 串口到达时间不参与 RTT。

## 2. 架构

```text
QCOM 历史日志 ─┐
               ├─ LineParser ─ Session ─ 精确统计 ─ Tkinter 图表
COM3 @115200 ──┘                         ├─ CSV 原始样本
                                         ├─ JSON 元数据/平台签名
                                         └─ Markdown 报告
```

解析与统计引擎不依赖 GUI 和 pyserial；因此历史回放与单元测试在纯 Python 环境即可运行。实时串口通过
惰性导入 pyserial 接入，缺少依赖时不会破坏日志回放。

## 3. 第一版功能

- QCOM 时间戳日志和纯固件 CSV 行统一解析；
- COM3 115200 实时数据源；
- 自动枚举系统全部串口，以“端口号 + 设备描述”下拉显示并支持手动刷新；
- 串口固定为115200/8N1/无流控，DTR/RTS明确关闭；
- 实时模式在 measurement-ready 前严格丢弃中途样本，防止无签名数据进入正式统计；
- 历史日志后台回放，UI 线程分批消费；
- RTT 最近1200点时间序列；
- 24桶直方图与经验 CDF；
- 精确 p50/p90/p99/p99.9/max/mean/jitter；
- 从第一条正式样本开始的采集时长，停止冻结，同串口继续时续计，清空/新会话才归零；
- timeout、duplicate、out-of-order、bad、drop；
- role、link、payload、rate、window、timeout、MTU、interval、selftest、ready 平台签名；
- 原始串口日志、CSV、JSON、Markdown 一次导出。

## 4. 统计口径

PC 对已收到的原始 `S` 行精确排序计算分位数。板端受 SRAM 限制，使用144桶对数直方图并在桶内插值，
所以 PC 精确值与板端 SUM 允许小幅不同。报告同时保存两套结果，不用其中一套冒充另一套。

`jitter` 与固件统一定义为：

```text
mean(abs(RTT[i] - RTT[i-1]))
```

它不是 RTT 总体标准差。

## 5. 验收计划

### A. 自动化

- 解析 QCOM 与纯 CSV；
- 平台签名提取；
- 精确分位数和 jitter；
- SUM 异常计数；
- CSV/JSON/Markdown 导出；
- M1/M2 已归档正式日志回放。

### B. 人工界面

- 打开 BLE Command、BLE Request 正式日志，图表与指标正常刷新；
- 大日志回放时界面不假死；
- 导出文件可重新核对样本数和平台签名；
- 缺少 pyserial 时给出明确安装指令。

### C. 双板实时

- 关闭 QCOM，避免 COM3 独占冲突；
- 由工具直接读取 COM3；
- 运行至少10分钟，实时样本数与板端 SUM 对齐；
- 中途停止并导出，CSV/JSON/Markdown 齐全；
- 保存截图和导出目录作为 M3 实验证据。

## 6. 最终验收结果

M3 已于2026-11-10完成验收：

- 8项自动化测试全部通过；
- BLE Command 43,702条与 BLE Request 34,486条既有正式日志均可回放；
- GUI 已完成串口枚举、严格准入、开始/停止、采集时长冻结和四件套导出复验；
- COM3 正式轮连续采集621.125秒，得到24,845个有效样本，序号1→24,845连续且唯一；
- 实际接收速率39.999988 samples/s，timeout/duplicate/out-of-order/bad/drop均为0；
- PC精确统计为 p50=15.157 ms、p90=17.803 ms、p99=20.044 ms、p99.9=25.354 ms、
  max=36.644 ms、mean=15.310 ms、jitter=3.473 ms；
- `.log/.csv/.json/.md` 四件套、验收摘要与 SHA-256 已归档到
  `artifacts/logs/M3/formal_live/`；
- Windows 安装版与 Portable 版已完成构建、安装/运行/卸载验收，并以
  [`v0.3.6`](https://github.com/Qjh213/nearlink-scope/releases/tag/v0.3.6) 正式发布。

PC精确分位数和板端对数直方图插值结果存在小幅差异，属于两种统计方法的预期差别；两端的 mean、jitter、
max 与异常计数一致。M3 的代码、实验、原始证据、问题日志和分发版本均已闭环，可以进入 M4。
