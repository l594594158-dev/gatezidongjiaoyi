#!/usr/bin/env python3
"""
孤儿单清理 (Gate.io版): 每分钟检查已平仓但SL/TP未撤的孤儿条件单。
四品种全覆盖。
"""
import ccxt, time, json, os
from datetime import datetime
from gate_config import GATE_API_KEY, GATE_API_SECRET

SYMBOLS = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT', 'BNB/USDT:USDT']

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'watchdog_gate.log')

def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    line = f'[WD][{ts}] {msg}'
    print(line, flush=True)
    with open(LOG_FILE, 'a') as f: f.write(line + '\n')

def main():
    exchange = ccxt.gate({
        'apiKey': GATE_API_KEY, 'secret': GATE_API_SECRET,
        'options': {'defaultType': 'swap'},
    })
    exchange.load_markets()

    # 收集所有有持仓的symbol
    positions = exchange.fetch_positions()
    active_symbols = set()
    for p in positions:
        size = float(p.get('contracts', 0))
        if abs(size) > 0:
            active_symbols.add(p['symbol'])

    total_cancelled = 0

    for sym in SYMBOLS:
        has_position = sym in active_symbols
        if has_position:
            continue  # 有仓位，保护单保留

        # 无仓位 → 检查是否有残留的SL/TP单
        try:
            for o in exchange.fetch_open_orders(sym):
                if o.get('reduceOnly') or o.get('reduce_only'):
                    try:
                        exchange.cancel_order(o['id'], sym)
                        log(f'{sym}: 孤儿reduceOnly {o["id"][:16]} 已清理')
                        total_cancelled += 1
                    except:
                        pass
        except Exception as e:
            pass

        try:
            for o in exchange.fetch_open_orders(sym, params={'trigger': True}):
                oid = o['id']
                try:
                    exchange.cancel_order(oid, sym, params={'trigger': True})
                    log(f'{sym}: 孤儿trigger {oid[:16]} 已清理')
                    total_cancelled += 1
                except:
                    pass
        except:
            pass

    if total_cancelled > 0:
        log(f'本轮清理 {total_cancelled} 个孤儿条件单')

if __name__ == '__main__':
    main()
