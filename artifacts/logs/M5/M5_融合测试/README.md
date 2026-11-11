# M5 串口工作台融合测试

## 结论

本轮通过，用于验收统一串口工作台、双端并发采集和联合导出；它是约 97 秒的功能冒烟，不替代 M5 正式
长时基线实验。

## 配置与证据

- 主分析端：COM3，`role=initiator`、`link=sle-write_cmd`、`cfg=500`；
- 参数：40 B、40 Hz、window=1、timeout=50 ms、MTU=520、interval=24；
- 辅助端：COM4，`role=responder`，115200/8N1、无流控、DTR/RTS关闭；
- 两端 SELFTEST 均为 PASS，连接与参数更新成功；
- 联合导出六个原始/结构化文件齐全，`run.json`正确记录`auxiliary_serials`。

## 核验结果

- PC精确样本：3874条，`seq=13…3886`，相邻序号连续，无PC侧日志缺口；
- 正式采集时长：`run.json`为96.772 s，样本时间跨度96.790 s；
- RTT：p50 14.704 ms、p90 15.799 ms、p99 18.647 ms、max 48.797 ms；
- 最新板端SUM：sent=3812、recv=3800、timeout=12、duplicate=12、bad=0、drop=0；
- responder心跳共201条，所有记录均`bad=0`、`req=rsp`、`lasterr=0x0`；
- 主、辅助原始日志均无UTF-8替换字符，无运行中断连记录。

板端最新SUM只统计到n=3800，而PC最终落盘3874条，是因为导出发生在下一条周期SUM输出之前，不是丢样。
启动阶段的`Flash Init Fail! ret = 0x80001341`和`init_sle_mac failed!!!`属于当前SDK既有启动打印；随后Flash、
射频、SLE、自检、连接和业务均成功，不能仅凭这两行判定本轮失败。

## 边界

本轮只实际使用一个辅助端COM4，证明了主分析端与辅助端融合流程；多个辅助端同时打开的能力已通过GUI冒烟，
仍需在具备第三块串口设备时补做多辅助端实机验收。
