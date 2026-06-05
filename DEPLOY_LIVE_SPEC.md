# EMA5/10 + LLM增强 自动化交易 实盘部署规格书

## 一、架构总览

```
┌─────────────┐    ┌──────────┐    ┌──────────────┐
│ K线数据获取  │───▶│ 方向判定  │───▶│ LLM语义审核   │
│ 5m/1h/4h   │    │ EMA+ADX   │    │ 六步检查       │
└─────────────┘    └──────────┘    └──────┬───────┘
                                          │ 通过
                                    ┌─────▼──────┐
                                    │ 入场执行    │
                                    │ 限价挂单    │
                                    └─────┬──────┘
                                          │ 成交
                                    ┌─────▼──────┐
                                    │ SL/TP挂载   │
                                    │ 1.5×ATR止损 │
                                    └────────────┘
```

## 二、品种配置

| 参数 | BTC | ZEC | HYPE | NEAR |
|------|-----|-----|------|------|
| 交易所 | Binance | Binance | Gate.io | Binance |
| 杠杆 | 20x | 10x | 10x | 10x |
| 每仓数量 | 0.005 BTC | 0.5 ZEC | 3 HYPE | 100 NEAR |
| 最大仓位 | 2仓/边 | 2仓/边 | 2仓/边 | 2仓/边 |
| 订单类型 | 限价单 | 限价单 | 限价单 | 限价单 |

## 三、方向判定（三周期EMA+ADX+DI）

### 3.1 信号来源
- **5分钟K线**: EMA5/EMA10排列判短期方向
- **1小时K线**: EMA5/EMA10排列+ADX+DI过滤
- **4小时K线**: EMA5/EMA10排列判长期趋势+ADX确认

### 3.2 做多条件
```
1. 三周期中≥2个周期 EMA5 > EMA10（多头排列）
2. 4h ADX > 25（趋势足够强）
3. +DI > -DI（多头占优）
```

### 3.3 做空条件
```
1. 三周期中≥2个周期 EMA5 < EMA10（空头排列）
2. 4h ADX > 25
3. +DI < -DI（空头占优）
```

### 3.4 方向
- 满足上述条件 → LONG / SHORT
- 不满足 → 观望（不进场）
- 方向变动 → 自动平旧仓

## 四、入场流程

### 4.1 入场价确定
```
做多: 找4h EMA5/EMA10 和 Fib(0.236/0.382)中，在当前价格下方最近的支撑位
做空: 找4h EMA5/EMA10 和 Fib(0.236/0.382)中，在当前价格上方最近的压力位

如果上述支撑/压力位不存在 → 用当前价格
```

### 4.2 止损（1.5×ATR自适应）
```
LONG:  SL = 入场价 - 1.5 × 4h_ATR
SHORT: SL = 入场价 + 1.5 × 4h_ATR
```

### 4.3 入场价变动监控
```
每30秒检查: 入场价变动 > 0.3×ATR → 撤旧限价单，重新挂单
```

### 4.4 LLM审核（六步）
提交给DeepSeek API审核的内容:
```
1. K线形态确认（影线/实体比例/反转形态）
2. OI-价格联动（持仓量是否支持方向）
3. BTC方向共振（是否与BTC大盘方向一致）
4. 入场价可达性（限价是否合理）
5. 费率检查（资金费率是否极端）
6. 多空比检查（市场情绪是否过热）
```

LLM通过 → 执行开仓 / LLM否决 → 写REJECTED日志，拒绝开仓 / LLM崩溃 → 安全拒绝（不降级开仓）

## 五、出场规则

### 5.1 止损
- 入场时即挂止损单（STOP_MARKET）
- 止损价 = 1.5 × 4h_ATR，随入场价变动同步更新

### 5.2 止盈
- 无固定TP（纯ATR止损，方向反转为出场信号）

### 5.3 方向反转
- 三周期EMA排列反转且ADX确认 → 平旧开新

## 六、风险控制

### 6.1 仓位上限
- 每方向最多2仓，超过不再开仓

### 6.2 信号冷却
- 无冷却（方向变动即触发）

### 6.3 -4045保护
- 检测到"Reach max stop order limit" → 自动清理全部algo订单 → 重试挂SL/TP

### 6.4 裸仓保护
- 每30秒检查: 有持仓但无SL/TP挂单 → 自动补挂
- `executor._pending_plan` 存在时不检查（避免空转刷屏）

## 七、运维体系

### 7.1 进程
```
9个bot进程（每品种一个）      cron: * * * * * 每分检查
watchdog.py                    cron: 每分孤儿仓检测
proc_guard.py                  cron: 每分僵尸进程检测
market_enrich.py               cron: 每4h市场数据更新
```

### 7.2 日志
- 每个bot独立日志: `/root/zidongjiaoyi/{品种}_bn.log`
- 交易记录: `/root/zidongjiaoyi/{品种}_bn_trades.txt`
- watchdog日志: `/root/zidongjiaoyi/watchdog.log`

### 7.3 监控脚本
- 实时EMA交叉监控: `/root/zidongjiaoyi/monitor_tick.py` (cron每分钟)
- 日志: `/root/.openclaw/live_monitor_log.txt`

## 八、API密钥

```
Binance API Key:    1iUNLoIbEpVwwi4eHPTrKD25FvsYhR0iEwKLhDuvCOW7EgDa7h9B3PdpzffhghMB
Binance API Secret: YWusnOHhS1OKHXJBJ57B3Q8zih6Ymhk6oK7CK4jJg3U9eOwcdyQ6eraCIaoVgIN6
DeepSeek API Key:   sk-90362f979d1344d29b2baed227cb090f
```

## 九、代码仓库

```
/root/zidongjiaoyi/
├── btc_bnb_auto.py      # BTC Binance
├── hype_bnb_auto.py     # HYPE Gate.io
├── zec_bnb_auto.py      # ZEC Binance
├── near_bnb_auto.py     # NEAR Binance
├── xlm_bnb_auto.py      # XLM
├── wld_bnb_auto.py      # WLD
├── ena_bnb_auto.py      # ENA
├── sui_bnb_auto.py      # SUI
├── bnb_auto.py          # BNB
├── llm_client.py        # LLM调用模块
├── watchdog.py          # 孤儿仓检测
├── proc_guard.py        # 进程守护
├── market_enrich.py     # 市场数据补充
├── monitor_tick.py      # 实时EMA监控
└── start_monitor.sh     # 监控启动脚本

GitHub: https://github.com/l594594158-dev/zidongjiaoyi.git
本地备份: /root/zidongjiaoyi_bak/
```

## 十、部署命令

```bash
# 启动全部bot
cd /root/zidongjiaoyi
for bot in btc hype zec near xlm wld ena sui bnb; do
    nohup python3 -u ${bot}_bnb_auto.py > /dev/null 2>&1 &
    sleep 2
done

# 设置cron
(crontab -l 2>/dev/null; echo "* * * * * python3 /root/zidongjiaoyi/watchdog.py >> /root/zidongjiaoyi/watchdog.log 2>&1") | crontab -
(crontab -l 2>/dev/null; echo "* * * * * python3 /root/zidongjiaoyi/proc_guard.py") | crontab -
(crontab -l 2>/dev/null; echo "0 */4 * * * python3 /root/zidongjiaoyi/market_enrich.py") | crontab -
(crontab -l 2>/dev/null; echo "* * * * * cd /root/zidongjiaoyi && python3 monitor_tick.py >> /tmp/monitor_cron.log 2>&1") | crontab -

# 检查状态
ps aux | grep bnb_auto | grep python
tail -5 /root/zidongjiaoyi/zec_bn.log
```

## 十一、关键经验

### 方向判定
- 不使用单一EMA交叉，而用三周期EMA排列共振（≥2/3周期同向）
- ADX>25过滤震荡市，DI确认方向
- 方向反转时自动平仓，避免持仓在错误方向

### 入场
- 用4h EMA/Fib找回踩位入场，不是市价追
- LLM审核六步是安全闸，崩溃时拒绝开仓不降级

### 止损
- ATR自适应止损（1.5倍），比固定百分比止损更合理
- -4045错误自动清理重试，保证SL/TP必挂

### 代码质量
- direction()去重：每周期只调一次
- 重叠审查防死循环
- raw_symbol正确引用
- adjust=True（EMA默认参数，与回测必须一致）
