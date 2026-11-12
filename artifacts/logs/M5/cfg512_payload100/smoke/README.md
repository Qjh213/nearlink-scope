# M5 cfg512 Payload 100B · 冒烟

## 判定

**冒烟通过，可进入两轮正式采集。** 100B配置和双端链路稳定，但启动阶段出现15次timeout/late duplicate，
正式轮次需继续观察其是否重复。

## 签名与时长

- COM3/COM4原始日志第一条均为本次`boot.`；
- initiator / SLE Write Command / cfg512；
- Payload 100 B、40 Hz、window=1、timeout=50 ms；
- MTU=520、interval=24、latency=0、自检PASS、ready=yes；
- 正式采集107.301 s，样本跨度107.333 s。

## 结果

- PC成功样本4295条，seq=16…4310，成功流内部严格连续；
- p50 23.429 ms、p90 24.467 ms、p99 26.929 ms、max 45.685 ms；
- 最新SUM sent=4216、recv=4200、timeout=15、duplicate=15；
- out-of-order=0、bad=0、drop=0；
- 两端各一次启动、一次连接、无断链、无乱码；
- COM4共223条心跳，全部bad=0、req=rsp、lasterr=0。

前15个请求没有形成正常成功序列，且timeout与late duplicate数量相等；seq从16开始后没有缺口。这更像连接
完成后业务启动阶段的短暂超时/迟到响应，不是PC日志丢失。正式run1/run2必须保留并比较启动阶段计数。
