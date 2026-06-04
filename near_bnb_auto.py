import os
#!/usr/bin/env python3
"""
NEAR 全自动交易机器人 - Binance 版
框架与BTC完全一致：三周期EMA+ADX+DI判方向，4h EMA/Fib共振入场，ATR自适应止损。
参数：10x杠杆，3 NEAR/笔。
"""
import ccxt, pandas as pd, numpy as np, time, json, os, traceback
from datetime import datetime

SYMBOL = 'NEAR/USDT:USDT'
LEVERAGE = 10
POSITION_SIZE = 100.0
TIMEFRAMES = ['1h', '4h', '1d']
SL_ATR_MULT = 1.5
FIB_LEVELS = [0.236, 0.382]
MIN_ADX = 25
DI_RATIO = 1.5
POLL_SECONDS = 300

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, 'near_bn_state.json')
LOG_FILE = os.path.join(SCRIPT_DIR, 'near_bn.log')
TRADE_LOG = os.path.join(SCRIPT_DIR, 'near_bn_trades.txt')

API_KEY = '1iUNLoIbEpVwwi4eHPTrKD25FvsYhR0iEwKLhDuvCOW7EgDa7h9B3PdpzffhghMB'
API_SECRET = 'YWusnOHhS1OKHXJBJ57B3Q8zih6Ymhk6oK7CK4jJg3U9eOwcdyQ6eraCIaoVgIN6'

def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line, flush=True)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')

def log_trade(entry):
    ts = entry.get('_timestamp') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    action = entry.get('action')
    d = entry.get('direction', '')
    d_cn = '做空' if d == 'SHORT' else '做多'
    lines = ['═══════════════════════════════════', f'时间: {ts}']
    if action == 'OPEN':
        lines += [
            f'操作: 开仓{d_cn}',
            f'数量: {entry.get("qty")} NEAR | 杠杆: {entry.get("leverage")}x',
            f'入场价: {entry.get("entry_price")} USDT ({entry.get("entry_type","")})',
            f'止损: {entry.get("sl")} USDT (-{entry.get("sl_pct")}%)',
            f'止盈: {entry.get("tp")} USDT (+{entry.get("tp_pct")}%)',
        ]
    elif action == 'FILLED':
        lines += [
            f'操作: 挂单成交{d_cn}',
            f'数量: {entry.get("qty")} 成交价: {entry.get("entry_price")} USDT',
        ]
    elif action == 'CANCEL':
        cancel_id = entry.get('order_id', '')
        lines += [
            f'操作: 取消挂单{d_cn}',
            f'数量: {entry.get("qty")} 订单ID: {cancel_id}',
            f'挂单价: {entry.get("entry_price")} USDT',
            f'原因: {entry.get("cancel_reason", "signal_change")}',
        ]
    elif action == 'CLOSE':
        lines += [
            f'操作: 平仓{d_cn}',
            f'数量: {entry.get("qty")} NEAR',
            f'开仓价: {entry.get("entry_price")} USDT',
            f'盈亏: {entry.get("upnl")} USDT',
        ]
    analysis = entry.get('analysis', {})
    if analysis:
        lines.append('── 分析依据 ──')
        for tf_name, tf_data in analysis.get('timeframes', {}).items():
            al = tf_data.get('alignment', '')
            al_cn = '多头排列' if al == 'bull' else '空头排列'
            lines.append(f'  {tf_name} EMA5={tf_data.get("ema5")} EMA10={tf_data.get("ema10")} -> {al_cn}')
        h4 = analysis.get('4h', {})
        if h4:
            trend_word = '强趋势' if h4.get('adx', 0) > 25 else '震荡'
            lines.append(f'  4h ADX={h4.get("adx")} {trend_word} +DI={h4.get("plus_di")} -DI={h4.get("minus_di")}')
            lines.append(f'  4h ATR={h4.get("atr")} USDT ({h4.get("atr_pct")}%)')
        for r in analysis.get('direction_rationale', {}).get('reasons', []):
            lines.append(f'    * {r}')
    lines += ['═══════════════════════════════════', '']
    with open(TRADE_LOG, 'a') as f:
        f.write('\n'.join(lines))

def compute(df):
    df['ema5'] = df['close'].ewm(span=5).mean()
    df['ema10'] = df['close'].ewm(span=10).mean()
    tr = pd.concat([df['high']-df['low'], abs(df['high']-df['close'].shift(1)), abs(df['low']-df['close'].shift(1))], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    atr_s = df['atr']
    up = df['high'] - df['high'].shift(1)
    down = df['low'].shift(1) - df['low']
    pdm = pd.Series(np.where((up > down) & (up > 0), up, 0), index=df.index)
    ndm = pd.Series(np.where((down > up) & (down > 0), down, 0), index=df.index)
    pdi = 100 * pdm.rolling(14).mean() / atr_s.replace(0, np.nan)
    ndi = 100 * ndm.rolling(14).mean() / atr_s.replace(0, np.nan)
    dx = 100 * abs(pdi - ndi) / (pdi + ndi).replace(0, np.nan)
    df['adx'] = dx.rolling(14).mean()
    df['plus_di'] = pdi
    df['minus_di'] = ndi
    return df

class Analyzer:
    def __init__(self, exchange):
        self.ex = exchange
        self.data = {}

    def fetch(self):
        for tf in TIMEFRAMES:
            raw = self.ex.fetch_ohlcv(SYMBOL, tf, limit=100 if tf == '1h' else 60)
            df = pd.DataFrame(raw, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
            df['ts'] = pd.to_datetime(df['ts'], unit='ms')
            df.set_index('ts', inplace=True)
            self.data[tf] = compute(df)

    @property
    def price(self):
        return self.data['1h']['close'].iloc[-1]

    def direction(self):
        h1 = self.data['1h']
        h4 = self.data['4h']
        d1 = self.data.get('1d')
        last4 = h4.iloc[-1]

        dfs = [h1, h4]
        if d1 is not None and len(d1) > 0:
            dfs.append(d1)

        ema_bear = sum(1 for df in dfs if df['ema5'].iloc[-1] < df['ema10'].iloc[-1])
        ema_bull = sum(1 for df in dfs if df['ema5'].iloc[-1] > df['ema10'].iloc[-1])

        adx_ok = pd.notna(last4['adx']) and last4['adx'] > MIN_ADX
        di_bear = (pd.notna(last4['minus_di']) and pd.notna(last4['plus_di']) and
                   last4['minus_di'] > last4['plus_di'] * DI_RATIO)
        di_bull = (pd.notna(last4['minus_di']) and pd.notna(last4['plus_di']) and
                   last4['plus_di'] > last4['minus_di'] * DI_RATIO)

        # 基础方向
        base = None
        if ema_bear >= 2 and adx_ok and di_bear:
            base = 'SHORT'
        elif ema_bull >= 2 and adx_ok and di_bull:
            base = 'LONG'
        if base is None:
            return None

        # 信号质量过滤
        h4_row = h4.iloc[-1]; h4_prev = h4.iloc[-2]  # 上根已收盘K线用于量/形态过滤
        vol_ma20 = h4['vol'].rolling(20).mean().iloc[-1]
        vol_ok = h4_prev['vol'] > 0.8 * vol_ma20

        # 用1h闭K RSI，比4h灵敏4倍
        h1_rsi = h1['close'].diff()
        h1_gain = h1_rsi.clip(lower=0).rolling(14).mean().iloc[-1]
        h1_loss = (-h1_rsi.clip(upper=0)).rolling(14).mean().iloc[-1]
        # RSI用当前未收盘K线(iloc[-1])，因为只是安全阀不是信号，不需要等闭K
        rsi = 100 - 100 / (1 + h1_gain / h1_loss) if h1_loss > 0 else 100
        rsi_ok = 25 < rsi < 75

        candle_range = h4_prev['high'] - h4_prev['low']
        candle_body = abs(h4_prev['close'] - h4_prev['open'])
        candle_ok = candle_range > 0 and candle_body / candle_range > 0.3

        # 任一不满足 → 观望
        if not vol_ok:
            log(f'  信号过滤: 缩量(vol={h4_row["vol"]:.0f} < 0.8xMA20={vol_ma20:.0f}) → 观望')
            return None
        if not rsi_ok:
            log(f'  信号过滤: RSI极值(1h RSI={rsi:.0f}) → 观望')
            return None
        if not candle_ok:
            log(f'  信号过滤: K线不够坚定(body={candle_body:.4f}/range={candle_range:.4f}={candle_body/candle_range*100:.0f}%) → 观望')
            return None
        return base

    def plan(self):
        d = self.direction()
        if d is None:
            return None
        price = self.price
        df4 = self.data['4h']
        last4 = df4.iloc[-1]
        r30 = df4.tail(30)
        hi = r30['high'].max()
        lo = r30['low'].min()
        atr = last4['atr']

        if d == 'SHORT':
            levels = []
            for name, v in [('4h_EMA5', last4['ema5']), ('4h_EMA10', last4['ema10'])]:
                if pd.notna(v) and v > price:
                    levels.append((v, name))
            for fib in FIB_LEVELS:
                lvl = lo + (hi - lo) * fib
                if lvl > price:
                    levels.append((lvl, f'Fib_{fib:.1%}'))
            levels.sort()
            entry = levels[0][0] if levels else price
            ename = levels[0][1] if levels else 'market'
            sl = entry + SL_ATR_MULT * atr
            tp = lo
        else:
            levels = []
            for name, v in [('4h_EMA5', last4['ema5']), ('4h_EMA10', last4['ema10'])]:
                if pd.notna(v) and v < price:
                    levels.append((v, name))
            for fib in FIB_LEVELS:
                lvl = hi - (hi - lo) * fib
                if lvl < price:
                    levels.append((lvl, f'Fib_{fib:.1%}'))
            levels.sort(reverse=True)
            entry = levels[0][0] if levels else price
            ename = levels[0][1] if levels else 'market'
            sl = entry - SL_ATR_MULT * atr
            tp = hi

        return {
            'direction': d,
            'entry': entry,
            'entry_name': ename,
            'sl': sl,
            'tp': tp,
            'atr': atr,
            'price': price,
            'analysis': self._analysis(d),
        }

    def _analysis(self, direction):
        h1 = self.data['1h']
        h4 = self.data['4h']
        last4 = h4.iloc[-1]
        r30 = h4.tail(30)
        hi = r30['high'].max()
        lo = r30['low'].min()
        price = self.price

        ema_data = {}
        for tf_name, df in [('1h', h1), ('4h', h4)]:
            lr = df.iloc[-1]
            bull = lr['ema5'] > lr['ema10'] if pd.notna(lr['ema5']) and pd.notna(lr['ema10']) else False
            ema_data[tf_name] = {
                'ema5': round(float(lr['ema5']), 3),
                'ema10': round(float(lr['ema10']), 3),
                'alignment': 'bull' if bull else 'bear',
            }

        levels = []
        if direction == 'SHORT':
            for name, v in [('4h_EMA5', last4['ema5']), ('4h_EMA10', last4['ema10'])]:
                if pd.notna(v):
                    levels.append({'level': round(float(v), 3), 'name': name, 'type': 'resistance' if v > price else 'broken'})
            for fib in FIB_LEVELS:
                lvl = lo + (hi - lo) * fib
                levels.append({'level': round(lvl, 3), 'name': f'Fib_{fib:.1%}', 'type': 'resistance' if lvl > price else 'broken'})
        else:
            for name, v in [('4h_EMA5', last4['ema5']), ('4h_EMA10', last4['ema10'])]:
                if pd.notna(v):
                    levels.append({'level': round(float(v), 3), 'name': name, 'type': 'support' if v < price else 'broken'})
            for fib in FIB_LEVELS:
                lvl = hi - (hi - lo) * fib
                levels.append({'level': round(lvl, 3), 'name': f'Fib_{fib:.1%}', 'type': 'support' if lvl < price else 'broken'})

        rationale = []
        for tf_name, df in [('1h', h1), ('4h', h4)]:
            e5 = df['ema5'].iloc[-1]
            e10 = df['ema10'].iloc[-1]
            is_bull = e5 > e10
            arrow = '>' if is_bull else '<'
            label = '多头' if is_bull else '空头'
            rationale.append(f'{tf_name} EMA{label}排列(EMA5={e5:.3f}{arrow}EMA10={e10:.3f})')

        adx_v = last4['adx']
        di_p = last4['plus_di']
        di_m = last4['minus_di']
        trend_word = '强趋势' if adx_v > 25 else '弱趋势'
        rationale.append(f'4h ADX={adx_v:.0f} {trend_word} +DI={di_p:.0f} -DI={di_m:.0f}')

        return {
            'price': round(float(price), 3),
            'timeframes': ema_data,
            '4h': {
                'adx': round(float(adx_v), 1),
                'plus_di': round(float(di_p), 1),
                'minus_di': round(float(di_m), 1),
                'atr': round(float(last4['atr']), 3),
                'atr_pct': round(float(last4['atr'] / price * 100), 2),
            },
            'recent_range': {
                'high': round(float(hi), 3),
                'low': round(float(lo), 3),
                'range_pct': round(float((hi - lo) / lo * 100), 2),
            },
            'key_levels': levels,
            'direction_rationale': {'reasons': rationale, 'conclusion': direction},
        }

class Executor:
    def __init__(self, exchange):
        self.ex = exchange
        self._pending_plan = None

    def has_position(self, direction):
        for p in self.ex.fetch_positions([SYMBOL]):
            if float(p.get('contracts', 0)) > 0:
                if p.get('info', {}).get('positionSide', '').upper() == direction:
                    return True
        return False

    def get_any_position(self):
        for p in self.ex.fetch_positions([SYMBOL]):
            if float(p.get('contracts', 0)) > 0:
                return p
        return None

    def cancel_all_sl_tp(self):
        self.cancel_all_orders()

    def cancel_all_orders(self):
        try:
            raw_symbol = SYMBOL.split(':')[0].replace('/','')
            open_orders = self.ex.fetch_open_orders(SYMBOL)
            for o in open_orders:
                self.ex.cancel_order(o['id'], SYMBOL)
                log(f'撤限价单: {o["id"]}')
        except Exception as e:
            log(f'撤限价单异常: {e}')
        try:
            import requests as rq, hmac as hm, hashlib as hl, urllib.parse as up
            BASE = 'https://fapi.binance.com'
            def signed(params):
                params['timestamp'] = int(time.time() * 1000)
                q = up.urlencode(params)
                params['signature'] = hm.new(API_SECRET.encode(), q.encode(), hl.sha256).hexdigest()
                return params
            p = signed({'symbol': raw_symbol})
            hd = {'X-MBX-APIKEY': API_KEY}
            for o in rq.get(f'{BASE}/fapi/v1/openAlgoOrders?{up.urlencode(p)}', headers=hd).json():
                p2 = signed({'symbol': raw_symbol, 'algoId': o['algoId']})
                rq.delete(f'{BASE}/fapi/v1/algoOrder?{up.urlencode(p2)}', headers=hd)
                log(f'撤条件单: {o["algoId"]}')
        except Exception as e:
            log(f'撤条件单异常: {e}')

    def has_open_order(self, direction):
        for o in self.ex.fetch_open_orders(SYMBOL):
            ps = o.get('info', {}).get('positionSide', '').upper() if isinstance(o.get('info'), dict) else ''
            side = 'BUY' if direction == 'LONG' else 'SELL'
            if o['side'].upper() == side and (not ps or ps == direction):
                return True
        return False


    def update_order_if_stale(self, plan):
        """如果入场价变动超过0.3xATR，撤旧挂新"""
        try:
            orders = self.ex.fetch_open_orders(SYMBOL)
            if not orders:
                return False
            new_entry = plan['entry']
            atr = plan.get('atr', 0)
            threshold = 0.3 * atr  # ATR自适应阈值
            for o in orders:
                old_price = float(o['price'])
                change_pct = abs(new_entry - old_price) / old_price * 100
                if abs(new_entry - old_price) > threshold:
                    log(f'入场价变动: {old_price:.3f}->{new_entry:.3f} ({change_pct:.2f}% > {threshold/new_entry*100:.2f}%阈值), 撤旧挂新')
                    self.cancel_all_orders()
                    self.open_position(plan)
                    return True
            return False
        except Exception as e:
            log(f'update_order_if_stale异常: {e}')
            return False

    def close_position(self, position_side):
        try:
            pos = self.get_any_position()
            if not pos:
                return
            info = pos.get('info', {})
            amt = abs(float(info.get('positionAmt', 0)))
            if amt <= 0:
                return
            side = 'BUY' if position_side == 'SHORT' else 'SELL'
            self.ex.create_order(SYMBOL, 'market', side.lower(), amt, None, params={'positionSide': position_side})
            log(f'平仓: {position_side} {amt} NEAR')
            cr = {
                'action': 'CLOSE',
                'symbol': 'NEAR/USDT',
                'direction': position_side,
                'qty': amt,
                'entry_price': round(float(pos['entryPrice']), 3),
                'close_reason': 'signal_reversal',
                'upnl': round(float(pos.get('unrealizedPnl', 0)), 4),
            }
            log_trade(cr)
        except Exception as e:
            log(f'平仓异常: {e}')

    def open_position(self, plan):
        d = plan['direction']
        side = 'sell' if d == 'SHORT' else 'buy'
        entry = plan['entry']
        sl = plan['sl']
        tp = plan['tp']
        qty = POSITION_SIZE

        log(f'开{d}: 限价 {entry:.3f} SL={sl:.3f} TP={tp:.3f} qty={qty}')

        trade_record = {
            'action': 'OPEN',
            'symbol': 'NEAR/USDT',
            'direction': d,
            'qty': qty,
            'leverage': LEVERAGE,
            'entry_price': round(entry, 3),
            'entry_type': plan.get('entry_name', ''),
            'sl': round(sl, 3),
            'sl_pct': round(abs(sl - entry) / entry * 100, 2),
            'tp': round(tp, 3),
            'tp_pct': round(abs(entry - tp) / entry * 100, 2),
            'analysis': plan.get('analysis', {}),
        }
        log_trade(trade_record)

        try:
            self.ex.set_leverage(LEVERAGE, SYMBOL)
            order = self.ex.create_order(SYMBOL, 'limit', side, qty, entry, params={'positionSide': d})
            log(f'限价单: {order["id"]} {side} {qty} @ {entry:.3f}')
            self._pending_plan = {
                'order_id': order['id'],
                'direction': d,
                'sl': sl,
                'tp': tp,
                'qty': qty,
                'entry_price': round(entry, 3),
            }
        except Exception as e:
            log(f'开仓异常: {e}')

    def ensure_sl_tp(self):
        if not self._pending_plan:
            return
        plan = self._pending_plan
        try:
            order = self.ex.fetch_order(plan['order_id'], SYMBOL)
            if order['status'] != 'closed':
                return
            d = plan['direction']
            sl_p = round(plan['sl'], 1)
            tp_p = round(plan['tp'], 1)
            qty = plan['qty']
            cs = 'buy' if d == 'SHORT' else 'sell'
            log(f'成交! 挂SL/TP: {d} SL={sl_p:.3f} TP={tp_p:.3f}')
            d_cn = '做空' if d == 'SHORT' else '做多'
            fill_ts = None
            if order.get('lastTradeTimestamp'):
                from datetime import datetime as dt2
                fill_ts = dt2.fromtimestamp(order['lastTradeTimestamp']/1000).strftime('%Y-%m-%d %H:%M:%S')
            elif order.get('datetime'):
                fill_ts = order['datetime'][:19].replace('T', ' ')
            log_trade({
                'action': 'FILLED', 'direction': d, 'qty': qty,
                'entry_price': round(float(order.get('price', plan.get('entry_price', 0))), 3) if order.get('price') else round(plan.get('entry_price', 0), 3),
                '_timestamp': fill_ts,
            })
            self.ex.create_order(SYMBOL, 'STOP_MARKET', cs, qty, None, params={'stopPrice': sl_p, 'positionSide': d})
            self.ex.create_order(SYMBOL, 'TAKE_PROFIT_MARKET', cs, qty, None, params={'stopPrice': tp_p, 'positionSide': d})
            log('SL/TP 已挂载')
            self._pending_plan = None
        except Exception as e:
            err = str(e)
            if '-4045' in err:
                log(f'SL/TP: 检测到订单限制(-4045)，清理所有algo订单...')
                try:
                    import requests as rq2, hmac as hm2, hashlib as hl2, urllib.parse as up2
                    BASE2 = 'https://fapi.binance.com'
                    def sign2(params):
                        params['timestamp'] = int(time.time() * 1000)
                        q2 = up2.urlencode(params)
                        params['signature'] = hm2.new(API_SECRET.encode(), q2.encode(), hl2.sha256).hexdigest()
                        return params
                    p2 = sign2({'symbol': 'NEARUSDT'})
                    hd2 = {'X-MBX-APIKEY': API_KEY}
                    all_algos = rq2.get(f'{BASE2}/fapi/v1/openAlgoOrders?{up2.urlencode(p2)}', headers=hd2).json()
                    if isinstance(all_algos, list):
                        for aa in all_algos:
                            try:
                                p3 = sign2({'symbol': 'NEARUSDT', 'algoId': aa['algoId']})
                                rq2.delete(f'{BASE2}/fapi/v1/algoOrder?{up2.urlencode(p3)}', headers=hd2)
                            except: pass
                        log(f'已清理{len(all_algos)}个algo订单，重试挂SL/TP...')
                        time.sleep(1)
                        self.ex.create_order(SYMBOL, 'STOP_MARKET', cs, qty, None, params={'stopPrice': sl_p, 'positionSide': d})
                        self.ex.create_order(SYMBOL, 'TAKE_PROFIT_MARKET', cs, qty, None, params={'stopPrice': tp_p, 'positionSide': d})
                        log('SL/TP 已挂载(重试)')
                        self._pending_plan = None
                        return
                except Exception as e2:
                    log(f'SL/TP重试也失败: {e2}')
            log(f'SL/TP异常: {e}')

    def ensure_naked_sl_tp(self):
        try:
            import requests as rq, hmac as hm, hashlib as hl, urllib.parse as up
            BASE = 'https://fapi.binance.com'

            def signed(params):
                params['timestamp'] = int(time.time() * 1000)
                q = up.urlencode(params)
                params['signature'] = hm.new(API_SECRET.encode(), q.encode(), hl.sha256).hexdigest()
                return params

            p = signed({'symbol': 'NEARUSDT'})
            hd = {'X-MBX-APIKEY': API_KEY}
            active_algos = rq.get(f'{BASE}/fapi/v1/openAlgoOrders?{up.urlencode(p)}', headers=hd).json()

            for pos in self.ex.fetch_positions([SYMBOL]):
                info = pos.get('info', {})
                amt = float(info.get('positionAmt', 0))
                if amt == 0:
                    continue
                d = info.get('positionSide', '')
                qty = abs(amt)
                ep = float(pos['entryPrice'])

                if d == 'SHORT':
                    has_sl = any(float(o.get('triggerPrice', 0)) > ep and abs(float(o.get('quantity', 0)) - qty) < 0.01 for o in active_algos)
                    has_tp = any(float(o.get('triggerPrice', 0)) < ep and abs(float(o.get('quantity', 0)) - qty) < 0.01 for o in active_algos)
                else:
                    has_sl = any(float(o.get('triggerPrice', 0)) < ep and abs(float(o.get('quantity', 0)) - qty) < 0.01 for o in active_algos)
                    has_tp = any(float(o.get('triggerPrice', 0)) > ep and abs(float(o.get('quantity', 0)) - qty) < 0.01 for o in active_algos)

                if has_sl and has_tp:
                    continue

                d_cn = '做空' if d == 'SHORT' else '做多'
                log_trade({
                    'action': 'FILLED', 'direction': d, 'qty': qty,
                    'entry_price': round(ep, 3),
                })
                log(f'裸仓: {d} {qty}NEAR 补SL/TP...')

                raw = self.ex.fetch_ohlcv(SYMBOL, '4h', limit=60)
                df = pd.DataFrame(raw, columns=['ts', 'o', 'h', 'l', 'c', 'v'])
                tr = pd.concat([df['h'] - df['l'], abs(df['h'] - df['c'].shift(1)), abs(df['l'] - df['c'].shift(1))], axis=1).max(axis=1)
                atr = tr.rolling(14).mean().iloc[-1]
                r30 = df.tail(30)
                lo = r30['l'].min()
                hi = r30['h'].max()

                if d == 'SHORT':
                    sl_p = round(ep + SL_ATR_MULT * atr, 1)
                    tp_p = round(lo, 1)
                else:
                    sl_p = round(ep - SL_ATR_MULT * atr, 1)
                    tp_p = round(hi, 1)

                cs = 'buy' if d == 'SHORT' else 'sell'
                self.ex.create_order(SYMBOL, 'STOP_MARKET', cs, qty, None, params={'stopPrice': sl_p, 'positionSide': d})
                self.ex.create_order(SYMBOL, 'TAKE_PROFIT_MARKET', cs, qty, None, params={'stopPrice': tp_p, 'positionSide': d})
                log(f'裸仓已保护: SL={sl_p:.3f} TP={tp_p:.3f}')
                # 反查: 有条件单但无对应持仓 → 清理孤儿
                for a in active_algos:
                    a_qty = float(a.get('quantity', 0))
                    has_match = False
                    for pos2 in self.ex.fetch_positions([SYMBOL]):
                        if abs(abs(float(pos2.get('info',{}).get('positionAmt',0)))-a_qty) < 0.01:
                            has_match = True
                            break
                    if not has_match:
                        try:
                            p3 = signed({'symbol': 'NEARUSDT', 'algoId': a['algoId']})
                            rq.delete(f'{BASE}/fapi/v1/algoOrder?{up.urlencode(p3)}', headers=hd)
                            log(f'孤儿清理: {a["algoId"]}')
                        except: pass
        except Exception as e:
            err = str(e)
            if '-4045' in err:
                log(f'检测到订单限制(-4045)，清理所有algo订单...')
                try:
                    p2 = signed({'symbol': 'NEARUSDT'})
                    hd2 = {'X-MBX-APIKEY': API_KEY}
                    all_algos = rq.get(f'{BASE}/fapi/v1/openAlgoOrders?{up.urlencode(p2)}', headers=hd2).json()
                    if isinstance(all_algos, list):
                        for aa in all_algos:
                            try:
                                p3 = signed({'symbol': 'NEARUSDT', 'algoId': aa['algoId']})
                                rq.delete(f'{BASE}/fapi/v1/algoOrder?{up.urlencode(p3)}', headers=hd2)
                            except: pass
                        log(f'已清理{len(all_algos)}个algo订单，重试挂SL/TP...')
                        time.sleep(1)
                        self.ex.create_order(SYMBOL, 'STOP_MARKET', cs, qty, None, params={'stopPrice': sl_p, 'positionSide': d})
                        self.ex.create_order(SYMBOL, 'TAKE_PROFIT_MARKET', cs, qty, None, params={'stopPrice': tp_p, 'positionSide': d})
                        log(f'裸仓已保护(重试): SL={sl_p:.3f} TP={tp_p:.3f}')
                        return
                except Exception as e2:
                    log(f'裸仓重试也失败: {e2}')
            log(f'裸仓异常: {e}')

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}

def save_state(s):
    with open(STATE_FILE, 'w') as f:
        json.dump(s, f, indent=2, default=str)

def main():
    log('══════ NEAR自动交易 启动 (币安 10x) ══════')
    log(f'品种: {SYMBOL}  仓位: {POSITION_SIZE} NEAR  轮询: {POLL_SECONDS}s')

    exchange = ccxt.binance({
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'options': {'defaultType': 'future'},
    })
    exchange.load_markets()

    analyzer = Analyzer(exchange)
    executor = Executor(exchange)
    state = load_state()

    # 启动时清理：有持仓则撤所有限价单，裸仓补SL/TP
    try:
        pos_check = executor.get_any_position()
        if pos_check:
            # 只撤限价单，不碰SL/TP(否则ensure_naked会重复写FILLED)
            for o in executor.ex.fetch_open_orders(SYMBOL):
                if o.get('type', '') not in ('STOP_MARKET', 'TAKE_PROFIT_MARKET', 'LIMIT_STOP_MARKET', 'LIMIT_TAKE_PROFIT_MARKET'):
                    executor.ex.cancel_order(o['id'], SYMBOL)
            log('启动清理: 撤残留限价单')
        executor.ensure_naked_sl_tp()
    except Exception as e:
        log(f'启动清理异常: {e}')

    while True:
        try:
            t0 = time.time()
            analyzer.fetch()
            plan = analyzer.plan()

            direction = plan["direction"] if plan else None
            price = analyzer.price
            h4 = analyzer.data['4h'].iloc[-1]

            log(f'── 价格:{price:.3f} 方向:{direction or "观望"} ADX:{h4["adx"]:.0f} +DI:{h4["plus_di"]:.0f} -DI:{h4["minus_di"]:.0f}')

            executor.ensure_sl_tp()
            executor.ensure_naked_sl_tp()

            # 方向消失 → 撤所有限价单
            if not direction:
                open_orders = executor.ex.fetch_open_orders(SYMBOL)
                for o in open_orders:
                    if o.get('type', '') not in ('STOP_MARKET', 'TAKE_PROFIT_MARKET'):
                        executor.ex.cancel_order(o['id'], SYMBOL)
                        log(f'方向消失 → 撤限价单: {o["id"]}')

            pos = executor.get_any_position()
            if pos and direction:
                ps = pos.get('info', {}).get('positionSide', '')
                if ps.upper() != direction:
                    log(f'方向反转: {ps}->{direction}')
                    executor.cancel_all_sl_tp()
                    executor.close_position(ps.upper())

            if direction and not executor.has_position(direction) and plan:
                if executor.has_open_order(direction):
                    if executor.update_order_if_stale(plan):
                        state['last_signal'] = direction
                        save_state(state)
                else:
                    # LLM二次确认: 直接调DeepSeek API
                    try:
                        from llm_client import analyze as llm_analyze
                        from llm_review import _write_trade_log
                        import json as _json
                
                        enrich = {}
                        epath = os.path.join(SCRIPT_DIR, 'market_enrich.json')
                        if os.path.exists(epath):
                            with open(epath) as _f:
                                edata = _json.load(_f)
                                enrich = edata.get('coins', {}).get('NEAR', {})
                
                        indicators = {'price': price, 'atr': h4.get('atr',0),
                                       'atr_pct': h4.get('atr_pct',0),
                                       'raw': f'ADX={float(h4.get("adx",0)):.0f} +DI={float(h4.get("plus_di",0)):.0f} -DI={float(h4.get("minus_di",0)):.0f} price={price:.3f} ATR={float(h4.get("atr_pct",0)):.1f}%'}
                
                        decision, reason = llm_analyze(
                            'NEAR', direction, plan['entry'], plan['sl'],
                            plan['tp'], POSITION_SIZE, LEVERAGE, indicators, enrich
                        )
                
                        if decision == 'CONFIRMED':
                            log(f'LLM确认: {reason[:60]}')
                            _write_trade_log('NEAR', 'CONFIRMED', reason, {'direction': direction, 'entry_price': plan['entry'], 'stop_loss': plan['sl'], 'take_profit': plan['tp'], 'qty': POSITION_SIZE, 'leverage': LEVERAGE})
                            executor.open_position(plan)
                            state['last_signal'] = direction
                            save_state(state)
                        else:
                            log(f'LLM否决: {reason[:60]}')
                            _write_trade_log('NEAR', 'REJECTED', reason, {'direction': direction, 'entry_price': plan['entry'], 'stop_loss': plan['sl'], 'take_profit': plan['tp'], 'qty': POSITION_SIZE, 'leverage': LEVERAGE})
                    except Exception as e:
                        log(f'LLM异常({e})，降级直接开仓')
                        executor.open_position(plan)
                        state['last_signal'] = direction
                        save_state(state)
            elapsed = time.time() - t0
            # 5. 等待——拆成30s小段，快速检测限价单成交
            remaining = POLL_SECONDS - elapsed
            while remaining > 0:
                chunk = min(30, remaining)
                time.sleep(chunk)
                executor.ensure_sl_tp()
                remaining -= chunk

        except KeyboardInterrupt:
            log('退出')
            break
        except Exception as e:
            log(f'异常: {e}')
            traceback.print_exc()
            time.sleep(30)

if __name__ == '__main__':
    main()
