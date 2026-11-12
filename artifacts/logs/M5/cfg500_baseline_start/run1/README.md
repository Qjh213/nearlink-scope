# M5 cfg500 开头基线 · run1

## 判定

**正式轮次通过。** 本轮满足cfg500平台签名、固定参数、采集时长和双端证据要求，可作为M5矩阵开头基线。

## 条件

- 日期：2026-11-12；
- 位置：近距基线，约0.5 m，常规环境；
- COM3：initiator，SLE Write Command，cfg500；
- COM4：responder；
- Payload 40 B、目标40 Hz、window=1、timeout=50 ms；
- 实际MTU=520、interval=24、latency=0；
- 两端串口115200/8N1、无流控、DTR/RTS关闭。

## 结果

- 正式采集：374.193 s，样本时间跨度374.124 s；
- PC精确样本14967条，seq=15…14981，序号连续、无PC侧缺口；
- p50 14.685 ms、p90 15.809 ms、p99 15.973 ms、p99.9 18.811 ms；
- max 48.881 ms、mean 14.749 ms、jitter 1.372 ms；
- 板端最近SUM：sent=14814、recv=14800、timeout=14、duplicate=14；
- out-of-order=0、bad=0、drop=0，loss_ppm=945；
- 运行中无断链、无日志乱码。

COM4在手动复位前已经处于旧的运行状态，因此辅助日志开头包含约18秒的高计数心跳；随后仅出现一次明确
`boot.`。以该启动边界为准，本轮启动后共750条心跳，全部`bad=0`、`req=rsp`、`lasterr=0x0`。这不会
影响COM3板端同一时钟计算的RTT，也不构成旧日志混入。

板端最新周期SUM统计到n=14800，PC最终落盘14967条，是导出发生在下一次SUM之前的正常快照差异。
