# BTC + HYPE 全自动交易系统 (zidongjiaoyi)

币安合约，统一框架：三周期EMA+ADX+DI判方向，4h EMA/Fib共振入场，ATR自适应止损。

## 品种

| 脚本 | 品种 | 杠杆 | 仓位 | 状态 |
|------|------|------|------|------|
| `btc_bnb_auto.py` | BTC/USDT | 20x | 0.005 BTC | ✅ 运行中 |
| `hype_bnb_auto.py` | HYPE/USDT | 10x | 3.0 HYPE | ✅ 运行中 |
| `btc_auto_dynamic.py` | BTC/USDT | Gate备用 | - | 待命 |

## 策略核心（所有品种统一）

- **方向判定**: 1h/4h/日线三周期 EMA5/EMA10 排列 + 4h ADX/DI 方向强度
- **入场**: 4h EMA + 斐波那契共振区限价挂单
- **止损**: 1.5 × 4h ATR（波动率自适应）
- **止盈**: 前 30 根 4h K 线极值（市场结构位）
- **轮询**: 每 5 分钟
- **反转**: 方向翻转自动平仓反手

## 日志

| 文件 | 内容 |
|------|------|
| `btc_bn.log` / `hype_bn.log` | 运行日志 |
| `btc_bn_trades.txt` / `hype_bn_trades.txt` | 交易日志（中文，含完整分析依据） |

## 运行

```bash
python3 btc_bnb_auto.py &
python3 hype_bnb_auto.py &
```
