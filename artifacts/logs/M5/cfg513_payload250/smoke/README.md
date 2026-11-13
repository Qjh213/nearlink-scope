# M5 cfg513 Payload 250B · 冒烟

**结论：功能链路通过，进入能力边界正式采集；不是零异常稳定性通过。**

- 签名：initiator、SLE Write Command、cfg513、REQ整帧250B、40Hz、window=1、timeout=50ms、MTU=520、interval=24；
- 采集81.342秒，成功样本1,894条，成功seq=1至1,919；
- 25个序号未形成有效RTT样本，板端同时记录timeout=25、duplicate=25；
- p50=41.951ms、p99=47.969ms、max=50.606ms、jitter=0.554ms；
- 成功样本速率约23.27Hz，计入timeout的事务推进速率约23.58Hz，明显低于目标40Hz；
- bad、drop、out-of-order均为0，两端各一次boot/连接且无断连；
- COM4共169条心跳，全部req=rsp、bad=0、lasterr=0，末条为1,911/1,911。

timeout与duplicate等量且响应端始终req=rsp，说明这些请求的响应迟到并超过50ms deadline，并非永久空口
丢包。250B典型RTT约42ms，`window=1`停等模型的完成速率上限约为1/RTT≈23.8Hz，因此40Hz目标无法
实现。个别已接收样本略高于50ms，是任务tick检查超时与收包回调的执行先后造成的边界现象，不改变25次
deadline miss事实。

正式两轮应保持所有固定条件，不提高timeout、不扩大window；非零deadline miss和实际速率本身就是cfg513
的实验结果。
