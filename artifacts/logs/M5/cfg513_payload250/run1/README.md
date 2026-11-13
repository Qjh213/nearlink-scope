# M5 cfg513 Payload 250B · 正式 run1

**结论：能力边界正式轮有效，结果与冒烟一致。**

- 配置：SLE Write Command、cfg513、REQ整帧250B、40Hz、window=1、timeout=50ms、MTU=520、interval=24；
- 采集240.549秒，成功样本5,549条，成功seq=1至5,654；
- 成功序列包含83处缺口、共缺105个seq，对应整轮约105次deadline miss；
- p50=41.955ms、p90=44.885ms、p99=47.948ms、p99.9=48.727ms、max=49.776ms；
- 成功样本速率约23.06Hz，事务推进速率约23.50Hz；
- 最新周期SUM在n=5,400时记录timeout=102、duplicate=100、loss_ppm=18,538；末尾149个成功样本之后
  没有下一条SUM，因此周期计数不是整轮最终计数；
- bad、drop、out-of-order均为0，两端各一次boot/连接且无断连；
- COM4共487条心跳，全部req=rsp、bad=0、lasterr=0，末条为5,646/5,646。

相对冒烟，p50约+0.01%、p99约-0.04%，事务推进速率由约23.58Hz变为23.50Hz，能力边界具有较好
重复性。按成功序号推算deadline miss比例约为105/(5,549+105)=1.86%。响应端完整应答、迟到duplicate
接近timeout，继续支持“50ms deadline miss而非永久空口丢包”的解释。

本次日志从干净`boot.`开始，证明重启程序后COM3采集恢复；新增的乱码boot容错分支本轮未被直接触发。
