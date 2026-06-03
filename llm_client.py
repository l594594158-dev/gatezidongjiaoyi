#!/usr/bin/env python3
"""
LLM客户端: bot信号触发 → 调DeepSeek API → 六步综合分析 → 返回CONFIRMED/REJECTED
"""
import json, os, time, requests

API_KEY = 'sk-90362f979d1344d29b2baed227cb090f'
API_URL = 'https://api.deepseek.com/v1/chat/completions'
MODEL = 'deepseek-chat'
TIMEOUT = 30

SYSTEM_PROMPT = """你是一个加密货币量化交易的风险审核员。你的职责是过滤明显危险的信号，不是吹毛求疵。
指标已经做了层层过滤，到你这里的信号大概率是合理的。默认倾向通过，只在有明确风险证据时才否决。

## 分析步骤（只有红色警报才否决）

### ① K线形态
最近3根4h K线: 上影线>实体3x且位于关键阻力位 → 假突破风险。普通K线震荡不作否决理由。
仅在上影线极长(>3x实体)+位置敏感时标记，其余情况默认通过。

### ② OI-价格联动
OI↓+价格↓=止盈平仓正常现象，不是危险信号。
仅OI↑+价格反向运行时才警告（主力出货/吸筹）。

### ③ BTC联动
判断该币是独立行情还是跟随BTC:
- 跟随BTC且BTC暴跌>5% → 同向仓位危险
- 独立行情或BTC平稳 → 不否决

### ④ 入场价可达性
差值/ATR > 1.5 = 大概率吃不到，否决。
差值/ATR ≤ 1.5 = 可达，不否决。

### ⑤ 费率极端
仅费率>0.1%（多头极端）或<-0.1%（空头极端）才否决。
其余费率水平仅记录不否决。

### ⑥ 多空比对照
大户反向+持仓反向同时出现才否决。仅单一分歧不否决。

## 核心原则
- 信号已经过多层技术过滤，默认可靠
- 你的任务是抓致命伤（假突破/极端费率/BTC崩盘联动），不是找瑕疵
- 单个模糊因素不足以否决，需要多个危险信号叠加才拒绝
- 宁可放行一个平庸信号，也不错杀一个好信号

## 输出格式
CONFIRMED|{理由，不超过40字}
或
REJECTED|{致命理由，不超过40字}

示例: CONFIRMED|OI增+入场0.7ATR可达+K线实体饱满
示例: REJECTED|OI↑价格反向主力出货+费率0.12%极端+假突破"""


def analyze(coin, direction, entry, sl, tp, qty, leverage, indicators, enrich):
    """
    调DeepSeek进行六步分析。
    返回: ('CONFIRMED', reason) 或 ('REJECTED', reason)
    """
    # 构建BTC联动数据
    btc_line = ''
    coin_change = ''
    try:
        import ccxt
        ex = ccxt.binance({
            'apiKey': '1iUNLoIbEpVwwi4eHPTrKD25FvsYhR0iEwKLhDuvCOW7EgDa7h9B3PdpzffhghMB',
            'secret': 'YWusnOHhS1OKHXJBJ57B3Q8zih6Ymhk6oK7CK4jJg3U9eOwcdyQ6eraCIaoVgIN6',
            'options': {'defaultType': 'future'},
        })
        sym = f'{coin}/USDT:USDT'
        coin_t = ex.fetch_ticker(sym)
        coin_change = f"{coin}24h涨跌: {coin_t.get('percentage',0):+.1f}%"
        btc_t = ex.fetch_ticker('BTC/USDT:USDT')
        btc_line = f"BTC24h涨跌: {btc_t.get('percentage',0):+.1f}%  → {coin_change} → 自己判断同向/背离/独立"
    except:
        btc_line = 'BTC数据获取失败'

    price = indicators.get('price', 0)
    atr = indicators.get('atr', 0)
    atr_pct = indicators.get('atr_pct', 0)
    diff = abs(entry - price)
    diff_atr = diff / atr if atr > 0 else 999

    user_msg = f"""## 交易信号
品种: {coin}  方向: {direction}  价格: ${price}
入场: ${entry}  止损: ${sl}  止盈: ${tp}
仓位: {qty} × {leverage}x  入场价差: {diff_atr:.1f}×ATR ({diff/price*100:.1f}%)

## 指标数据
{indicators.get('raw','')}

## 市场增强数据
费率: {enrich.get('funding_rate','?')}%
OI价值: ${enrich.get('oi_value',0)/1e6:.1f}M
持仓多空比: {enrich.get('ls_position_ratio','?')}
大户多空比: {enrich.get('ls_account_ratio','?')}

## BTC联动
{btc_line}"""

    try:
        resp = requests.post(
            API_URL,
            headers={
                'Authorization': f'Bearer {API_KEY}',
                'Content-Type': 'application/json',
            },
            json={
                'model': MODEL,
                'messages': [
                    {'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'user', 'content': user_msg},
                ],
                'temperature': 0.1,
                'max_tokens': 150,
            },
            timeout=TIMEOUT,
        )

        if resp.status_code != 200:
            return ('REJECTED', f'API错误{resp.status_code}')

        content = resp.json()['choices'][0]['message']['content'].strip()

        # 解析 CONFIRMED|reason 或 REJECTED|reason
        for prefix in ['CONFIRMED', 'REJECTED']:
            if content.startswith(prefix):
                reason = content[len(prefix):].lstrip('|').strip()
                return (prefix, reason[:100])

        # 兜底: 如果在输出里找到了关键词
        if 'CONFIRM' in content.upper() and 'REJECT' not in content.upper():
            return ('CONFIRMED', content[:100])
        if 'REJECT' in content.upper():
            return ('REJECTED', content[:100])

        return ('REJECTED', f'无法解析: {content[:80]}')

    except requests.Timeout:
        return ('REJECTED', 'API超时')
    except Exception as e:
        return ('REJECTED', f'异常: {str(e)[:60]}')


def now():
    """bot调用入口: 同步分析并返回决策"""
    try:
        import ccxt
        ex = ccxt.binance({
            'apiKey': '1iUNLoIbEpVwwi4eHPTrKD25FvsYhR0iEwKLhDuvCOW7EgDa7h9B3PdpzffhghMB',
            'secret': 'YWusnOHhS1OKHXJBJ57B3Q8zih6Ymhk6oK7CK4jJg3U9eOwcdyQ6eraCIaoVgIN6',
            'options': {'defaultType': 'future'},
        })
    except:
        pass

    # 扫描信号目录
    signal_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'signals')
    if not os.path.exists(signal_dir):
        return

    for f in sorted(os.listdir(signal_dir)):
        if not f.endswith('_signal.json'):
            continue
        coin = f.replace('_signal.json', '')
        resp_file = os.path.join(signal_dir, f'{coin}_response.json')
        if os.path.exists(resp_file):
            continue  # 已经处理过

        with open(os.path.join(signal_dir, f)) as fp:
            sig = json.load(fp)

        enrich = sig.get('enrich', {})
        indicators = {
            'price': sig['entry_price'],
            'atr': enrich.get('oi_value', 0) * 0.001,
            'atr_pct': 3.0,
            'raw': sig.get('analysis', ''),
        }

        decision, reason = analyze(
            sig['coin'], sig['direction'],
            sig['entry_price'], sig['stop_loss'], sig['take_profit'],
            sig['qty'], sig['leverage'],
            indicators, enrich,
        )

        from llm_review import llm_confirm, llm_reject
        if decision == 'CONFIRMED':
            llm_confirm(coin, reason)
            print(f'{coin}: CONFIRMED → {reason}')
        else:
            llm_reject(coin, reason)
            print(f'{coin}: REJECTED → {reason}')


if __name__ == '__main__':
    now()
