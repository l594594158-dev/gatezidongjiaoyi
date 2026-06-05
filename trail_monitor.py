#!/usr/bin/env python3
"""
移动止盈监控脚本 - 每2秒轮询
读取策略bot写入的state，实时跟踪浮动利润，
峰值回撤≥50%或价格触达动态止盈 → 立即平仓。
用法: python3 trail_monitor.py BTC
"""

import ccxt
import time
import json
import os
import sys
import traceback
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

API_KEY = 'IlPevOWyWpnC2FgpcRlk7kQX24AjjBh6hhD0l5ki5g43AebJy1GwNPH4D3fzZcI9'
API_SECRET = 'cdw4Owv1y7llmXZqwHXSTW0pSDEI68EEP0FCMa09bi5r24YenCV4n6vnRzjQpF1I'

DD_THRESHOLD = 0.50   # 峰值回撤50%触发
POLL_SECONDS = 2       # 2秒轮询


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line, flush=True)


def main():
    if len(sys.argv) < 2:
        print('用法: python3 trail_monitor.py <COIN>')
        sys.exit(1)

    coin = sys.argv[1].upper()
    symbol = f'{coin}/USDT:USDT'
    state_file = os.path.join(SCRIPT_DIR, f'{coin.lower()}_trail_state.json')

    ex = ccxt.binance({
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'options': {'defaultType': 'swap'},
    })
    ex.load_markets()

    log(f'{coin} 移动止盈监控启动 轮询{POLL_SECONDS}s 回撤阈值{DD_THRESHOLD*100:.0f}%')

    while True:
        try:
            # 读取state
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
                # 取消所有SL/TP
                try:
                    import requests as rq, hmac as hm, hashlib as hl, urllib.parse as up
                    BASE = 'https://fapi.binance.com'
                    def sign_(params):
                        params['timestamp'] = int(time.time() * 1000)
                        q = up.urlencode(params)
                        params['signature'] = hm.new(API_SECRET.encode(), q.encode(), hl.sha256).hexdigest()
                        return params
                    p = sign_({'symbol': coin + 'USDT'})
                    hd = {'X-MBX-APIKEY': API_KEY}
                    algos = rq.get(f'{BASE}/fapi/v1/openAlgoOrders?{up.urlencode(p)}', headers=hd).json()
                    if isinstance(algos, list):
                        for a in algos:
                            p2 = sign_({'symbol': coin + 'USDT', 'algoId': a['algoId']})
                            rq.delete(f'{BASE}/fapi/v1/algoOrder?{up.urlencode(p2)}', headers=hd)
                except:
                    pass
                # 市价平仓
                close_side = 'buy' if direction == 'SHORT' else 'sell'
                ex.create_order(symbol, 'MARKET', close_side, position_size, None, params={
                    'positionSide': direction, 'reduceOnly': True
                })
                log(f'{coin} 回撤止盈平仓完成 价格{price:.1f} 利润{pnl_pct:+.1f}%')
                state['active'] = False
                state['closed_reason'] = f'回撤止盈 峰值{peak_pnl:.1f}%→{pnl_pct:.1f}%'
                with open(state_file, 'w') as f:
                    json.dump(state, f)

            # 检查动态止盈触发（LLM设定的新TP目标）
            if dynamic_tp > 0:
                tp_triggered = (direction == 'SHORT' and price <= dynamic_tp) or \
                               (direction == 'LONG' and price >= dynamic_tp)
                if tp_triggered:
                    log(f'🎯 {coin} 动态止盈触发！ 价格{price:.1f} 触达止盈{dynamic_tp:.1f}')
                    try:
                        import requests as rq, hmac as hm, hashlib as hl, urllib.parse as up
                        BASE = 'https://fapi.binance.com'
                        def sign_(params):
                            params['timestamp'] = int(time.time() * 1000)
                            q = up.urlencode(params)
                            params['signature'] = hm.new(API_SECRET.encode(), q.encode(), hl.sha256).hexdigest()
                            return params
                        p = sign_({'symbol': coin + 'USDT'})
                        hd = {'X-MBX-APIKEY': API_KEY}
                        algos = rq.get(f'{BASE}/fapi/v1/openAlgoOrders?{up.urlencode(p)}', headers=hd).json()
                        if isinstance(algos, list):
                            for a in algos:
                                p2 = sign_({'symbol': coin + 'USDT', 'algoId': a['algoId']})
                                rq.delete(f'{BASE}/fapi/v1/algoOrder?{up.urlencode(p2)}', headers=hd)
                    except:
                        pass
                    close_side = 'buy' if direction == 'SHORT' else 'sell'
                    ex.create_order(symbol, 'MARKET', close_side, position_size, None, params={
                        'positionSide': direction, 'reduceOnly': True
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
