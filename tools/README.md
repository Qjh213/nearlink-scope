# LinkScope M3 PC 工具

第一版采用 Python 标准库 Tkinter 绘图，只有实时串口需要 `pyserial`。板端时间戳仍是唯一测量时基；
PC 只负责接收、显示和导出，USB 串口到达时间不参与 RTT。

## 历史日志回放

无需安装依赖：

```powershell
python tools\nlscope.py --replay artifacts\logs\M2\ble_write_req\client_COM3.txt
```

同时导出原始串口日志、CSV、JSON 元数据和 Markdown 报告：

```powershell
python tools\nlscope.py --replay artifacts\logs\M2\ble_write_req\client_COM3.txt --export out\m3-demo
```

## 桌面界面

```powershell
python tools\nlscope.py
```

界面可打开历史 QCOM 日志，显示 RTT 时间序列、直方图、CDF、p50/p90/p99/p99.9/max、抖动及
timeout/duplicate/out-of-order/bad/drop。平台签名常驻顶部。

界面中的分位数标为“精确”，表示 PC 对原始 `S` 样本排序后计算；板端 `SUM` 为节省 SRAM 使用对数直方图
插值，因此两者可能有小幅差异。报告同时保存 PC 精确统计和最近一条板端 SUM。两端 `jitter` 统一使用
相邻 RTT 差分绝对值的平均值。

实时串口前安装：

```powershell
python -m pip install -r tools\requirements.txt
```

然后选择 COM3，点击“开始串口”。QCOM 等其他串口软件必须先关闭，避免同一串口被独占。

也可以无界面采集10秒并导出，便于验收和自动化：

```powershell
.venv\Scripts\python tools\nlscope.py --serial COM3 --duration 10 --export out\m3-live
```

## 测试

```powershell
python -m unittest discover -s tools -p "test_*.py"
```
