# 四品种自动交易 - 实盘部署规格

## 品种配置

| 品种 | 文件 | 杠杆 | 仓位 | 保证金 | 轮询 |
|------|------|------|------|--------|------|
| BTC | btc_bnb_auto.py | 20x | 0.005 | $15 | 300s |
| BNB | bnb_auto.py | 10x | 0.6 | ~$35 | 300s |
| SOL | sol_bnb_auto.py | 10x | 4 | $10 | 300s |
| ETH | eth_bnb_auto.py | 10x | 0.15 | $10 | 300s |

## 策略框架
- 方向：1h/4h/1d EMA5/10排列 + 4h ADX/DI投票
- 入场：4h EMA/斐波共振位限价单
- 止损：入场价 ± 1.5×ATR(4h)
- 止盈：前30根4h K线极值
- 过滤：量>0.8×MA20 + RSI(1h) 25~75 + K线实体>振幅30%
- 审核：DeepSeek LLM二审

## Crontab 运维任务
```
* * * * * python3 /root/zidongjiaoyi/watchdog.py >> /root/zidongjiaoyi/watchdog.log 2>&1
* * * * * python3 /root/zidongjiaoyi/proc_guard.py
0 */4 * * * python3 /root/zidongjiaoyi/market_enrich.py
```

## 一键恢复
```bash
cd /root/zidongjiaoyi && git pull origin master && \
pkill -f "bnb_auto.py\|btc_bnb_auto.py\|sol_bnb_auto.py\|eth_bnb_auto.py" 2>/dev/null; sleep 2 && \
> bnb_bn.log; > bnb_bn_trades.txt; > btc_bn.log; > btc_bn_trades.txt; \
> sol_bn.log; > sol_bn_trades.txt; > eth_bn.log; > eth_bn_trades.txt; \
echo '{"last_signal": null}' > bnb_bn_state.json; echo '{"last_signal": null}' > btc_bn_state.json; \
echo '{"last_signal": null}' > sol_bn_state.json; echo '{"last_signal": null}' > eth_bn_state.json; \
nohup python3 -u btc_bnb_auto.py >> btc_bn.log 2>&1 & \
nohup python3 -u bnb_auto.py >> bnb_bn.log 2>&1 & \
nohup python3 -u sol_bnb_auto.py >> sol_bn.log 2>&1 & \
nohup python3 -u eth_bnb_auto.py >> eth_bn.log 2>&1 & \
sleep 5 && ps aux | grep "auto\.py" | grep python | grep -v grep
```
