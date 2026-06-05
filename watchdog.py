#!/usr/bin/env python3
"""
每分钟自检：清理已平仓但止盈止损未撤的孤儿条件单。
五品种全覆盖。
"""
import ccxt, time, json, os
from datetime import datetime

API_KEY = 'IlPevOWyWpnC2FgpcRlk7kQX24AjjBh6hhD0l5ki5g43AebJy1GwNPH4D3fzZcI9'
API_SECRET = 'cdw4Owv1y7llmXZqwHXSTW0pSDEI68EEP0FCMa09bi5r24YenCV4n6vnRzjQpF1I'

SYMBOLS = ['BTC/USDT:USDT', 'HYPE/USDT:USDT', 'ZEC/USDT:USDT',
           'NEAR/USDT:USDT', 'XLM/USDT:USDT', 'WLD/USDT:USDT',
           'ENA/USDT:USDT', 'SUI/USDT:USDT', 'BNB/USDT:USDT']

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'watchdog.log')

def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    line = f'[WD][{ts}] {msg}'
    print(line, flush=True)
    with open(LOG_FILE, 'a') as f: f.write(line + '\n')

def main():
    exchange = ccxt.binance({
        'apiKey': API_KEY, 'secret': API_SECRET,
        'options': {'defaultType': 'future'},
    })
    exchange.load_markets()

    # 收集所有持仓symbol
    positions = exchange.fetch_positions()
    active_symbols = set()
    for p in positions:
        amt = float(p.get('info', {}).get('positionAmt', 0))
        if abs(amt) > 0:
            active_symbols.add(p['symbol'])

    # 检查所有品种的openAlgoOrders
    import requests as rq, hmac as hm, hashlib as hl, urllib.parse as up
    BASE = 'https://fapi.binance.com'

    def signed(params):
        params['timestamp'] = int(time.time() * 1000)
        q = up.urlencode(params)
        params['signature'] = hm.new(API_SECRET.encode(), q.encode(), hl.sha256).hexdigest()
        return params

    hd = {'X-MBX-APIKEY': API_KEY}
    total_cancelled = 0

    for sym in SYMBOLS:
        raw = sym.split(':')[0].replace('/', '')
        try:
            p = signed({'symbol': raw})
            algos = rq.get(f'{BASE}/fapi/v1/openAlgoOrders?{up.urlencode(p)}', headers=hd).json()
        except Exception as e:
            log(f'{raw}: 查询异常 {e}')
            continue

        has_position = sym in active_symbols

        if not isinstance(algos, list):
            log(f'{raw}: API返回异常(非列表) → {str(algos)[:100]}')
            continue

        for a in algos:
            oid = a.get('algoId', a.get('orderId', 0))
            otype = a.get('orderType', a.get('type', '?'))

            if has_position:
                continue  # 有仓位，条件单保留

            # 无仓位但有条件单 → 孤儿，删除
            try:
                p2 = signed({'symbol': raw, 'algoId': oid})
                rq.delete(f'{BASE}/fapi/v1/algoOrder?{up.urlencode(p2)}', headers=hd)
                log(f'{raw}: 孤儿{otype} {oid} 已清理（无对应持仓）')
                total_cancelled += 1
            except Exception as e:
                pass  # 可能已被其他进程清理

    if total_cancelled > 0:
        log(f'本轮清理 {total_cancelled} 个孤儿条件单')
    # 不记安静日志，保持日志文件干净
    # 仅清理时才写入

if __name__ == '__main__':
    main()
