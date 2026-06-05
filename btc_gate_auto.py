#!/usr/bin/env python3
"""
BTC 全自动交易机器人 - Gate.io 版
每5分钟轮询，多周期EMA+ADX+DI动态判方向，
阻力/支撑共振区入场，ATR自适应止损，前低/前高结构止盈。
Gate交易所适配层，策略逻辑与Binance版完全一致。
"""

import ccxt
import pandas as pd
import numpy as np
import time
import json
import os
import sys
import traceback
from datetime import datetime
from gate_config import GATE_API_KEY, GATE_API_SECRET

# ── 配置 ──────────────────────────────────────────

SYMBOL = 'BTC/USDT:USDT'
EXCHANGE = 'gate'
LEVERAGE = 10
MARGIN_PER_TRADE = 15          # 单笔保证金 USDT
POSITION_SIZE = 100            # 合约张数 (0.0001 BTC/张 = 0.01 BTC)

TIMEFRAMES = ['1h', '4h', '1d']
SL_ATR_MULT = 1.5
FIB_LEVELS = [0.236, 0.382]
EMA_SHORT = 5
EMA_LONG = 10
MIN_ADX = 25
DI_RATIO = 1.5

POLL_SECONDS = 300             # 5分钟

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, 'btc_gate_state.json')
LOG_FILE = os.path.join(SCRIPT_DIR, 'btc_gate.log')
TRADE_LOG = os.path.join(SCRIPT_DIR, 'btc_gate_trades.txt')

# ── API 密钥 ──────────────────────────────────────

API_KEY = GATE_API_KEY
API_SECRET = GATE_API_SECRET


# ── 日志 (与Binance版完全一致) ────────────────────

def log(msg: str):
    ts = datetime.now().strftime('%H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line, flush=True)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')

def log_trade(entry: dict):
    """写入中文交易日志"""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    action = entry.get('action')
    d = entry.get('direction', '')
    d_cn = '做空' if d == 'SHORT' else ('做多' if d == 'LONG' else d)
    qty = entry.get('qty')
    lev = entry.get('leverage', LEVERAGE)

    lines = []
    lines.append(f'═══════════════════════════════════')
    lines.append(f'时间: {ts}')

    if action == 'OPEN':
        lines.append(f'操作: 开仓{d_cn}')
        lines.append(f'数量: {qty} BTC | 杠杆: {lev}x')
        lines.append(f'入场价: {entry.get("entry_price", "?")} USDT ({entry.get("entry_type", "")})')
        lines.append(f'止损: {entry.get("sl", "?")} USDT (-{entry.get("sl_pct", "?")}%)')
        lines.append(f'止盈: {entry.get("tp", "?")} USDT (+{entry.get("tp_pct", "?")}%)')
    elif action == 'CANCEL':
        cancel_id = entry.get('order_id', '')
        lines += [
            f'操作: 取消挂单{d_cn}',
            f'数量: {entry.get("qty")} 订单ID: {cancel_id}',
            f'挂单价: {entry.get("entry_price")} USDT',
            f'原因: {entry.get("cancel_reason", "signal_change")}',
        ]
    elif action == 'CLOSE':
        lines.append(f'操作: 平仓{d_cn}')
        lines.append(f'数量: {qty} BTC')
        lines.append(f'开仓价: {entry.get("entry_price", "?")} USDT')
        lines.append(f'平仓原因: {entry.get("close_reason", "")}')
        lines.append(f'盈亏: {entry.get("upnl", "?")} USDT')

    lines.append('── 分析依据 ──')
    analysis = entry.get('analysis', {})
    if analysis:
        tfs = analysis.get('timeframes', {})
        for tf_name, tf_data in tfs.items():
            al = tf_data.get('alignment', '')
            al_cn = '多头排列' if al == 'bull' else ('空头排列' if al == 'bear' else '持平')
            lines.append(f'  {tf_name} EMA5={tf_data.get("ema5","?")} EMA10={tf_data.get("ema10","?")} → {al_cn}')

        h4 = analysis.get('4h', {})
        if h4:
            lines.append(f'  4h ADX={h4.get("adx","?")} {"强趋势" if h4.get("adx",0)>25 else "震荡"} '
                        f'+DI={h4.get("plus_di","?")} -DI={h4.get("minus_di","?")}')
            lines.append(f'  4h ATR={h4.get("atr","?")} USDT ({h4.get("atr_pct","?")}%)')

        rng = analysis.get('recent_range', {})
        if rng:
            lines.append(f'  近期区间: {rng.get("high","?")} ~ {rng.get("low","?")} (波幅 {rng.get("range_pct","?")}%)')

        levels = analysis.get('key_levels', [])
        if levels:
            lines.append(f'  关键价位:')
            for lv in levels:
                typ_cn = {'resistance': '阻力', 'support': '支撑', 'broken': '已破位'}.get(lv.get('type',''), lv.get('type',''))
                lines.append(f'    {lv.get("name","")}: {lv.get("level","?")} USDT ({typ_cn})')

        rationale = analysis.get('direction_rationale', {})
        if rationale:
            lines.append(f'  方向判定: {rationale.get("conclusion","?")}')
            for r in rationale.get('reasons', []):
                lines.append(f'    • {r}')

    lines.append(f'═══════════════════════════════════')
    lines.append('')

    text = '\n'.join(lines)
    with open(TRADE_LOG, 'a') as f:
        f.write(text)
    log(f'📝 交易日志已写入')


# ── 指标 (与Binance版完全一致) ────────────────────

def compute(df: pd.DataFrame) -> pd.DataFrame:
    df['ema5'] = df['close'].ewm(span=EMA_SHORT).mean()
    df['ema10'] = df['close'].ewm(span=EMA_LONG).mean()
    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift(1)),
        abs(df['low'] - df['close'].shift(1))
    ], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    atr = df['atr']
    up = df['high'] - df['high'].shift(1)
    down = df['low'].shift(1) - df['low']
    pdm = pd.Series(np.where((up > down) & (up > 0), up, 0), index=df.index)
    ndm = pd.Series(np.where((down > up) & (down > 0), down, 0), index=df.index)
    pdi = 100 * pdm.rolling(14).mean() / atr.replace(0, np.nan)
    ndi = 100 * ndm.rolling(14).mean() / atr.replace(0, np.nan)
    dx = 100 * abs(pdi - ndi) / (pdi + ndi).replace(0, np.nan)
    df['adx'] = dx.rolling(14).mean()
    df['plus_di'] = pdi
    df['minus_di'] = ndi
    return df


# ── 分析器 (与Binance版完全一致) ──────────────────

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

    def direction(self) -> str | None:
        h1 = self.data['1h']; h4 = self.data['4h']
        d1 = self.data.get('1d')
        last4 = h4.iloc[-1]

        ema_bear = sum(1 for df in ([h1, h4, d1] if d1 is not None and len(d1) > 0 else [h1, h4])
                       if df['ema5'].iloc[-1] < df['ema10'].iloc[-1])
        ema_bull = sum(1 for df in ([h1, h4, d1] if d1 is not None and len(d1) > 0 else [h1, h4])
                       if df['ema5'].iloc[-1] > df['ema10'].iloc[-1])
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

    def plan(self) -> dict | None:
        d = self.direction()
        if d is None:
            return None
        price = self.price
        df4 = self.data['4h']
        last4 = df4.iloc[-1]
        r30 = df4.tail(30)
        hi, lo = r30['high'].max(), r30['low'].min()
        atr = last4['atr']

        if d == 'SHORT':
            # 找上方阻力共振
            levels = []
            for v, name in [(last4['ema5'], '4h_EMA5'), (last4['ema10'], '4h_EMA10')]:
                if pd.notna(v) and v > price:
                    levels.append((v, name, 3))
            for fib in FIB_LEVELS:
                lvl = lo + (hi - lo) * fib
                if lvl > price:
                    levels.append((lvl, f'Fib_{fib:.1%}', 1))
            levels.sort()
            entry = levels[0][0] if levels else price
            entry_name = levels[0][1] if levels else 'market'
            sl = entry + SL_ATR_MULT * atr
            tp = lo
        else:  # LONG
            levels = []
            for v, name in [(last4['ema5'], '4h_EMA5'), (last4['ema10'], '4h_EMA10')]:
                if pd.notna(v) and v < price:
                    levels.append((v, name, 3))
            for fib in FIB_LEVELS:
                lvl = hi - (hi - lo) * fib
                if lvl < price:
                    levels.append((lvl, f'Fib_{fib:.1%}', 1))
            levels.sort(reverse=True)
            entry = levels[0][0] if levels else price
            entry_name = levels[0][1] if levels else 'market'
            sl = entry - SL_ATR_MULT * atr
            tp = hi

        return {
            'direction': d,
            'entry': entry,
            'entry_name': entry_name,
            'sl': sl,
            'tp': tp,
            'atr': atr,
            'price': price,
            'analysis': self._analysis_detail(d),
        }

    def _analysis_detail(self, direction: str) -> dict:
        """生成详细分析数据，供交易日志记录"""
        h1 = self.data['1h']
        h4 = self.data['4h']
        last1 = h1.iloc[-1]
        last4 = h4.iloc[-1]
        r30 = h4.tail(30)
        hi, lo = r30['high'].max(), r30['low'].min()

        # 收集所有EMA
        ema_data = {}
        for tf, df, name in [('1h', h1, '1h'), ('4h', h4, '4h')]:
            l = df.iloc[-1]
            ema_data[name] = {
                'ema5': round(float(l['ema5']), 1) if pd.notna(l['ema5']) else None,
                'ema10': round(float(l['ema10']), 1) if pd.notna(l['ema10']) else None,
                'alignment': 'bull' if (pd.notna(l['ema5']) and pd.notna(l['ema10']) and l['ema5'] > l['ema10']) else 'bear',
            }
        if '1d' in self.data:
            d1 = self.data['1d']
            if len(d1) > 0:
                ld = d1.iloc[-1]
                ema_data['1d'] = {
                    'ema5': round(float(ld['ema5']), 1) if pd.notna(ld['ema5']) else None,
                    'ema10': round(float(ld['ema10']), 1) if pd.notna(ld['ema10']) else None,
                    'alignment': 'bull' if (pd.notna(ld['ema5']) and pd.notna(ld['ema10']) and ld['ema5'] > ld['ema10']) else 'bear',
                }

        # 阻力/支撑位
        levels = []
        if direction == 'SHORT':
            for name, v in [('4h_EMA5', last4['ema5']), ('4h_EMA10', last4['ema10'])]:
                if pd.notna(v):
                    levels.append({'level': round(float(v), 1), 'name': name, 'type': 'resistance' if v > self.price else 'broken'})
            for fib in FIB_LEVELS:
                lvl = lo + (hi - lo) * fib
                levels.append({'level': round(lvl, 1), 'name': f'Fib_{fib:.1%}', 'type': 'resistance' if lvl > self.price else 'broken'})
        else:
            for name, v in [('4h_EMA5', last4['ema5']), ('4h_EMA10', last4['ema10'])]:
                if pd.notna(v):
                    levels.append({'level': round(float(v), 1), 'name': name, 'type': 'support' if v < self.price else 'broken'})
            for fib in FIB_LEVELS:
                lvl = hi - (hi - lo) * fib
                levels.append({'level': round(lvl, 1), 'name': f'Fib_{fib:.1%}', 'type': 'support' if lvl < self.price else 'broken'})

        return {
            'price': round(float(self.price), 1),
            'timeframes': ema_data,
            '4h': {
                'adx': round(float(last4['adx']), 1) if pd.notna(last4['adx']) else None,
                'plus_di': round(float(last4['plus_di']), 1) if pd.notna(last4['plus_di']) else None,
                'minus_di': round(float(last4['minus_di']), 1) if pd.notna(last4['minus_di']) else None,
                'atr': round(float(last4['atr']), 1) if pd.notna(last4['atr']) else None,
                'atr_pct': round(float(last4['atr'] / self.price * 100), 2) if pd.notna(last4['atr']) else None,
            },
            'recent_range': {'high': round(float(hi), 1), 'low': round(float(lo), 1), 'range_pct': round(float((hi - lo) / lo * 100), 2)},
            'key_levels': levels,
            'direction_rationale': self._direction_rationale(),
        }

    def _direction_rationale(self) -> dict:
        """方向判定的逐条理由"""
        rationale = []
        for tf_name, df in [('1h', self.data['1h']), ('4h', self.data['4h'])]:
            l = df.iloc[-1]
            if pd.notna(l['ema5']) and pd.notna(l['ema10']):
                if l['ema5'] > l['ema10']:
                    rationale.append(f'{tf_name} EMA多头排列(EMA5={l["ema5"]:.0f}>EMA10={l["ema10"]:.0f})')
                else:
                    rationale.append(f'{tf_name} EMA空头排列(EMA5={l["ema5"]:.0f}<EMA10={l["ema10"]:.0f})')

        if '1d' in self.data and len(self.data['1d']) > 0:
            ld = self.data['1d'].iloc[-1]
            if pd.notna(ld['ema5']) and pd.notna(ld['ema10']):
                if ld['ema5'] > ld['ema10']:
                    rationale.append(f'1d EMA多头排列(EMA5={ld["ema5"]:.0f}>EMA10={ld["ema10"]:.0f})')
                else:
                    rationale.append(f'1d EMA空头排列(EMA5={ld["ema5"]:.0f}<EMA10={ld["ema10"]:.0f})')

        l4 = self.data['4h'].iloc[-1]
        rationale.append(f'4h ADX={l4["adx"]:.0f} {"强趋势" if l4["adx"]>25 else "弱趋势/震荡"}')
        rationale.append(f'4h +DI={l4["plus_di"]:.0f} -DI={l4["minus_di"]:.0f} '
                        f'→ {"-DI碾压" if l4["minus_di"]>l4["plus_di"]*DI_RATIO else "+DI碾压" if l4["plus_di"]>l4["minus_di"]*DI_RATIO else "DI胶着"}')

        return {'reasons': rationale, 'conclusion': self.direction()}


# ═══════════════════════════════════════════════════
# 以下为 Gate.io 交易所适配层
# 除交易所API差异外，逻辑与Binance版完全一致
# ═══════════════════════════════════════════════════

# ── 交易执行器 ────────────────────────────────────

class Executor:
    def __init__(self, exchange):
        self.ex = exchange
        self._pending_plan = None

    def _get_pos_side(self, pos):
        """Gate: 双向持仓用 info.mode (dual_long/dual_short → LONG/SHORT)"""
        info = pos.get('info', {})
        if isinstance(info, dict):
            m = info.get('mode', '')
            if m:
                m = m.upper()
                if 'SHORT' in m:
                    return 'SHORT'
                if 'LONG' in m:
                    return 'LONG'
        return ''

    def has_position(self, direction: str) -> bool:
        for p in self.ex.fetch_positions([SYMBOL]):
            if float(p.get('contracts', 0)) > 0:
                ps = self._get_pos_side(p)
                if ps == direction:
                    return True
        return False

    def get_any_position(self) -> dict | None:
        for p in self.ex.fetch_positions([SYMBOL]):
            if float(p.get('contracts', 0)) > 0:
                return p
        return None

    def cancel_all_sl_tp(self):
        self.cancel_all_orders()

    def cancel_all_orders(self):
        try:
            for o in self.ex.fetch_open_orders(SYMBOL):
                self.ex.cancel_order(o['id'], SYMBOL)
                log(f'撤单: {o["id"][:16]}')
        except Exception as e:
            log(f'撤单异常: {e}')
        try:
            for o in self.ex.fetch_open_orders(SYMBOL, params={'trigger': True}):
                try:
                    self.ex.cancel_order(o['id'], SYMBOL, params={'trigger': True})
                except:
                    pass
        except:
            pass

    def has_open_order(self, direction):
        side = 'buy' if direction == 'LONG' else 'sell'
        for o in self.ex.fetch_open_orders(SYMBOL):
            if o['side'] == side and not (o.get('reduceOnly') or o.get('reduce_only')):
                return True
        return False

    def update_order_if_stale(self, plan):
        try:
            orders = self.ex.fetch_open_orders(SYMBOL)
            if not orders:
                return False
            new_entry = plan['entry']
            atr = plan.get('atr', 0)
            threshold = 0.3 * atr
            for o in orders:
                if o.get('reduceOnly') or o.get('reduce_only'):
                    continue
                old_price = float(o['price'])
                if abs(new_entry - old_price) > threshold:
                    log(f'入场价变动: {old_price:.3f}->{new_entry:.3f}, 撤旧挂新')
                    self.cancel_all_orders()
                    self.open_position(plan)
                    return True
            return False
        except Exception as e:
            log(f'update_order_if_stale异常: {e}')
            return False

    def close_position(self, position_side: str):
        """市价平仓"""
        try:
            pos = self.get_any_position()
            if not pos:
                return
            amt = abs(float(pos.get('contracts', 0)))
            if amt <= 0:
                return
            side = 'buy' if position_side == 'SHORT' else 'sell'
            self.ex.create_order(SYMBOL, 'market', side, amt, None, params={'reduceOnly': True})
            log(f'平仓: {position_side} {amt}张')
            close_record = {
                'action': 'CLOSE', 'direction': position_side, 'qty': amt,
                'entry_price': round(float(pos['entryPrice']), 1) if pos.get('entryPrice') else None,
                'close_reason': 'signal_reversal',
                'upnl': round(float(pos.get('unrealizedPnl', 0)), 4),
            }
            log_trade(close_record)
        except Exception as e:
            log(f'平仓异常: {e}')

    def open_position(self, plan: dict):
        d = plan['direction']
        side = 'sell' if d == 'SHORT' else 'buy'
        entry = plan['entry']
        sl = plan['sl']
        tp = plan['tp']
        qty = POSITION_SIZE

        log(f'开{d}: 限价 {entry:.0f}  SL={sl:.0f}  TP={tp:.0f}  qty={qty}')

        trade_record = {
            'action': 'OPEN', 'direction': d, 'qty': qty,
            'leverage': LEVERAGE,
            'entry_price': round(entry, 1),
            'entry_type': plan.get('entry_name', 'limit'),
            'sl': round(sl, 1),
            'sl_pct': round(abs(sl - entry) / entry * 100, 2),
            'tp': round(tp, 1),
            'tp_pct': round(abs(entry - tp) / entry * 100, 2),
            'analysis': plan.get('analysis', {}),
        }
        log_trade(trade_record)

        try:
            self.ex.set_leverage(LEVERAGE, SYMBOL)
            order = self.ex.create_order(SYMBOL, 'limit', side, qty, entry)
            log(f'限价单: {order["id"]} {side} {qty} @ {entry:.0f}')
            self._pending_plan = {
                'order_id': order['id'],
                'direction': d,
                'sl': sl,
                'tp': tp,
                'qty': qty,
                'trade_record': trade_record,
            }
        except Exception as e:
            log(f'开仓异常: {e}')

    # ── 移动止盈状态管理 ──

    def _write_trail_state(self, plan):
        try:
            coin = SYMBOL.split("/")[0].lower()
            entry_p = plan.get("entry", plan.get("entry_price", 0))
            atr = plan.get("atr", 0)
            state = {
                "active": True, "direction": plan["direction"],
                "entry_price": entry_p, "dynamic_tp": plan["tp"],
                "position_size": plan.get("qty", POSITION_SIZE),
                "peak_pnl": 0,
                "min_profit": max(1.5 * atr / entry_p * 100, 2.0) if entry_p > 0 else 2.0,
                "sl": plan["sl"],
            }
            path = os.path.join(SCRIPT_DIR, f"{coin}_trail_state.json")
            with open(path, "w") as _f:
                json.dump(state, _f)
        except:
            pass

    def _update_trail_tp(self, direction, new_tp):
        try:
            coin = SYMBOL.split("/")[0].lower()
            path = os.path.join(SCRIPT_DIR, f"{coin}_trail_state.json")
            if os.path.exists(path):
                with open(path) as _f:
                    state = json.load(_f)
                state["dynamic_tp"] = new_tp
                with open(path, "w") as _f:
                    json.dump(state, _f)
        except:
            pass

    def _clear_trail_state(self):
        try:
            coin = SYMBOL.split("/")[0].lower()
            path = os.path.join(SCRIPT_DIR, f"{coin}_trail_state.json")
            if os.path.exists(path):
                with open(path) as _f:
                    state = json.load(_f)
                state["active"] = False
                with open(path, "w") as _f:
                    json.dump(state, _f)
        except:
            pass

    def _get_current_tp(self, direction):
        """Gate: 获取当前止盈限价单价格"""
        cs = 'sell' if direction == 'LONG' else 'buy'
        for o in self.ex.fetch_open_orders(SYMBOL):
            if (o.get('reduceOnly') or o.get('reduce_only')) and o.get('type') == 'limit':
                if o.get('side') == cs:
                    return float(o.get('price', 0))
        return 0

    def _replace_tp(self, direction, qty, new_tp):
        """Gate: 取消旧TP limit单 + 挂新TP limit单"""
        cs = 'sell' if direction == 'LONG' else 'buy'
        for o in self.ex.fetch_open_orders(SYMBOL):
            if (o.get('reduceOnly') or o.get('reduce_only')) and o.get('type') == 'limit':
                if o.get('side') == cs:
                    try:
                        self.ex.cancel_order(o['id'], SYMBOL)
                    except:
                        pass
        self.ex.create_order(SYMBOL, 'limit', cs, qty, new_tp, params={'reduceOnly': True})

    def _create_sl_tp_orders(self, d, qty, sl_p, tp_p):
        """Gate: SL=market trigger reduceOnly, TP=limit reduceOnly"""
        close_side = 'buy' if d == 'SHORT' else 'sell'
        try:
            self.ex.create_order(SYMBOL, 'market', close_side, qty, None, params={
                'triggerPrice': sl_p, 'reduceOnly': True
            })
            log(f'止损已挂: {sl_p:.1f}')
        except Exception as e:
            log(f'挂止损异常: {e}')
        try:
            self.ex.create_order(SYMBOL, 'limit', close_side, qty, tp_p, params={
                'reduceOnly': True
            })
            log(f'止盈已挂: {tp_p:.1f}')
        except Exception as e:
            log(f'挂止盈异常: {e}')

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

            log(f'成交! 挂SL/TP: {d} SL={sl_p:.1f} TP={tp_p:.1f}')
            self._create_sl_tp_orders(d, qty, sl_p, tp_p)
            log('SL/TP 已挂载')
            self._pending_plan = None
            self._write_trail_state(plan)
        except Exception as e:
            log(f'SL/TP异常: {e}')

    def ensure_naked_sl_tp(self):
        try:
            for pos in self.ex.fetch_positions([SYMBOL]):
                size = float(pos.get('contracts', 0))
                if size == 0:
                    continue
                qty = abs(size)
                ep = float(pos['entryPrice']) if pos.get('entryPrice') else 0
                if ep <= 0:
                    continue

                d = self._get_pos_side(pos)
                if not d:
                    continue

                # 检查已有SL/TP
                has_sl = False; has_tp = False
                cs = 'sell' if d == 'LONG' else 'buy'
                for o in self.ex.fetch_open_orders(SYMBOL):
                    if o.get('reduceOnly') or o.get('reduce_only'):
                        if o.get('side') == cs:
                            if o.get('triggerPrice'):
                                has_sl = True
                            elif o.get('type') == 'limit':
                                has_tp = True
                for o in self.ex.fetch_open_orders(SYMBOL, params={'trigger': True}):
                    if (o.get('reduceOnly') or o.get('reduce_only')) and o.get('side') == cs:
                        has_sl = True
                if has_sl and has_tp:
                    continue

                self.cancel_all_sl_tp()
                log_trade({
                    'action': 'FILLED', 'direction': d, 'qty': qty,
                    'entry_price': round(ep, 3),
                })
                log(f'裸仓: {d} {qty}张 补SL/TP...')
                raw = self.ex.fetch_ohlcv(SYMBOL, '4h', limit=60)
                df = pd.DataFrame(raw, columns=['ts','o','h','l','c','v'])
                tr = pd.concat([df['h']-df['l'],abs(df['h']-df['c'].shift(1)),abs(df['l']-df['c'].shift(1))],axis=1).max(axis=1)
                atr_val = tr.rolling(14).mean().iloc[-1]
                r30 = df.tail(30); lo=r30['l'].min(); hi=r30['h'].max()
                if d == 'SHORT':
                    sl_p = round(ep+SL_ATR_MULT*atr_val,1); tp_p = round(lo,1)
                else:
                    sl_p = round(ep-SL_ATR_MULT*atr_val,1); tp_p = round(hi,1)
                self._create_sl_tp_orders(d, qty, sl_p, tp_p)
                log(f'裸仓已保护: SL={sl_p:.0f} TP={tp_p:.0f}')
        except Exception as e:
            log(f'裸仓异常: {e}')


# ── 状态 ──────────────────────────────────────────

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {'last_signal': None}

def save_state(s):
    with open(STATE_FILE, 'w') as f:
        json.dump(s, f, indent=2, default=str)


# ── Gate.io 初始化 ────────────────────────────────

def make_exchange():
    exchange = ccxt.gate({
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'options': {'defaultType': 'swap'},
    })
    exchange.load_markets()
    try:
        exchange.set_position_mode(True, SYMBOL)
    except:
        pass
    return exchange


# ── 主循环 (与Binance版逻辑完全一致) ──────────────

def main():
    log('══════ BTC自动交易 启动 (Gate.io 10x) ══════')
    log(f'品种: {SYMBOL}  仓位: {POSITION_SIZE}张  轮询: {POLL_SECONDS}s')

    exchange = make_exchange()
    analyzer = Analyzer(exchange)
    executor = Executor(exchange)
    state = load_state()

    # 启动清理
    try:
        pos_check = executor.get_any_position()
        if pos_check:
            for o in executor.ex.fetch_open_orders(SYMBOL):
                if not (o.get('reduceOnly') or o.get('reduce_only')):
                    executor.ex.cancel_order(o['id'], SYMBOL)
            log('启动清理: 撤残留限价单')
        executor.ensure_naked_sl_tp()
    except Exception as e:
        log(f'启动清理异常: {e}')

    while True:
        try:
            t0 = time.time()

            # 1. 分析
            analyzer.fetch()
            plan = analyzer.plan()

            direction = plan["direction"] if plan else None
            price = analyzer.price

            h4 = analyzer.data['4h'].iloc[-1]
            log(f'── 价格:{price:.0f} 方向:{direction or "观望"} '
                f'ADX:{h4["adx"]:.0f} +DI:{h4["plus_di"]:.0f} -DI:{h4["minus_di"]:.0f}')

            # ── 移动止盈管理 ──
            pos = executor.get_any_position()
            if pos and direction:
                pos_side = executor._get_pos_side(pos)
                entry_p = float(pos.get('entryPrice', 0) or 0)
                if entry_p > 0 and pos_side == direction:
                    pnl_pct = (price - entry_p) / entry_p * 100
                    if direction == "SHORT":
                        pnl_pct = -pnl_pct
                    atr_pct = float(h4["atr"]) / price * 100
                    MIN_PROFIT = max(1.5 * atr_pct, 2.0)
                    peak_key = f"peak_pnl_{direction}"
                    peak_pnl = state.get(peak_key, 0)
                    if pnl_pct > peak_pnl:
                        peak_pnl = pnl_pct
                        state[peak_key] = peak_pnl
                        save_state(state)
                    # 利润回撤50%止盈
                    if peak_pnl > MIN_PROFIT and pnl_pct < peak_pnl * 0.5:
                        log(f"利润回撤止盈！峰值{peak_pnl:.1f}%→{pnl_pct:.1f}%")
                        executor.cancel_all_sl_tp()
                        executor.close_position(direction)
                        state.pop(peak_key, None)
                        state.pop("last_signal", None)
                        save_state(state)
                        continue
                    # LLM动态调整止盈位
                    if pnl_pct >= MIN_PROFIT:
                        current_tp = executor._get_current_tp(direction)
                        if current_tp > 0:
                            try:
                                from llm_client import manage_position as llm_manage
                                qty_tp = abs(float(pos.get('contracts', 0)))
                                indicators_raw = f"ADX={float(h4['adx']):.0f} +DI={float(h4['plus_di']):.0f} -DI={float(h4['minus_di']):.0f} price={price:.1f} pnl={pnl_pct:+.1f}% peak={peak_pnl:.1f}%"
                                coin_name = SYMBOL.split("/")[0]
                                result = llm_manage(
                                    coin_name, direction, entry_p, price, current_tp,
                                    float(h4["atr"]), indicators_raw
                                )
                                if result[0] in ("WIDEN", "TIGHTEN"):
                                    new_tp = result[1]
                                    reason = result[2] if len(result) > 2 else ""
                                    action_cn = "放宽" if result[0] == "WIDEN" else "收紧"
                                    log(f"LLM移动止盈: {current_tp:.1f}→{new_tp:.1f} [{action_cn}] {reason[:50]}")
                                    executor._replace_tp(direction, qty_tp, new_tp)
                                    executor._update_trail_tp(direction, new_tp)
                                elif result[0] == "KEEP":
                                    log(f"LLM移动止盈: 维持 | 浮盈{pnl_pct:+.1f}%")
                            except Exception as e:
                                log(f"移动止盈异常: {e}")

            # 2. 检查已有成交的SL/TP
            executor.ensure_sl_tp()
            executor.ensure_naked_sl_tp()

            # 方向消失 → 撤限价单
            if not direction:
                for o in executor.ex.fetch_open_orders(SYMBOL):
                    if not (o.get('reduceOnly') or o.get('reduce_only')):
                        executor.ex.cancel_order(o['id'], SYMBOL)
                        log(f'方向消失 → 撤限价单: {o["id"][:16]}')

            # 方向反转 → 平旧仓
            pos = executor.get_any_position()
            if pos and direction:
                pos_side = executor._get_pos_side(pos)
                if pos_side and pos_side != direction:
                    log(f'方向反转: {pos_side} → {direction}')
                    executor.cancel_all_sl_tp()
                    executor.close_position(pos_side)

            # 开仓 (LLM二次确认)
            if direction and not executor.has_position(direction) and plan:
                if executor.has_open_order(direction):
                    if executor.update_order_if_stale(plan):
                        state['last_signal'] = direction
                        save_state(state)
                else:
                    try:
                        from llm_client import analyze as llm_analyze
                        from llm_review import _write_trade_log
                        import json as _json

                        enrich = {}
                        epath = os.path.join(SCRIPT_DIR, 'market_enrich.json')
                        if os.path.exists(epath):
                            with open(epath) as _f:
                                edata = _json.load(_f)
                                enrich = edata.get('coins', {}).get('BTC', {})

                        indicators = {'price': price, 'atr': float(h4.get('atr',0)),
                                       'atr_pct': float(h4.get('atr_pct',0)),
                                       'raw': f'ADX={float(h4.get("adx",0)):.0f} +DI={float(h4.get("plus_di",0)):.0f} -DI={float(h4.get("minus_di",0)):.0f} price={price:.3f} ATR={float(h4.get("atr_pct",0)):.1f}%'}

                        decision, reason = llm_analyze(
                            'BTC', direction, plan['entry'], plan['sl'],
                            plan['tp'], POSITION_SIZE, LEVERAGE, indicators, enrich
                        )

                        if decision == 'CONFIRMED':
                            log(f'✅ LLM确认: {reason[:60]}')
                            _write_trade_log('BTC', 'CONFIRMED', reason, {'direction': direction, 'entry_price': plan['entry'], 'stop_loss': plan['sl'], 'take_profit': plan['tp'], 'qty': POSITION_SIZE, 'leverage': LEVERAGE})
                            executor.open_position(plan)
                            state['last_signal'] = direction
                            save_state(state)
                        else:
                            log(f'❌ LLM否决: {reason[:60]}')
                            _write_trade_log('BTC', 'REJECTED', reason, {'direction': direction, 'entry_price': plan['entry'], 'stop_loss': plan['sl'], 'take_profit': plan['tp'], 'qty': POSITION_SIZE, 'leverage': LEVERAGE})
                    except Exception as e:
                        log(f'LLM异常({e})，安全拒绝开仓')
                        try:
                            _write_trade_log('BTC', 'REJECTED', f'LLM异常: {str(e)[:60]}', {'direction': direction, 'entry_price': plan['entry'], 'stop_loss': plan['sl'], 'take_profit': plan['tp'], 'qty': POSITION_SIZE, 'leverage': LEVERAGE})
                        except:
                            pass

            elapsed = time.time() - t0
            remaining = POLL_SECONDS - elapsed
            while remaining > 0:
                chunk = min(30, remaining)
                time.sleep(chunk)
                if executor._pending_plan:
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
