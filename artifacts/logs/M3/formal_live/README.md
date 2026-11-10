# M3 PC 实时采集正式轮

时间：2026-11-10 21:51:43–22:02:04。数据源：COM3，115200/8N1、无流控、DTR/RTS off。

## 平台签名

```text
role=initiator
link=ble-write_req
cfg=0
payload=40B
rate=40Hz
window=1
timeout=50ms
mtu=247
interval=6
latency=0
selftest=PASS
measurement-ready=yes
```

## 完整性核验

- 样本记录时长：621.125 s（10分21.125秒）；
- CSV 有效样本：24,845；
- 序号：1→24,845，全部唯一，0缺口；
- 实际接收速率：39.999988 samples/s；
- 原始日志 `S` 行：24,971；
- 严格准入排除复位前中途样本：126；
- 板端最近完整 SUM：n=24,800，CSV 多出的45条是 SUM 后正常尾部；
- 全程1次连接成功，无 Disconnected；
- timeout/duplicate/out-of-order/bad/drop：0/0/0/0/0；
- RTT >30 ms：8条；RTT >50 ms：0条。

## PC 原始样本精确统计

```text
p50=15.157 ms  p90=17.803 ms  p99=20.044 ms  p99.9=25.354 ms
max=36.644 ms  mean=15.310 ms  jitter=3.473 ms
```

## 板端最近完整 SUM

```text
n=24800  p50=15.136 ms  p90=17.902 ms  p99=19.920 ms  p99.9=26.318 ms
max=36.644 ms  mean=15.310 ms  jitter=3.473 ms  loss_ppm=0
```

PC 分位数为原始样本精确排序，板端分位数为对数直方图插值，因此允许小幅差异。两端 mean、jitter 和
max 一致，异常计数一致。

## 文件哈希

```text
c290ae17da784d0e6ce60bae28c5101eb915dc03fcc70dee197ef411debaf970  linkscope-20261110-220311.csv
bb90cf1388438a9d509e65921db05de9fe187f3c08acf4a6def1051caf351657  linkscope-20261110-220311.json
e2264486b655d29cd9b80dac2d8607018b32c82019d96571a1c081574807b1f9  linkscope-20261110-220311.log
84d4f938718994f96bb4732efe5acaf97e050b8b762f92f802724e84af9bcdd3  linkscope-20261110-220311.md
```

结论：M3 第一轮 PC 实时采集满足10分钟、完整平台签名、连续序号、40 Hz、零异常计数和四件套导出的
验收要求。
