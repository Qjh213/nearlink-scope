# M5 cfg511 Payload 12B · 冒烟

## 判定

**冒烟通过，可进入两轮180秒正式采集。**

## 签名与时长

- initiator / SLE Write Command / cfg511；
- Payload 12 B、40 Hz、window=1、timeout=50 ms；
- MTU=520、interval=24、latency=0、自检PASS、ready=yes；
- 正式采集67.684 s，样本时间跨度67.692 s。

## 结果

- 成功样本2695条，seq=1…2697；
- 唯一序号跳跃为10→13，与板端2次timeout和2次late duplicate一致，不是PC串口日志丢失；
- p50 11.786 ms、p90 12.683 ms、p99 30.889 ms、max 57.555 ms；
- out-of-order=0、bad=0、drop=0；
- 两端各连接一次、无断链、无UTF-8替换字符；
- COM4本轮boot后142条心跳全部bad=0、req=rsp、lasterr=0。

p99和max较cfg500开头基线偏高，但单次67秒冒烟不用于得出Payload性能结论；保留该现象，在两轮正式采集后
再比较分位数、timeout和环境一致性。
