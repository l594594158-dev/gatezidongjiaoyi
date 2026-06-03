#!/usr/bin/env python3
"""
BTC 全自动交易机器人 - 动态分析版
每5分钟轮询，多周期EMA+ADX+DI 动态判方向，
阻力/支撑共振区入场，ATR自适应止损。
"""

import ccxt
import pandas as pd
import numpy as np
import time
import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple, List

# ── 配置 ──────────────────────────────────────────

SYMBOL = 'BTC/USDT:USDT'
TIMEFRAMES = ['1h', '4h', '1d']
PRIMARY_TF = '4h'
ENTRY_TF = '1h'

MARGIN_PER_TRADE = 10          # 单笔保证金 USDT
LEVERAGE = 50                  # 逐仓杠杆
SL_ATR_MULT = 1.5              # 止损 = 入场价 ± SL_ATR_MULT × ATR(4h)
TP1_ATR_MULT = 1.0             # 第一止盈
TP2_ATR_MULT = 2.0             # 第二止盈

MIN_ADX = 25                   # ADX 趋势阈值
DI_RATIO_THRESHOLD = 1.5       # DI 比值阈值（强势方/弱势方）
EMA_SHORT = 5
EMA_LONG = 10
RSI_PERIOD = 14
ATR_PERIOD = 14
FIB_LEVELS = [0.236, 0.382, 0.5, 0.618]

POLL_SECONDS = 300             # 5分钟轮询
DRY_RUN = False                # True=只分析不下单

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, 'btc_dynamic_state.json')
LOG_FILE = os.path.join(SCRIPT_DIR, 'btc_dynamic.log')

sys.path.insert(0, SCRIPT_DIR)
from api_config import TRADE_API_KEY, TRADE_SECRET


# ── 日志 ──────────────────────────────────────────

def log(msg: str):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')


# ── 指标计算 ──────────────────────────────────────

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """原地计算所有技术指标"""
    df['ema5'] = df['close'].ewm(span=EMA_SHORT).mean()
    df['ema10'] = df['close'].ewm(span=EMA_LONG).mean()
    df['sma10'] = df['close'].rolling(10).mean()

    # RSI
    delta = df['close'].diff()
    gain = delta.clip(lower=0).rolling(RSI_PERIOD).mean()
    loss = (-delta.clip(upper=0)).rolling(RSI_PERIOD).mean()
    rs = gain / loss.replace(0, np.nan)
    df['rsi'] = 100 - (100 / (1 + rs))

    # ATR
    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift(1)),
        abs(df['low'] - df['close'].shift(1))
    ], axis=1).max(axis=1)
    df['atr'] = tr.rolling(ATR_PERIOD).mean()

    # ADX / +DI / -DI
    atr_s = df['atr']
    up = df['high'] - df['high'].shift(1)
    down = df['low'].shift(1) - df['low']
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0), index=df.index)
    plus_di = 100 * plus_dm.rolling(ATR_PERIOD).mean() / atr_s.replace(0, np.nan)
    minus_di = 100 * minus_dm.rolling(ATR_PERIOD).mean() / atr_s.replace(0, np.nan)
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
    df['adx'] = dx.rolling(ATR_PERIOD).mean()
    df['plus_di'] = plus_di
    df['minus_di'] = minus_di

    return df


# ── 市场分析器 ─────────────────────────────────────

class MarketAnalyzer:
    def __init__(self, exchange: ccxt.Exchange):
        self.exchange = exchange
        self.data: Dict[str, pd.DataFrame] = {}

    def fetch(self):
        """拉取所有周期K线"""
        for tf in TIMEFRAMES:
            limit = 100 if tf == '1h' else 60
            raw = self.exchange.fetch_ohlcv(SYMBOL, tf, limit=limit)
            df = pd.DataFrame(raw, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
            df['ts'] = pd.to_datetime(df['ts'], unit='ms')
            df.set_index('ts', inplace=True)
            self.data[tf] = compute_indicators(df)
        return self

    @property
    def current_price(self) -> float:
        return self.data['1h']['close'].iloc[-1]

    def direction(self) -> Optional[str]:
        """
        多维度方向判定，返回 'LONG' / 'SHORT' / None
        """
        scores = {'LONG': 0, 'SHORT': 0}
        details = []

        # 1. EMA 排列
        for tf in TIMEFRAMES:
            df = self.data[tf]
            last = df.iloc[-1]
            if pd.notna(last['ema5']) and pd.notna(last['ema10']):
                if last['ema5'] > last['ema10']:
                    scores['LONG'] += 1
                    details.append(f'{tf} EMA多头')
                else:
                    scores['SHORT'] += 1
                    details.append(f'{tf} EMA空头')

        # 2. 主周期 ADX
        df4 = self.data['4h']
        last4 = df4.iloc[-1]
        prev4 = df4.iloc[-2] if len(df4) > 1 else last4

        if pd.notna(last4['adx']):
            if last4['adx'] > MIN_ADX:
                details.append(f'4h ADX={last4["adx"]:.0f}>25 强趋势')
                if last4['adx'] > prev4['adx']:
                    details.append('ADX上升→趋势增强')

                # DI 判定方向
                if pd.notna(last4['plus_di']) and pd.notna(last4['minus_di']):
                    if last4['minus_di'] > last4['plus_di'] * DI_RATIO_THRESHOLD:
                        scores['SHORT'] += 3
                        details.append(f'-DI({last4["minus_di"]:.0f})碾压+DI({last4["plus_di"]:.0f})')
                    elif last4['plus_di'] > last4['minus_di'] * DI_RATIO_THRESHOLD:
                        scores['LONG'] += 3
                        details.append(f'+DI({last4["plus_di"]:.0f})碾压-DI({last4["minus_di"]:.0f})')
            else:
                details.append(f'4h ADX={last4["adx"]:.0f}≤25 震荡市')

        # 3. RSI 极端值
        if pd.notna(last4['rsi']):
            details.append(f'4h RSI={last4["rsi"]:.0f}')

        # 判定
        diff = scores['LONG'] - scores['SHORT']
        if diff >= 2:
            return 'LONG'
        elif diff <= -2:
            return 'SHORT'
        else:
            return None  # 方向不明确

    def summary(self) -> str:
        """生成分析摘要"""
        dir_ = self.direction()
        df4 = self.data['4h']
        last4 = df4.iloc[-1]
        df1 = self.data['1h']
        last1 = df1.iloc[-1]

        lines = [
            f'方向: {dir_ or "NONE(观望)"}',
            f'价格: {self.current_price:.1f}',
            f'4h EMA5={last4["ema5"]:.0f} EMA10={last4["ema10"]:.0f}',
            f'4h ADX={last4["adx"]:.0f} +DI={last4["plus_di"]:.0f} -DI={last4["minus_di"]:.0f} RSI={last4["rsi"]:.0f}',
            f'1h EMA5={last1["ema5"]:.0f} EMA10={last1["ema10"]:.0f} RSI={last1["rsi"]:.0f}',
            f'4h ATR={last4["atr"]:.0f} ({last4["atr"]/self.current_price*100:.1f}%)',
        ]
        return '\n'.join(lines)


# ── 入场规划器 ─────────────────────────────────────

class EntryPlanner:
    def __init__(self, analyzer: MarketAnalyzer):
        self.a = analyzer

    def plan_short(self) -> Optional[Dict]:
        """做空入场计划：找阻力共振区"""
        price = self.a.current_price
        df4 = self.a.data['4h']
        last4 = df4.iloc[-1]

        # 收集阻力位
        resistances = []

        # 4h EMA
        for ema_name, ema_val in [('EMA5', last4['ema5']), ('EMA10', last4['ema10'])]:
            if pd.notna(ema_val) and ema_val > price:
                resistances.append({'level': ema_val, 'source': f'4h_{ema_name}', 'weight': 3})

        # 1h EMA
        df1 = self.a.data['1h']
        last1 = df1.iloc[-1]
        for ema_name, ema_val in [('EMA5', last1['ema5']), ('EMA10', last1['ema10'])]:
            if pd.notna(ema_val) and ema_val > price:
                resistances.append({'level': ema_val, 'source': f'1h_{ema_name}', 'weight': 2})

        # VWAP (24根1h)
        recent_24 = df1.tail(24)
        if len(recent_24) >= 5:
            vwap = (recent_24['close'] * recent_24['vol']).sum() / recent_24['vol'].sum()
            if vwap > price:
                resistances.append({'level': vwap, 'source': 'VWAP_24h', 'weight': 2})

        # 斐波那契
        recent_30 = df4.tail(30)
        high = recent_30['high'].max()
        low = recent_30['low'].min()
        if high > low:
            for fib in FIB_LEVELS:
                level = low + (high - low) * fib
                if level > price:
                    resistances.append({'level': level, 'source': f'Fib_{fib:.1%}', 'weight': 1})

        # 前低（已跌破 → 阻力）
        pre_crash = df4[df4.index < df4.index[-10]]
        if len(pre_crash) > 0:
            broken_low = pre_crash['low'].min()
            if broken_low > price:
                resistances.append({'level': broken_low, 'source': 'broken_support', 'weight': 3})

        if not resistances:
            return None

        # 按价格排序，找权重共振区
        resistances.sort(key=lambda x: x['level'])

        # 聚类：相近阻力合并
        clusters = []
        for r in resistances:
            if clusters and abs(r['level'] - clusters[-1]['level']) / price < 0.005:
                clusters[-1]['level'] = (clusters[-1]['level'] * clusters[-1]['weight'] +
                                         r['level'] * r['weight']) / (clusters[-1]['weight'] + r['weight'])
                clusters[-1]['weight'] += r['weight']
                clusters[-1]['source'] += '+' + r['source']
            else:
                clusters.append(r.copy())

        # 选权重最高且离当前价最近的
        best = max(clusters, key=lambda x: x['weight'] / max(1, (x['level'] - price) / price * 100))

        atr = last4['atr']
        entry = best['level']
        sl = entry + SL_ATR_MULT * atr
        tp1 = current_low = recent_30['low'].min()
        tp2 = round(tp1 / 1000) * 1000 - 1000  # 下一整数关口

        return {
            'direction': 'SHORT',
            'entry_zone': f'{entry:.0f} (共振: {best["source"]}, 权重={best["weight"]})',
            'entry': entry,
            'sl': sl,
            'tp1': tp1,
            'tp2': tp2,
            'atr': atr,
        }

    def plan_long(self) -> Optional[Dict]:
        """做多入场计划：找支撑共振区（镜像逻辑）"""
        price = self.a.current_price
        df4 = self.a.data['4h']
        last4 = df4.iloc[-1]

        supports = []

        for ema_name, ema_val in [('EMA5', last4['ema5']), ('EMA10', last4['ema10'])]:
            if pd.notna(ema_val) and ema_val < price:
                supports.append({'level': ema_val, 'source': f'4h_{ema_name}', 'weight': 3})

        df1 = self.a.data['1h']
        last1 = df1.iloc[-1]
        for ema_name, ema_val in [('EMA5', last1['ema5']), ('EMA10', last1['ema10'])]:
            if pd.notna(ema_val) and ema_val < price:
                supports.append({'level': ema_val, 'source': f'1h_{ema_name}', 'weight': 2})

        recent_30 = df4.tail(30)
        high = recent_30['high'].max()
        low = recent_30['low'].min()
        if high > low:
            for fib in FIB_LEVELS:
                level = high - (high - low) * fib
                if level < price:
                    supports.append({'level': level, 'source': f'Fib_{fib:.1%}', 'weight': 1})

        if not supports:
            return None

        supports.sort(key=lambda x: x['level'], reverse=True)

        clusters = []
        for r in supports:
            if clusters and abs(r['level'] - clusters[-1]['level']) / price < 0.005:
                clusters[-1]['weight'] += r['weight']
                clusters[-1]['source'] += '+' + r['source']
            else:
                clusters.append(r.copy())

        best = max(clusters, key=lambda x: x['weight'] / max(1, (price - x['level']) / price * 100))

        atr = last4['atr']
        entry = best['level']
        sl = entry - SL_ATR_MULT * atr
        tp1 = recent_30['high'].max()
        tp2 = round(tp1 / 1000) * 1000 + 1000

        return {
            'direction': 'LONG',
            'entry_zone': f'{entry:.0f} (共振: {best["source"]}, 权重={best["weight"]})',
            'entry': entry,
            'sl': sl,
            'tp1': tp1,
            'tp2': tp2,
            'atr': atr,
        }

    def plan(self) -> Optional[Dict]:
        """生成入场计划"""
        dir_ = self.a.direction()
        if dir_ == 'SHORT':
            return self.plan_short()
        elif dir_ == 'LONG':
            return self.plan_long()
        return None


# ── 仓位管理器 ─────────────────────────────────────

class PositionManager:
    def __init__(self, exchange: ccxt.Exchange):
        self.exchange = exchange

    def get_positions(self) -> list:
        """获取当前BTC持仓"""
        try:
            positions = self.exchange.fetch_positions([SYMBOL])
            active = []
            for p in positions:
                if p.get('contracts') and float(p['contracts']) > 0:
                    active.append({
                        'side': p['side'],
                        'contracts': float(p['contracts']),
                        'entry_price': float(p['entryPrice']) if p.get('entryPrice') else 0,
                        'upnl': float(p.get('unrealizedPnl', 0)),
                    })
            return active
        except Exception as e:
            log(f'获取持仓异常: {e}')
            return []

    def has_direction(self, direction: str) -> Optional[Dict]:
        """检查是否已有该方向持仓"""
        for p in self.get_positions():
            if p['side'] == direction.lower():
                return p
        return None

    def cancel_all_orders(self):
        """取消所有BTC挂单（含条件单）"""
        try:
            # 普通挂单
            orders = self.exchange.fetch_open_orders(SYMBOL)
            for o in orders:
                self.exchange.cancel_order(o['id'], SYMBOL)
                log(f'取消挂单: {o["id"]}')
            # 条件单 (SL)
            cond = self.exchange.private_futures_get_settle_price_orders({
                'settle': 'usdt', 'status': 'open', 'contract': 'BTC_USDT'
            })
            for o in cond:
                self.exchange.private_futures_delete_settle_price_orders_order_id({
                    'settle': 'usdt', 'order_id': o['id']
                })
                log(f'取消条件单: {o["id"]}')
        except Exception as e:
            log(f'取消挂单异常: {e}')

    def open_position(self, plan: Dict):
        """按计划开仓"""
        dir_ = plan['direction']
        side = 'sell' if dir_ == 'SHORT' else 'buy'
        entry_price = plan['entry']
        sl_price = plan['sl']
        tp_price = plan['tp1']

        # 计算合约张数: contractSize=0.0001 BTC, 1张=price×0.0001 USDT
        ticker = self.exchange.fetch_ticker(SYMBOL)
        price = ticker['last']
        contract_size = self.exchange.market(SYMBOL)['contractSize']
        notional = MARGIN_PER_TRADE * LEVERAGE
        contracts = int(notional / (contract_size * price))
        contracts = max(1, contracts)

        log(f'计划开{dir_}: entry={entry_price:.0f} SL={sl_price:.0f} TP={tp_price:.0f} 张数={contracts}')

        if DRY_RUN:
            log('[DRY RUN] 模拟开仓，不实际执行')
            return None

        try:
            # 设置杠杆
            self.exchange.set_leverage(LEVERAGE, SYMBOL)

            # 下限价单（maker，省手续费）
            order = self.exchange.create_order(
                SYMBOL, 'limit', side, contracts, entry_price,
                params={
                    'text': f't-btc_dynamic_{dir_}',
                }
            )
            log(f'限价单已挂: {order["id"]} {side} {contracts}张 @ {entry_price:.0f}')

            # 记录待挂 SL/TP（等成交后再挂）
            self._pending_plan = {
                'order_id': order['id'],
                'side': side,
                'direction': dir_,
                'sl': sl_price,
                'tp': tp_price,
                'contracts': contracts,
            }
            return order

        except Exception as e:
            log(f'开仓异常: {e}')
            traceback.print_exc()
            return None

    def ensure_sl_tp(self):
        """对已成交的仓位确保挂好止损止盈"""
        if not hasattr(self, '_pending_plan') or not self._pending_plan:
            return

        plan = self._pending_plan
        try:
            order = self.exchange.fetch_order(plan['order_id'], SYMBOL)
            if order['status'] != 'closed':
                return

            contracts = plan['contracts']
            direction = plan['direction']
            sl = plan['sl']
            tp = plan['tp']

            log(f'成交确认，挂SL/TP: {direction} SL={sl:.0f} TP={tp:.0f}')

            if DRY_RUN:
                log('[DRY RUN] 模拟SL/TP')
                self._pending_plan = None
                return

            if direction == 'SHORT':
                # 空仓止损: 价格涨到 sl 触发 → 用条件单 direction='short' 平空
                self.exchange.private_futures_post_settle_price_orders({
                    'settle': 'usdt',
                    'trigger': {
                        'strategy_type': 0, 'price_type': 0,
                        'price': str(sl), 'rule': 1, 'expiration': 0
                    },
                    'initial': {
                        'contract': 'BTC_USDT', 'size': contracts,
                        'price': '0', 'tif': 'ioc', 'direction': 'short'
                    }
                })
                # 空仓止盈: reduceOnly 限价买
                self.exchange.create_order(
                    SYMBOL, 'limit', 'buy', contracts, tp,
                    params={'reduceOnly': True, 'text': 't-btc_dyn_TP'}
                )
            else:  # LONG
                # 多仓止损: 价格跌到 sl 触发
                self.exchange.private_futures_post_settle_price_orders({
                    'settle': 'usdt',
                    'trigger': {
                        'strategy_type': 0, 'price_type': 0,
                        'price': str(sl), 'rule': 1, 'expiration': 0
                    },
                    'initial': {
                        'contract': 'BTC_USDT', 'size': contracts,
                        'price': '0', 'tif': 'ioc', 'direction': 'long'
                    }
                })
                # 多仓止盈: reduceOnly 限价卖
                self.exchange.create_order(
                    SYMBOL, 'limit', 'sell', contracts, tp,
                    params={'reduceOnly': True, 'text': 't-btc_dyn_TP'}
                )

            # 验证挂载
            cond_count = len(self.exchange.private_futures_get_settle_price_orders({
                'settle': 'usdt', 'status': 'open', 'contract': 'BTC_USDT'
            }))
            open_orders = len(self.exchange.fetch_open_orders(SYMBOL))
            log(f'SL/TP挂载完成: 条件单={cond_count} 限价单={open_orders}')
            self._pending_plan = None

        except Exception as e:
            log(f'SL/TP挂载异常: {e}')

    def close_position(self, side: str):
        """市价平仓指定方向"""
        positions = self.get_positions()
        for p in positions:
            if p['side'] == side.lower():
                close_side = 'buy' if side.lower() == 'short' else 'sell'
                log(f'平仓 {side}: {p["contracts"]}张 @ 市价')
                if not DRY_RUN:
                    self.exchange.create_order(
                        SYMBOL, 'market', close_side, p['contracts'],
                        params={'text': 't-btc_dynamic_close'}
                    )


# ── 状态管理 ──────────────────────────────────────

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {'active_directions': [], 'last_signal': None, 'trades': []}

def save_state(state: dict):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2, default=str)


# ── 主循环 ────────────────────────────────────────

def main():
    log('════════ BTC 动态交易机器人 启动 ════════')
    log(f'品种: {SYMBOL}  杠杆: {LEVERAGE}x  保证金/笔: {MARGIN_PER_TRADE} USDT')
    log(f'轮询间隔: {POLL_SECONDS}s  DRY_RUN: {DRY_RUN}')

    exchange = ccxt.gate({
        'apiKey': TRADE_API_KEY,
        'secret': TRADE_SECRET,
        'options': {'defaultType': 'swap'},
    })
    exchange.load_markets()

    analyzer = MarketAnalyzer(exchange)
    planner = None
    pm = PositionManager(exchange)
    state = load_state()

    while True:
        try:
            tick_start = time.time()

            # 1. 分析市场
            analyzer.fetch()
            direction = analyzer.direction()
            planner = EntryPlanner(analyzer)
            plan = planner.plan()

            price = analyzer.current_price
            summary = analyzer.summary()

            log(f'── 轮询 {datetime.now().strftime("%H:%M:%S")} ──')
            log(f'价格: {price:.1f}  方向判定: {direction or "观望"}')

            # 2. 同步持仓状态
            positions = pm.get_positions()
            active_sides = [p['side'] for p in positions]

            # 3. 确保已成交仓位的SL/TP
            pm.ensure_sl_tp()

            # 4. 方向反转检查：如果持仓方向和当前信号不符，平仓
            for p in positions:
                p_side = p['side'].upper()
                if direction and p_side != direction:
                    # 方向反转了
                    if direction == 'LONG' and p_side == 'SHORT':
                        log(f'信号转LONG，平空仓')
                        pm.close_position('SHORT')
                    elif direction == 'SHORT' and p_side == 'LONG':
                        log(f'信号转SHORT，平多仓')
                        pm.close_position('LONG')

            # 5. 开仓：信号方向没有持仓 → 挂单
            if direction and direction not in active_sides:
                # 检查是否已经有该方向挂单
                existing_dirs = state.get('active_directions', [])
                if direction not in existing_dirs:
                    if plan:
                        log(f'信号{direction}，入场计划: {plan["entry_zone"]}')
                        log(f'  entry={plan["entry"]:.0f} SL={plan["sl"]:.0f} TP1={plan["tp1"]:.0f}')
                        pm.open_position(plan)
                        existing_dirs.append(direction)
                        state['active_directions'] = existing_dirs
                        state['last_signal'] = direction

            # 6. 如果持仓方向都已经平掉了，清空活跃记录
            state['active_directions'] = [d for d in state.get('active_directions', [])
                                           if d in active_sides]

            save_state(state)

            # 7. 等待下一次轮询
            elapsed = time.time() - tick_start
            wait = max(1, POLL_SECONDS - elapsed)
            log(f'本次耗时 {elapsed:.1f}s，等待 {wait:.0f}s\n')
            time.sleep(wait)

        except KeyboardInterrupt:
            log('收到中断信号，退出')
            break
        except Exception as e:
            log(f'主循环异常: {e}')
            traceback.print_exc()
            time.sleep(30)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='只分析不下单')
    parser.add_argument('--once', action='store_true', help='只执行一次分析')
    args = parser.parse_args()

    if args.dry_run:
        DRY_RUN = True

    if args.once:
        exchange = ccxt.gate({
            'apiKey': TRADE_API_KEY,
            'secret': TRADE_SECRET,
            'options': {'defaultType': 'swap'},
        })
        exchange.load_markets()
        analyzer = MarketAnalyzer(exchange).fetch()
        planner = EntryPlanner(analyzer)

        print('══════════════════════')
        print(analyzer.summary())
        print('──────────────────────')
        plan = planner.plan()
        if plan:
            print(f'入场计划: {plan["direction"]}')
            print(f'  入场区: {plan["entry_zone"]}')
            print(f'  入场价: {plan["entry"]:.0f}')
            print(f'  止损:   {plan["sl"]:.0f}  (-{(plan["sl"]-plan["entry"])/plan["entry"]*100:.1f}%)')
            print(f'  止盈1:  {plan["tp1"]:.0f}  (+{(plan["entry"]-plan["tp1"])/plan["entry"]*100:.1f}%)')
            print(f'  止盈2:  {plan["tp2"]:.0f}  (+{(plan["entry"]-plan["tp2"])/plan["entry"]*100:.1f}%)')
            print(f'  ATR(4h): {plan["atr"]:.0f}')
        else:
            print('入场计划: 无（方向不明确或无法找到合适入场位）')
        print('══════════════════════')
    else:
        main()
