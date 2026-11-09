# M1 Write Command 原始记录

## 平台签名

- 日期：2026-11-09
- responder：带外置天线，COM4
- initiator：无外置天线，COM3
- 模式：SLE Write Command
- 配置：40 Hz、40 B、window=1、timeout=50 ms、连接参数档位24
- initiator 固件 SHA-256：`69DF8B773663C59161D0FDED80D59FF97817A04261F1C85B7908215797C590E6`
- responder 固件 SHA-256：`333D6F55364D2F6B7D41FBDB3ABBF243C66F48070811E9A93F2467DF3B2BAF23`

## 原始文件

```text
client_COM3.txt  1,930,855 bytes
SHA-256 3E403163D27F9E88458B9E06C92A3399B6CAD72B9D3F2732A9FE01203DC3CEEB

server_COM4.txt    136,284 bytes
SHA-256 95BDE8082282E7010B7C2E75DEB069F35741E86EAE70E1C5FEF6B0C2FAF4DC76
```

## 验收摘要

- initiator 明确打印 `link=sle-write_cmd`；两端 selftest 均为 `pass=32 fail=0`；
- RSSI：-39 dBm；MTU：520；属性句柄：2；
- `20:28:28.206` 参数更新成功：`intv=24 slot`、latency=0、supervision=3200；
- 全程无 disconnect；服务端最终心跳 `req=29022 bad=0 rsp=29022 lasterr=0x0`；
- 最后一条完整汇总：

```text
n=28800 p50=14873 p90=16128 p99=18403 p999=27477 max=36160 mean=14830 jit=1471
sent=28812 recv=28800 to=12 dup=12 bad=0 drop=0 bproc=10/335 loss_ppm=416
```

`to=12 / dup=12` 在第一条 SUM 时已经存在，之后直到末尾不再增加，属于连接参数生效前的预热代价；
不能把累计 `loss_ppm=416` 描述成稳态丢包率。稳态新增超时、重复、坏帧和本地丢样均为0。

Write Command 没有协议层确认，因此周期日志始终为 `cfm_n=0`，与预期一致。
