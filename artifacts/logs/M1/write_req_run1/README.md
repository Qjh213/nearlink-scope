# M1 Write Request 预实验（run1，不作为最终 A/B）

## 平台签名

- 日期：2026-11-09
- 模式：SLE Write Request
- 配置：40 Hz、40 B、window=1、timeout=50 ms、连接参数档位24
- initiator 固件：`EA098117BC5C89A4E8EA501DB2689990FE9D3CD4A7128342225C4C7C563068C0`
- responder 固件：`333D6F55364D2F6B7D41FBDB3ABBF243C66F48070811E9A93F2467DF3B2BAF23`

## 原始文件

```text
client_COM3.txt  4,148,934 bytes
SHA-256 F8B090A4C90BFFB0DCB8580D5D91672B5A7BADE74EBAA5F14DD30157A3186313

server_COM4.txt    101,811 bytes
SHA-256 963191E41D954E67D2AFB0E170ACC380E033439957871D0986C89AC4733ACA45
```

## 观测结果

```text
n=21000 p50=23638 p90=25579 p99=28109 p999=36057 max=41435 mean=23914 jit=1190
sent=21018 recv=21000 to=17 dup=17 bad=0 drop=0 bproc=11/536 loss_ppm=856
```

服务端末尾 `req=21383 bad=0 rsp=21383 lasterr=0x0`，全程无断链。17个 timeout/dup 是参数生效前
的预热代价，稳态没有继续增加。写确认窗口均值大多约16 ms。

## 为什么不作为最终结论

代码用会被 `sle_dbg()` 每5秒清零的 `g_cfm_n` 判断“只打印前3条确认”，导致每个窗口都再次打印3条，
本轮累计出现318条 `write cfm`，而 Command 没有对应输出。该额外 UART 扰动违反严格 A/B 的单变量原则。
原始数据保留用于定位和趋势参考；修复为独立、永不窗口清零的打印计数后重新采集 Request。
