# M5 cfg511 Payload 12B · 正式 run2

## 判定

**正式通过。** 0.5 m近距条件下第二次独立启动与采集证据完整。

## 结果

- COM3与COM4原始日志第一条均为各自本次`boot.`；
- cfg511、Payload 12 B、40 Hz、MTU=520、interval=24、ready=yes；
- 正式采集376.945 s，样本跨度376.870 s；
- PC精确样本15065条，seq=1…15065，严格连续；
- p50 12.253 ms、p90 13.066 ms、p99 14.099 ms、p99.9 24.219 ms；
- max 50.015 ms、mean 12.182 ms、jitter 1.360 ms；
- 最新SUM sent=recv=15000，timeout/duplicate/ooo/bad/drop均为0；
- 两端各一次启动、一次连接、无断链、无乱码；
- COM4共760条心跳，全部bad=0、req=rsp、lasterr=0。

最新周期SUM与PC最终样本数相差65条，是停止发生在下一次周期SUM之前的正常快照差异。
