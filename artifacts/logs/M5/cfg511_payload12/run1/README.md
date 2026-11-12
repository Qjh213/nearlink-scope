# M5 cfg511 Payload 12B · 正式 run1

## 判定

**正式通过。** 两端原始日志均严格从各自本次`boot.`开始，配置、时长、样本和双端证据完整。

## 条件

- 日期：2026-11-12；
- 近距约0.5 m，板卡位置与方向保持固定；
- COM3 initiator / COM4 responder；
- SLE Write Command、cfg511、Payload 12 B、40 Hz；
- window=1、timeout=50 ms、MTU=520、interval=24、latency=0；
- 两端自检和平台签名完整，ready=yes。

## 结果

- 正式采集233.903 s，样本时间跨度233.840 s；
- PC精确样本9342条，seq=1…9342，严格连续；
- p50 12.219 ms、p90 13.059 ms、p99 14.001 ms、p99.9 49.908 ms；
- max 50.033 ms、mean 12.211 ms、jitter 1.222 ms；
- 最新板端SUM：sent=9200、recv=9200、timeout=0、duplicate=0；
- out-of-order=0、bad=0、drop=0、loss_ppm=0；
- COM3和COM4各一个boot、一次连接、无断链、无乱码；
- COM4共474条心跳，全部bad=0、req=rsp、lasterr=0。

最新周期SUM统计到9200条，而PC在停止前最终落盘9342条，是SUM输出周期与停止时刻之间的正常快照差异。
