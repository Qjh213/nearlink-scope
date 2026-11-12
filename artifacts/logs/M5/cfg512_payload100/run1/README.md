# M5 cfg512 Payload 100B · 正式 run1

## 判定

**正式通过。** 两端证据完整；100B/40Hz条件再次出现可重复的启动阶段timeout/late duplicate，稳定成功流连续。

## 结果

- 两端原始日志第一条均为本次`boot.`，各一次启动、一次连接、无断链；
- cfg512、Payload 100 B、40 Hz、window=1、timeout=50 ms、MTU=520、interval=24；
- 正式采集313.203 s，样本跨度313.139 s；
- PC成功样本12526条，seq=17…12542，成功流内部严格连续；
- p50 23.948 ms、p90 25.021 ms、p99 29.019 ms、p99.9 35.943 ms；
- max 41.963 ms、mean 24.144 ms、jitter 1.422 ms；
- 最新SUM sent=12417、recv=12400、timeout=16、duplicate=16；
- out-of-order=0、bad=0、drop=0；
- COM4共634条心跳，全部bad=0、req=rsp、lasterr=0。

冒烟为前15个请求超时，本轮为前16个请求超时，随后序号完全连续，证明启动阶段迟到可重复。稳定阶段p50
约23.95 ms，已接近40 Hz对应的25 ms发送周期；这不是协议极限结论，但说明当前固件/SDK/配置下100B、
40Hz已明显增加每事务占用时间。正式run2需继续验证中心分布和启动计数。
