# M3补充：SLE实时入口兼容性冒烟

时间：2026-11-11 17:58:28–18:00:03。目的仅为验证LinkScope v0.3.6能实时接收、显示和导出
SLE Write Request数据，不与M3的BLE十分钟正式轮做性能比较。

## 平台签名

```text
role=initiator
link=sle-write_req
cfg=0
payload=40B
rate=40Hz
window=1
timeout=50ms
mtu=520
interval=24
latency=0
selftest=PASS
measurement-ready=yes
```

## 完整性核验

- 有效CSV样本：2,619；
- 样本记录时长：65.427466 s；
- CSV序号：17→2,635，全部唯一，范围内0缺口；
- 估算有效样本速率：约40.01 samples/s；
- bad/drop/out-of-order：0/0/0；
- PC四件套导出完整；
- COM4为SLE responder，服务端持续 `req=rsp`、`bad=0`。

## 启动窗口说明

链路在17:58:32.038完成MTU与属性发现并进入ready，但连接参数到17:58:32.802才更新为24 slot。
引擎在参数更新前已经开始发包，因此seq 1–16均发生50ms timeout，迟到响应随后计为16次duplicate。
PC严格准入后落盘的CSV从seq 17开始，后续2,619条连续、唯一，timeout/duplicate计数不再增长。

因此本轮证明“SLE实时采集、统计和四件套导出兼容”，同时暴露SLE initiator的ready门早于连接参数更新；
它不是零异常稳定性实验，也不用于重算M1/M2性能结论。

## 文件哈希

```text
7c825ddb3df4e6753302e2fe6f68d5d51b7cb955d9e49f7af703585c96079912  linkscope-20261111-180003.csv
46424bfef052800189823ba5da829808db6b1f00ee54ebf5b4cf18b70650b5fd  linkscope-20261111-180003.json
fefbb2c9b0f1f05ab328d390516d4da7e58b355ab85ca3469b13994a19ca54bb  linkscope-20261111-180003.log
3e4a5f8f1091f1ba6332ea7475139550d456a47058b25acba8b86938f48e82cf  linkscope-20261111-180003.md
c159645063c30054fa2d561d147a19030b53ddbb0b2d0a9762bfcb8e83008e16  QCOM_LOG_server.txt
```
