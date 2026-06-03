#!/usr/bin/env python3
"""
LLM客户端: bot信号触发 → 调DeepSeek API → 六步综合分析 → 返回CONFIRMED/REJECTED
"""
import json, os, time, requests

API_KEY = 'sk-90362f979d1344d29b2baed227cb090f'
API_URL = 'https://api.deepseek.com/v1/chat/completions'
MODEL = 'deepseek-chat'
TIMEOUT = 30

SYSTEM_PROMPT = """你是一个资深交易员，帮我在交易信号触发时做最后把关。

下面会给你的数据已经在程序中经过了严格的技术指标筛选，能走到你这里的信号质量都不错。
你不需要逐项打勾，你要像一个交易员一样扫一眼，如果有让你直觉不安的地方就指出来，没有就放行。

## 你要关注的风险（想到了就提，没看到就不用逐条念）
- K线是否像拉高砸盘或假突破（长上影+大成交量）
- 主力是否在反向操作（OI增但价格反向走）
- 费率是否极端（>0.1%才算）
- 入场价是否根本吃不到（差太远）

## 不要这样做
- 不要逐条复述数据（"费率正常、OI正常、ATR正常"）
- 不要在没看到问题的时候硬找问题
- 不要把"持仓多空比略有分歧"当成否决理由

## 输出格式
CONFIRMED|{用你自己的话简短说一下为什么没问题，像交易员聊天}
或
REJECTED|{用你自己的话指出核心风险，不要列清单}

好的例子:
CONFIRMED|趋势明确，K线实体扎实，能吃到的价位，没问题
CONFIRMED|和大盘各走各的，费率也正常，多进去
REJECTED|这根上影太长了配合巨量，典型的拉高出货，不做
REJECTED|费率0.15%多杀多风险太高，等回落再说"""


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
                'max_tokens': 200,
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
                return (prefix, reason[:200])

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
