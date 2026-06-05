#!/usr/bin/env python3
"""
LLM客户端: bot信号触发 → 调DeepSeek API → 六步综合分析 → 返回CONFIRMED/REJECTED
"""
import json, os, time, requests

API_KEY = 'IlPevOWyWpnC2FgpcRlk7kQX24AjjBh6hhD0l5ki5g43AebJy1GwNPH4D3fzZcI9'
API_URL = 'https://api.deepseek.com/v1/chat/completions'
MODEL = 'deepseek-chat'
TIMEOUT = 30

SYSTEM_PROMPT = """你是一个资深交易员，帮我在交易信号触发时做最后把关。

下面会给你的数据已经在程序中经过了严格的技术指标筛选，能走到你这里的信号质量都不错。
你不需要逐项打勾，你要像一个交易员一样扫一眼，如果有让你直觉不安的地方就指出来，没有就放行。

## 你要关注的风险（想到了就提，没看到就不用逐条念）
- **高位追多风险**: 如果24h已经涨了10%+，还做多，要考虑是不是在追顶。看K线是"放量实体推升"还是"缩量冲高"。前者趋势健康、后者要砸
- **低位追空风险**: 同理，24h跌了10%+还做空，要考虑是不是踩底
- K线是否像拉高砸盘或假突破（长上影+大成交量）
- 主力是否在反向操作（OI增但价格反向走）
- 费率是否极端（>0.1%才算）
- 入场价是否根本吃不到（差太远）

## 不要这样做
- 不要逐条复述数据（"费率正常、OI正常、ATR正常"）
- 不要在没看到问题的时候硬找问题
- 不要把"持仓多空比略有分歧"当成否决理由
- 参考历史分析：如果上次否决的理由已不存在（如上次说费率极端现在正常了），可以改判；如果连续多次否决同一方向，想想是不是这个方向就是不对

## 输出格式
先写你的判断过程（想到什么说什么，不用列点），最后一行输出:
CONFIRMED|{简短结论}
或
REJECTED|{核心风险}

像这样的:
这币和大盘完全走反了，说明有自己的资金在推。K线实体一根比一根扎实，不是那种拉一根针就砸的。入场价也就差一点点，一个4h波动就能吃到。费率没毛病。
CONFIRMED|独立走势，K线扎实，可以进
"""


def _load_history(coin):
    """读取同品种最近3次LLM分析历史"""
    import re
    raw_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'llm_raw_think.log')
    if not os.path.exists(raw_path):
        return '(无历史记录)'
    with open(raw_path) as f:
        content = f.read()
    # 提取该币种的分析块
    blocks = re.split(r'══════ ', content)
    coin_blocks = [b for b in blocks if b.startswith(f'{coin} ')]
    if not coin_blocks:
        return '(无历史记录)'
    # 取最近3条
    recent = coin_blocks[-3:]
    lines = []
    for i, b in enumerate(recent):
        b = b.strip()
        # 截取前400字
        lines.append(f'--- 历史第{len(recent)-i}次 ---\n{b[:400]}')
    return '\n\n'.join(lines)


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
            'apiKey': 'IlPevOWyWpnC2FgpcRlk7kQX24AjjBh6hhD0l5ki5g43AebJy1GwNPH4D3fzZcI9',
            'secret': 'cdw4Owv1y7llmXZqwHXSTW0pSDEI68EEP0FCMa09bi5r24YenCV4n6vnRzjQpF1I',
            'options': {'defaultType': 'future'},
        })
        sym = f'{coin}/USDT:USDT'
        coin_t = ex.fetch_ticker(sym)
        coin_pct = coin_t.get('percentage',0)
        coin_high = coin_t.get('high',0)
        coin_low = coin_t.get('low',0)
        coin_change_line = f'{coin}24h: {coin_pct:+.1f}%  区间: ${coin_low:.4f}-${coin_high:.4f}'
        btc_t = ex.fetch_ticker('BTC/USDT:USDT')
        btc_line = f"BTC24h涨跌: {btc_t.get('percentage',0):+.1f}%  → 对比{coin}{coin_pct:+.1f}% → 自己判断同向/背离/独立"
    except:
        btc_line = 'BTC数据获取失败'
        coin_change_line = ''

    price = indicators.get('price', 0)
    atr = indicators.get('atr', 0)
    atr_pct = indicators.get('atr_pct', 0)
    diff = abs(entry - price)
    diff_atr = diff / atr if atr > 0 else 999

    user_msg = f"""## 交易信号
品种: {coin}  方向: {direction}  价格: ${price}
入场: ${entry}  止损: ${sl}  止盈: ${tp}
仓位: {qty} × {leverage}x  入场价差: {diff_atr:.1f}×ATR ({diff/price*100:.1f}%)
{coin_change_line}

## 指标数据
{indicators.get('raw','')}

## 市场增强数据
费率: {enrich.get('funding_rate','?')}%
OI价值: ${enrich.get('oi_value',0)/1e6:.1f}M
持仓多空比: {enrich.get('ls_position_ratio','?')}
大户多空比: {enrich.get('ls_account_ratio','?')}

## BTC联动
{btc_line}

## 同品种历史分析（最近3次）
{_load_history(coin)}"""

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

        # 写入原始思考（完整LLM回复）
        _raw_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'llm_raw_think.log')
        with open(_raw_path, 'a') as _rf:
            _rf.write(f'\n══════ {coin} {direction} ══════\n{content}\n')

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


# ── 持仓动态止盈管理 ──────────────────────────────

POSITION_SYSTEM_PROMPT = """你是一个交易员，我的单子已经在持仓中，帮我判断要不要调整止盈位。

## 给你看的数据
- 品种、方向、入场价、当前价、当前止盈、止损
- 4h ADX/DI 当前值和方向（方便对比趋势是加速还是减速）
- 当前的4h ATR
- BTC联动数据（品种跟着大饼走还是独立行情）

## 你要判断的
不要改变方向，只判断止盈应该怎么调：
- WIDEN: 趋势在加速（ADX上升/DI碾压/放量实体K线），利润还可以多拿 → 放宽止盈
- TIGHTEN: 趋势减速（ADX下降/DI收窄/缩量/接近结构阻力），风险变大 → 收紧止盈，落袋
- KEEP: 中间状态，维持不变

## 新止盈价格怎么定
- WIDEN: 把止盈推到下一个结构位（前高/前低 + 1~2倍ATR）
- TIGHTEN: 把止盈拉到入场价和当前价之间的合理位置（ATR的0.5~1倍）

## 输出格式
先写判断理由，最后一行输出:
KEEP|{理由}
或
WIDEN|{新止盈价}|{理由}
或
TIGHTEN|{新止盈价}|{理由}

像这样:
ADX从82升到86，DI空头碾压格局不变，最近一根4h实体大阴线，趋势踩油门。
WIDEN|1580.0|趋势加速，止盈推到前低1580"""


def manage_position(coin, direction, entry_price, current_price, current_tp, current_sl, atr, indicators_raw):
    """
    LLM评估持仓止盈调整。
    返回: ('KEEP', reason) | ('WIDEN', new_tp, reason) | ('TIGHTEN', new_tp, reason)
    """
    pnl_pct = (current_price - entry_price) / entry_price * 100
    if direction == 'SHORT':
        pnl_pct = -pnl_pct
    tp_dist = abs(current_tp - current_price) / current_price * 100

    # BTC联动
    btc_line = ''
    try:
        import ccxt
        ex = ccxt.binance()
        btc_t = ex.fetch_ticker('BTC/USDT:USDT')
        coin_t = ex.fetch_ticker(f'{coin}/USDT:USDT')
        btc_line = f"BTC24h: {btc_t.get('percentage',0):+.1f}%  {coin}24h: {coin_t.get('percentage',0):+.1f}%"
    except:
        pass

    user_msg = f"""## 持仓状态
品种: {coin}  方向: {direction}
入场价: ${entry_price}  当前价: ${current_price}  浮动盈亏: {pnl_pct:+.1f}%
当前止盈: ${current_tp} (距现价 {tp_dist:.1f}%)  止损: ${current_sl}

## 指标
{indicators_raw}
4h ATR: ${atr:.1f}

## BTC联动
{btc_line}"""

    try:
        resp = requests.post(
            API_URL,
            headers={'Authorization': f'Bearer {API_KEY}', 'Content-Type': 'application/json'},
            json={
                'model': MODEL,
                'messages': [
                    {'role': 'system', 'content': POSITION_SYSTEM_PROMPT},
                    {'role': 'user', 'content': user_msg},
                ],
                'temperature': 0.1,
                'max_tokens': 150,
            },
            timeout=TIMEOUT,
        )

        if resp.status_code != 200:
            return ('KEEP', f'API错误{resp.status_code}')

        content = resp.json()['choices'][0]['message']['content'].strip()

        # 写入LLM思考日志
        _raw_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'llm_raw_think.log')
        with open(_raw_path, 'a') as _rf:
            _rf.write(f'\n══════ {coin} TP管理 ══════\n{content}\n')

        # 解析
        for prefix in ['KEEP', 'WIDEN', 'TIGHTEN']:
            if content.startswith(prefix):
                rest = content[len(prefix):].lstrip('|').strip()
                if prefix == 'KEEP':
                    return ('KEEP', rest[:200])
                else:
                    # WIDEN|price|reason 或 TIGHTEN|price|reason
                    parts = rest.split('|', 1)
                    try:
                        new_tp = float(parts[0].strip())
                        reason = parts[1].strip() if len(parts) > 1 else ''
                        return (prefix, new_tp, reason[:200])
                    except ValueError:
                        return ('KEEP', f'无法解析价格: {rest[:80]}')

        return ('KEEP', f'输出格式异常: {content[:80]}')

    except requests.Timeout:
        return ('KEEP', 'API超时')
    except Exception as e:
        return ('KEEP', f'异常: {str(e)[:60]}')


def now():
    """bot调用入口: 同步分析并返回决策"""
    try:
        import ccxt
        ex = ccxt.binance({
            'apiKey': 'IlPevOWyWpnC2FgpcRlk7kQX24AjjBh6hhD0l5ki5g43AebJy1GwNPH4D3fzZcI9',
            'secret': 'cdw4Owv1y7llmXZqwHXSTW0pSDEI68EEP0FCMa09bi5r24YenCV4n6vnRzjQpF1I',
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
