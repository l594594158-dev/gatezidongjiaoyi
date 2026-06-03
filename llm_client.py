#!/usr/bin/env python3
"""
LLM客户端: bot信号触发 → 调DeepSeek API → 六步综合分析 → 返回CONFIRMED/REJECTED
"""
import json, os, time, requests

API_KEY = 'sk-90362f979d1344d29b2baed227cb090f'
API_URL = 'https://api.deepseek.com/v1/chat/completions'
MODEL = 'deepseek-chat'
TIMEOUT = 30

SYSTEM_PROMPT = """你是一个加密货币量化交易的风险审核员。你会收到一个交易信号和六组数据。
你必须按以下六步逐项分析，然后给出 CONFIRMED 或 REJECTED 的最终裁决。

## 分析步骤

### ① K线形态
检查最近3根4h K线: 上影线>实体2x = 假突破/流动性掠夺 → 危险。实体饱满 = 方向确认。
如果现价刚突破关键位就出现长影线回拉 → 假突破概率高。

### ② OI-价格联动
未平仓合约变化方向: OI↑+价格↑=趋势健康多; OI↑+价格横/跌=主力出货; OI↓+价格↓=多头溃败。
对比当前OI值和半小时前的变化。

### ③ BTC仅供参考（不否决）
BTC走势仅作为背景信息参考，不作为否决理由。
该币是独立行情品种，不跟随大盘走向。
若BTC与该币方向一致=加分项，不一致=忽略。

### ④ 入场价可达性
入场价 vs 当前价差距: 差值/ATR > 1.5 = 很可能吃不到，降级。
差值/ATR < 0.5 = 容易吃到，加分。

### ⑤ 费率极端
做多时费率>0.05% = 多头拥挤警告。做空时费率<-0.05% = 空头拥挤警告。
费率>0.1% = 直接否决对应方向。

### ⑥ 多空比对照
持仓多空比 vs 大户多空比: 两者同向 = 一致加分; 反向 = 分歧警告。
大户反向意味着聪明钱不认可当前方向。

## 输出格式
你必须且只能输出以下格式（不要任何额外文字）:

CONFIRMED|{一句话理由，不超过40字}
或
REJECTED|{一句话理由，不超过40字}

理由必须具体，引用数据。不要泛泛说"指标良好"。
示例: CONFIRMED|OI增+入场差2%ATR可达+K线实体饱满+持仓大户一致
示例: REJECTED|入场差7%ATR不可达+费率0.012%偏多但未极端+持仓大户分歧
"""


def analyze(coin, direction, entry, sl, tp, qty, leverage, indicators, enrich):
    """
    调DeepSeek进行六步分析。
    返回: ('CONFIRMED', reason) 或 ('REJECTED', reason)
    """
    # 构建BTC联动数据
    btc_line = ''
    try:
        import ccxt
        ex = ccxt.binance({
            'apiKey': '1iUNLoIbEpVwwi4eHPTrKD25FvsYhR0iEwKLhDuvCOW7EgDa7h9B3PdpzffhghMB',
            'secret': 'YWusnOHhS1OKHXJBJ57B3Q8zih6Ymhk6oK7CK4jJg3U9eOwcdyQ6eraCIaoVgIN6',
            'options': {'defaultType': 'future'},
        })
        btc_t = ex.fetch_ticker('BTC/USDT:USDT')
        btc_line = f"BTC当前: ${btc_t['last']:.0f}  24h涨跌: {btc_t.get('percentage',0):+.1f}%"
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
