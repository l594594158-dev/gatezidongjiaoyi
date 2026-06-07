#!/usr/bin/env python3
"""
移动止盈监控脚本 (Gate.io版) - 每2秒轮询
读取策略bot写入的state，实时跟踪浮动利润，
峰值回撤≥50%或价格触达动态止盈 → 立即平仓。
用法: python3 trail_monitor_gate.py BTC
"""

import ccxt
import time
import json
import os
import sys
import traceback
from datetime import datetime
from gate_config import GATE_API_KEY, GATE_API_SECRET

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

DD_THRESHOLD = 0.50   # 峰值回撤50%触发
POLL_SECONDS = 2       # 2秒轮询


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line, flush=True)


def cancel_all_sl_tp(ex, coin, symbol, direction):
    """取消所有SL/TP保护单 (Gate: 撤reduceOnly订单)"""
    try:
        for o in ex.fetch_open_orders(symbol):
            if o.get('reduceOnly') or o.get('reduce_only'):
                ex.cancel_order(o['id'], symbol)
    except:
        pass
    try:
        for o in ex.fetch_open_orders(symbol, params={'trigger': True}):
            try:
                ex.cancel_order(o['id'], symbol, params={'trigger': True})
            except:
                pass
    except:
        pass


def main():
    if len(sys.argv) < 2:
        print('用法: python3 trail_monitor_gate.py <COIN>')
        sys.exit(1)

    coin = sys.argv[1].upper()
    symbol = f'{coin}/USDT:USDT'
    state_file = os.path.join(SCRIPT_DIR, f'{coin.lower()}_trail_state.json')

    ex = ccxt.gate({
        'apiKey': GATE_API_KEY,
        'secret': GATE_API_SECRET,
        'options': {'defaultType': 'swap'},
    })
    ex.load_markets()

    log(f'{coin} 移动止盈监控启动 Gate 轮询{POLL_SECONDS}s 回撤阈值{DD_THRESHOLD*100:.0f}%')

    while True:
        try:
            if not os.path.exists(state_file):
                time.sleep(POLL_SECONDS)
                continue

            with open(state_file) as f:
                state = json.load(f)

            if not state.get('active'):
                time.sleep(POLL_SECONDS)
                continue

            direction = state['direction']
            entry_price = state['entry_price']
            if entry_price <= 0:
                log(f'{coin} entry_price=0 无效, 跳过')
                time.sleep(POLL_SECONDS)
                continue
            dynamic_tp = state.get('dynamic_tp', 0)
            position_size = state['position_size']
            peak_pnl = state.get('peak_pnl', 0)

            # 获取当前价
            ticker = ex.fetch_ticker(symbol)
            price = ticker['last']

            # 计算浮动利润
            if direction == 'SHORT':
                pnl_pct = (entry_price - price) / entry_price * 100
            else:
                pnl_pct = (price - entry_price) / entry_price * 100

            # 更新峰值
            if pnl_pct > peak_pnl:
                peak_pnl = pnl_pct
                state['peak_pnl'] = peak_pnl
                with open(state_file, 'w') as f:
                    json.dump(state, f)

            # 检查峰值回撤
            if peak_pnl >= state.get('min_profit', 2.0) and pnl_pct < peak_pnl * (1 - DD_THRESHOLD):
                log(f'🔴 {coin} 回撤止盈触发！ 峰值{peak_pnl:.1f}%→当前{pnl_pct:.1f}% 回撤{100-pnl_pct/max(peak_pnl,0.01)*100:.0f}%')
                cancel_all_sl_tp(ex, coin, symbol, direction)
                close_side = 'buy' if direction == 'SHORT' else 'sell'
                ex.create_order(symbol, 'market', close_side, position_size, None, params={
                    'reduceOnly': True
                })
                log(f'{coin} 回撤止盈平仓完成 价格{price:.1f} 利润{pnl_pct:+.1f}%')
                state['active'] = False
                state['closed_reason'] = f'回撤止盈 峰值{peak_pnl:.1f}%→{pnl_pct:.1f}%'
                with open(state_file, 'w') as f:
                    json.dump(state, f)

            # 检查动态止盈触发
            if dynamic_tp > 0:
                tp_triggered = (direction == 'SHORT' and price <= dynamic_tp) or \
                               (direction == 'LONG' and price >= dynamic_tp)
                if tp_triggered:
                    log(f'🎯 {coin} 动态止盈触发！ 价格{price:.1f} 触达止盈{dynamic_tp:.1f}')
                    cancel_all_sl_tp(ex, coin, symbol, direction)
                    close_side = 'buy' if direction == 'SHORT' else 'sell'
                    ex.create_order(symbol, 'market', close_side, position_size, None, params={
                        'reduceOnly': True
                    })
                    log(f'{coin} 动态止盈平仓完成 价格{price:.1f} 利润{pnl_pct:+.1f}%')
                    state['active'] = False
                    state['closed_reason'] = f'动态止盈触发 {price:.1f}'
                    with open(state_file, 'w') as f:
                        json.dump(state, f)

        except Exception as e:
            log(f'{coin} 监控异常: {e}')
            traceback.print_exc()

        time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    main()
