# Gate.io 全自动交易机器人

基于Binance版完整移植到Gate.io交易所。策略逻辑与Binance版完全一致。

## 品种
- BTC/USDT:USDT  100张 (0.01 BTC)
- ETH/USDT:USDT  50张  (0.5 ETH)
- SOL/USDT:USDT  10张  (10 SOL)
- BNB/USDT:USDT  1000张 (1 BNB)

## 策略
- 三周期EMA+ADX+DI方向判定 (1h/4h/1d)
- 4h EMA/Fib共振入场
- ATR自适应止损 (1.5x)
- 前低/前高结构止盈
- 峰值回撤50%移动止盈
- DeepSeek LLM入场二次确认
- DeepSeek LLM动态止盈管理

## 部署
```
# 主策略 (300秒轮询)
python3 btc_gate_auto.py
python3 eth_gate_auto.py
python3 sol_gate_auto.py
python3 bnb_gate_auto.py

# 移动止盈监控 (2秒轮询)
python3 trail_monitor_gate.py BTC
python3 trail_monitor_gate.py ETH
python3 trail_monitor_gate.py SOL
python3 trail_monitor_gate.py BNB

# 辅助 (cron)
*/1 * * * * python3 proc_guard_gate.py
*/1 * * * * python3 watchdog_gate.py
0 */4 * * * python3 market_enrich_gate.py
```

## 配置
编辑 `gate_config.py` 填入Gate.io API密钥和DeepSeek API密钥。
