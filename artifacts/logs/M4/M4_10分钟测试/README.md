# M4 SLE被动监测正式轮

时间：2026-11-11。COM3运行monitor，COM4运行固定SLE advertiser。LinkScope实时采集后导出
log/CSV/JSON/Markdown，COM4由QCOM独立保存。

## 数据边界

PC打开COM3后先收到旧会话 `seq=23555` 起的159条尾部，随后复位COM3。正式轮以日志中最后一次
`ADV,1`为起点，启动身份为：

```text
role=monitor
selftest=PASS
link=sle-passive-seek seek_intv=100 seek_win=100 filter_dup=1
set/start seek rc=0x0
seek enabled status=0x0
```

四类自动导出保留完整PC会话，因此显示41,739条、reset=1；下列正式指标只统计复位后的完整分段。

## 正式分段结果

- seq 1→41,580，41,580条连续唯一；
- 板端跨度686.297138秒（11分26.297秒），PC时间跨度686.267秒；
- 报告速率60.586 reports/s；
- 唯一地址ID `867ec3df`；
- event 3：20,897条；event 11：20,683条；
- RSSI min/p10/p50/p90/max：-81/-50/-44/-42/-36 dBm；
- gap/duplicate/out-of-order/reset：0/0/0/0；
- 最新 `# MON n=41400 drop=0 invalid_rssi=0`；
- Payload和data_status全程符合受控advertiser；
- COM4日志无Connected/Disconnected，心跳 `req=0 bad=0 rsp=0`。

本轮实际配置是 `filter_dup=1`。前置对照已经证明该SDK与当前广播内容下未观察到报告抑制；因此本轮仍可
验收长时间被动采集稳定性，但不得在文档中写成filter_dup=0。

## 文件SHA-256

```text
504bc6dd1525e6cdac47f14acac6cd53942f12c6ed472d72496151a35305ae93  linkscope-20261111-185626.csv
0bff8f278e89beea44b3e15a1b930e0eb706ccddad4e85ac42945edaad0b69a4  linkscope-20261111-185626.json
99856c937cb61b2a3fa6afac714babd25b132af7f06816cf21e2a2159d1d92c3  linkscope-20261111-185626.log
09e4f0a34ee558b94bda09f15d2a41fe4cbd933fca815614dfbdd034d0c51812  linkscope-20261111-185626.md
6a8ad9ed37ed9cf5d8edc9503eeec73f66e69d2892e4ce970eea1b0c6e8fb336  QCOM_LOG_server.txt
```
