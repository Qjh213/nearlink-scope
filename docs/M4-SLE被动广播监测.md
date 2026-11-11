# M4 · SLE被动广播监测

## 1. 目标与边界

使用一块 BearPi-Pico H3863 作为只听不连的SLE监测探头，持续接收seek报告并交给LinkScope PC工具
聚合分析。监测模式不发起连接、配对、MTU交换或服务发现，因此不能测RTT，也不是空口抓包器。

当前SDK公开的 `sle_seek_result_info_t` 包含：`event_type`、地址、定向地址、RSSI、`data_status`、
`data_length` 和广播数据。M4只对这些公开字段及板端回调到达时间做可核验分析。

## 2. 第一阶段固件

- 新增 `role=monitor` 与 `link=sle-passive-seek`；
- seek回调第一行读取板端微秒时钟；
- 回调只复制固定快照到消息队列，不在SLE service线程格式化或打印；
- 任务上下文输出：

```text
ADV,seq,t_us,event,addr_id,rssi_dbm,data_status,data_len,payload_hex
```

- 地址默认输出FNV-1a稳定标识，不直接公开完整设备地址；
- RSSI原始值 `0x7F` 输出为127并单独累计 `invalid_rssi`；
- 每200条输出 `# MON n=... drop=... invalid_rssi=...`；
- 默认seek interval/window均为100，即12.5ms，重复过滤关闭。

## 3. PC工具计划

- 新增广播记录解析器与历史日志回放；
- 设备列表：稳定ID、首次/最近出现、报告数、最近RSSI；
- 单设备RSSI时间序列、到达间隔分布和活跃度；
- 导出原始日志、逐报告CSV、会话JSON和Markdown摘要；
- 任意设备没有广播序号时，不计算或宣称丢包率。

### 3.1 已完成的核心解析层

`nlscope_core.py` 已可严格解析 `ADV` 与 `# MON` 行，对地址ID、RSSI、状态、长度和十六进制
Payload做完整性校验，畸形串口行不会进入统计。记录按 `addr_id` 聚合，RSSI排除无效值127后给出
min/p10/p50/p90/max；报告间隔只在同一设备、同一event类型内统计，并正确处理32位微秒时钟回绕。

历史QCOM日志可能同时包含复位前的旧会话尾部和复位后的新会话。解析层会识别seq回到1的边界，跨复位
不计算报告间隔，避免生成数千秒的伪最大值；同时保留reset、gap、duplicate和out-of-order诊断计数。
这些计数只描述UART记录序列完整性，不等价于空口丢包率。

核心层现已接入GUI：monitor会话自动切换为RSSI时间序列、设备汇总和按event分组的报告间隔CDF；顶部
指标显示ADV数、设备数、RSSI分位数、序列诊断、无效RSSI和板端queue drop。M3 RTT页面保持原样。
原始日志、逐ADV CSV、包含聚合指标的JSON和带口径说明的Markdown四类导出均已通过真实日志回放，
导出的ADV CSV也可重新打开。当前单元测试12项全部通过。

## 4. 实验顺序

1. COM4运行已知SLE advertiser，COM3运行monitor，验证字段和持续seek；
2. 对照 `filter_duplicates=0/1` 的上报行为；
3. 固定位置运行至少10分钟，检查时间戳单调、设备聚合、有效RSSI和队列drop；
4. 归档固件身份、配置、两端日志、导出文件和SHA-256。

### 4.1 重复过滤对照结果

`filter_dup=0`完整会话51.903秒、3,134条、60.382 reports/s；`filter_dup=1`完整会话69.631秒、
4,260条、61.179 reports/s。开启后event 3/11仍持续且近似1:1，gap/dup/ooo、queue drop和
invalid RSSI均为0。因此当前平台未观察到重复过滤带来的报告抑制。由于两轮不是同时采集且无线环境不同，
不把约1.3%的速率差异解释为过滤效果；SDK的重复定义和实现细节仍属未验证。

距离、干扰、包长和速率矩阵留给M5。M4只证明被动监测链路成立、字段解释正确、PC端能够稳定保存和分析。

## 5. 验收标准

- 固件无任何连接API调用，运行中只保持seek；
- 10分钟正式轮无崩溃、无重启、消息队列drop为0；
- PC解析、回放、设备聚合和四类导出均有自动化测试；
- 受控设备的报告时间戳单调，RSSI与Payload可复核；
- 原始证据与问题处理记录齐全；
- M4文章和配图保存到 `E:\Starklink\文章\link\M4`。
