# LinkScope SLE被动监测报告

## 平台签名

`role=monitor | link=sle-passive-seek | cfg=unknown | payload=?B | rate=?Hz | win=? | timeout=?ms | mtu=? | intv=? | selftest=PASS | ready=no`

## 会话摘要

- 有效ADV报告：3489
- QCOM记录跨度：61.662 s
- 序列诊断：`{"gaps": 0, "duplicates": 0, "out_of_order": 0, "resets": 1}`
- 板端最近MON：`{"n": 3000, "drop": 0, "invalid_rssi": 0}`

| 地址ID | 报告数 | RSSI min / p50 / max (dBm) | 无效RSSI | 各event报告数 |
|---|---:|---:|---:|---|
| `867ec3df` | 3489 | -50 / -43 / -39 | 0 | 3: 1748, 11: 1741 |

## 口径

报告间隔只在同一地址ID、同一event类型内计算，且跨复位不连算。它是SDK seek回调的报告到达间隔，
不是空口广播周期；没有广播端序号时，序列诊断也不能解释为空口丢包率。
