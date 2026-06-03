# BTC 全自动交易系统 (zidongjiaoyi)

币安合约 BTC/USDT 永续，20x 逐仓，0.005 BTC/笔。

## 策略核心

- **方向判定**: 1h/4h/日线三周期 EMA5/EMA10 排列 + 4h ADX/DI 方向强度
- **入场**: 4h EMA + 斐波那契共振区限价挂单
- **止损**: 1.5 × 4h ATR（波动率自适应）
- **止盈**: 前 30 根 4h K 线极值（市场结构位）
- **轮询**: 每 5 分钟（对齐 5m K 线收盘）
- **反转**: 方向翻转自动平仓反手

## 文件

| 文件 | 说明 |
|------|------|
| `btc_bnb_auto.py` | 币安版主程序（当前运行版本） |
| `btc_auto_dynamic.py` | Gate 版（备用） |
| `btc_bn.log` | 运行日志 |
| `btc_bn_trades.txt` | 交易日志（中文，含完整分析依据） |

## 运行

```bash
python3 btc_bnb_auto.py
```
