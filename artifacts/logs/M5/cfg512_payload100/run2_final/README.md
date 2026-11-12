# M5 cfg512 Payload 100B 最终正式 run2

**结论：正式通过。**

- 配置：SLE Write Command、cfg512、Payload 100B、40Hz、window=1、timeout=50ms、interval=24；
- 采集330.518秒，有效样本13,216条，seq=1至13,216严格连续；
- PC精确统计：p50=23.812ms、p90=24.464ms、p99=34.965ms、p99.9=45.464ms、max=46.403ms；
- timeout/duplicate/out-of-order/bad/drop全部为0，66条周期SUM的loss_ppm全部为0；
- 两端各一次boot、一次连接、无断连；
- COM4共667条心跳，全部req=rsp、bad=0、lasterr=0，末条为13,199/13,199。

相对run1，p50=-0.58%、mean=-0.92%、jitter=+2.60%，中心分布稳定；但p99=+25.53%、
p99.9=+33.63%。大于30ms样本占比由0.486%增至1.264%，大于40ms由0.011%增至0.628%。
这些长尾仍低于50ms阈值且未形成超时，应保留为两轮间的真实波动，不能只引用较好的一轮。
