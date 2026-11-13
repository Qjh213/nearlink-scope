# M5 cfg513 Payload 250B · 正式 run2

**结论：第二轮有效，再次确认250B停等吞吐边界，但deadline miss比例存在轮间波动。**

- 配置：SLE Write Command、cfg513、REQ整帧250B、40Hz、window=1、timeout=50ms、MTU=520、interval=24；
- LinkScope版本：`M5.0.2-dev`；采集240.544秒，日志样本跨度240.558秒；
- 成功样本5,663条，成功seq=1至5,692，共缺29个seq，对应整轮约29次deadline miss；
- p50=41.949ms、p90=42.053ms、p99=47.739ms、p99.9=48.022ms、max=48.270ms；
- 成功样本速率约23.54Hz，事务推进速率约23.66Hz；
- 最新周期SUM在n=5,600时记录timeout=28、duplicate=27、loss_ppm=4,975；其后又出现一个序号缺口，
  因此整轮最终deadline miss按原始成功序号推算为29次，约29/5,692=0.51%；
- bad、drop、out-of-order均为0，两端各一次boot、一次连接且无断连；
- COM4共487条心跳，全部req=rsp、bad=0、lasterr=0，末条为5,688/5,688。

timeout与迟到duplicate数量接近、响应端请求与响应始终相等，继续支持“响应越过50ms deadline后才到达”，
而不是永久空口丢失。run2不能证明250B配置已经满足40Hz：实际事务推进仍约23.66Hz，接近约42ms典型
RTT对应的window=1停等上限。
