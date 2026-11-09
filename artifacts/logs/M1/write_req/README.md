# M1 Write Request 最终记录

## 平台签名

- 日期：2026-11-09
- responder：带外置天线，COM4
- initiator：无外置天线，COM3
- 模式：SLE Write Request（P38 修复版）
- 配置：40 Hz、40 B、window=1、timeout=50 ms、连接参数档位24
- initiator 固件 SHA-256：`ACB868951FEC55F2A54A34D3E088D1560A405DD8A24BA5A507B7ECE3A0FF6149`
- responder 固件 SHA-256：`333D6F55364D2F6B7D41FBDB3ABBF243C66F48070811E9A93F2467DF3B2BAF23`

## 原始文件

```text
client_COM3.txt  6,162,768 bytes
SHA-256 5CF22880AD89BC24F00A1B6CAFF9B029A726A855B5F485EADE5F8B189E752F0A

server_COM4.txt    150,434 bytes
SHA-256 503186A306E2530999E2EEBF6B6DD603BEA8043033C6756EB40618D2F7AC7B5D
```

## 验收摘要

- initiator 明确打印 `link=sle-write_req`，全程恰好3条 `write cfm`，P38 修复生效；
- 两端 selftest 均通过；RSSI -36 dBm；MTU 520；
- 参数更新成功：`intv=24`、latency=0、supervision=3200；
- 全程无 disconnect；服务端末尾 `req=32171 bad=0 rsp=32171 lasterr=0x0`；
- 最后一条完整汇总：

```text
n=31400 p50=23362 p90=24535 p99=26481 p999=30689 max=38471 mean=23641 jit=1263
sent=31416 recv=31400 to=15 dup=15 bad=0 drop=0 bproc=11/288 loss_ppm=509
```

15个 timeout/dup 从第一条 SUM 到末尾不再增长，属于参数生效前的预热代价；稳态新增异常为0。
排除第一个含预热的确认窗口后，共156个完整窗口、31,200次确认，按窗口样本数加权平均
CFM 时延为15,872.8 µs，窗口最大值的全局最大为30,696 µs。
