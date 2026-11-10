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
- 历史日志后台回放，UI 线程分批消费；
- RTT 最近1200点时间序列；
- 24桶直方图与经验 CDF；
- 精确 p50/p90/p99/p99.9/max/mean/jitter；
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

## 6. 当前状态

2026-11-10：第一版数据引擎、桌面界面和导出功能完成；三项单元测试通过；BLE Command 正式日志
43,702条原始样本、BLE Request 正式日志34,486条原始样本均可回放；GUI 启动冒烟通过。实时串口尚待
安装 pyserial 后连接 COM3 完成双板验收。
