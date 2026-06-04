#!/usr/bin/env python3
"""
HYPE 超短线 — 动量突破 + 15m趋势确认
核心: 5m价格突破15m前高/低 + 量确认 → 入场
      单方向单仓，ATR动态止损，ADX过热刹车
"""

import ccxt, pandas as pd, numpy as np, time, json, os
from datetime import datetime

# ════════════════════ 配置 ════════════════════
SYMBOL = 'HYPE/USDT:USDT'
LEVERAGE = 10
POSITION_SIZE = 1.0        # 1个HYPE
TP_PCT = 0.6               # 止盈0.6%
SL_PCT = 1.0               # 止损1.0%
SL_ATR_MULT = 1.5          # ATR止损倍率（备选）
MIN_ADX = 20               # 15m ADX下限
MAX_ADX = 40               # ADX上限，过热不做
DI_RATIO = 1.3
VOL_RATIO = 0.6
POLL_SECONDS = 15

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, 'hype_scalp_state.json')
LOG_FILE = os.path.join(SCRIPT_DIR, 'hype_scalp.log')

API_KEY = '1iUNLoIbEpVwwi4eHPTrKD25FvsYhR0iEwKLhDuvCOW7EgDa7h9B3PdpzffhghMB'
API_SECRET = 'YWusnOHhS1OKHXJBJ57B3Q8zih6Ymhk6oK7CK4jJg3U9eOwcdyQ6eraCIaoVgIN6'

def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line, flush=True)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')

def ema(arr, span):
    return pd.Series(arr).ewm(span=span, adjust=True).mean().values

def compute(df):
    c = df['close'].values
    df['ema5'] = ema(c, 5)
    df['ema10'] = ema(c, 10)

    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift(1)),
        abs(df['low'] - df['close'].shift(1))
    ], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()

    up = df['high'] - df['high'].shift(1)
    down = df['low'].shift(1) - df['low']
    pdm = np.where((up > down) & (up > 0), up, 0)
    ndm = np.where((down > up) & (down > 0), down, 0)
    atr_s = df['atr'].values
    pdi = 100 * pd.Series(pdm).rolling(14).mean() / np.where(atr_s > 0, atr_s, np.nan)
    ndi = 100 * pd.Series(ndm).rolling(14).mean() / np.where(atr_s > 0, atr_s, np.nan)
    dx = 100 * abs(pdi - ndi) / (pdi + ndi).replace(0, np.nan)
    df['adx'] = dx.rolling(14).mean()
    df['plus_di'] = pdi
    df['minus_di'] = ndi
    df['vol_ma20'] = df['vol'].rolling(20).mean()
    return df

class Analyzer:
    def __init__(self, exchange):
        self.ex = exchange
        self.data = {}

    def fetch(self):
        for tf, limit in [('5m', 200), ('15m', 150)]:
            raw = self.ex.fetch_ohlcv(SYMBOL, tf, limit=limit)
            df = pd.DataFrame(raw, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
            df['ts'] = pd.to_datetime(df['ts'], unit='ms')
            df.set_index('ts', inplace=True)
            self.data[tf] = compute(df)

    @property
    def price(self):
        return self.data['5m']['close'].iloc[-1]

    def trend_15m(self):
        """15m趋势确认: EMA方向 + ADX区间 + DI方向"""
        d15 = self.data['15m']
        prev = d15.iloc[-2]  # 已闭K

        ema_bull = prev['ema5'] > prev['ema10']
        ema_bear = prev['ema5'] < prev['ema10']

        adx = prev['adx']
        adx_ok = pd.notna(adx) and MIN_ADX <= adx <= MAX_ADX

        if not adx_ok:
            if pd.notna(adx) and adx > MAX_ADX:
                log(f'  ADX={adx:.0f}>40 趋势过热 → 观望')
            return None

        di = prev['plus_di'] - prev['minus_di']
        di_dir = 'LONG' if di > 0 else ('SHORT' if di < 0 else None)

        if ema_bull and di_dir == 'LONG':
            return 'LONG'
        if ema_bear and di_dir == 'SHORT':
            return 'SHORT'
        return None

    def breakout_signal(self):
        """5m动量突破: 价格突破15m前高/前低 + 量确认"""
        d5 = self.data['5m']
        d15 = self.data['15m']
        prev15 = d15.iloc[-2]  # 已闭K的15m bar

        # 最近5根5m bar（已闭K，不含当前未闭K）
        recent5 = d5.iloc[-6:-1]  # 5根已闭K 5m
        current_close = d5['close'].iloc[-1]

        # 突破判定
        break_high = current_close > prev15['high']
        break_low = current_close < prev15['low']

        # 量确认: 当前5m bar的量（用已闭K的上一根）
        last5_vol = d5['vol'].iloc[-2]
        vol_ok = last5_vol > VOL_RATIO * d5['vol_ma20'].iloc[-2]

        # 动量确认: 突破方向的5m K线实体
        if break_high:
            body = current_close - d5['open'].iloc[-1]
            body_pct = body / d5['open'].iloc[-1] * 100
            if body_pct < 0.1:
                log(f'  突破力度不足: 实体={body_pct:.2f}%')
                return None
            if not vol_ok:
                log(f'  突破量不足: vol={last5_vol:.0f} < {VOL_RATIO}xMA20={d5["vol_ma20"].iloc[-2]:.0f}')
                return None
            return 'LONG'

        if break_low:
            body = d5['open'].iloc[-1] - current_close
            body_pct = body / d5['open'].iloc[-1] * 100
            if body_pct < 0.1:
                log(f'  突破力度不足: 实体={body_pct:.2f}%')
                return None
            if not vol_ok:
                log(f'  突破量不足')
                return None
            return 'SHORT'

        return None

    def direction(self):
        """完整信号链: 动量突破 + 15m趋势确认"""
        breakout = self.breakout_signal()
        if breakout is None:
            return None, '无动量突破'

        trend = self.trend_15m()
        if trend is None:
            return None, '15m趋势不确认'

        if breakout != trend:
            return None, f'方向分歧(突破={breakout} 趋势={trend})'

        return breakout, f'动量突破 ✅ 15m共振 ✅'

def main():
    exchange = ccxt.binance({
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'options': {'defaultType': 'future'},
        'urls': {'api': {'fapiPublic': 'https://fapi.binance.com/fapi/v1'}},
    })
    exchange.set_sandbox_mode(False)

    try:
        exchange.set_leverage(LEVERAGE, SYMBOL)
    except Exception as e:
        log(f'设杠杆(可能已是{LEVERAGE}x): {e}')

    analyzer = Analyzer(exchange)
    in_position = False
    pos_dir = None

    log('═══ HYPE超短线v2 动量突破 ═══')
    log(f'杠杆{LEVERAGE}x 仓位{POSITION_SIZE}个 TP={TP_PCT}% SL={SL_PCT}%')
    log(f'ADX窗口{MIN_ADX}~{MAX_ADX} 轮询{POLL_SECONDS}s')

    while True:
        try:
            analyzer.fetch()
            price = analyzer.price
            direction, reason = analyzer.direction()
            log(f'价格={price:.4f} 方向={direction or "观望"} {reason}')

            if in_position:
                pos = exchange.fetch_position(SYMBOL)
                if pos and abs(float(pos['info']['positionAmt'])) > 0.001:
                    log(f'  持仓中 {pos_dir}，等待平仓')
                else:
                    log(f'  已平仓')
                    in_position = False
                    pos_dir = None

            if not in_position and direction:
                log(f'🚀 开仓: {direction} | {reason}')

                amt = POSITION_SIZE if direction == 'LONG' else -POSITION_SIZE
                try:
                    # 市价开仓
                    order = exchange.create_order(
                        SYMBOL, 'market',
                        'buy' if direction == 'LONG' else 'sell',
                        abs(amt), None, {'positionSide': 'BOTH'}
                    )
                    fill = float(order.get('average') or order.get('price') or price)
                    log(f'  成交 @ {fill:.4f}')

                    # 固定TP/SL
                    if direction == 'LONG':
                        tp_p = fill * (1 + TP_PCT / 100)
                        sl_p = fill * (1 - SL_PCT / 100)
                    else:
                        tp_p = fill * (1 - TP_PCT / 100)
                        sl_p = fill * (1 + SL_PCT / 100)

                    exchange.create_order(SYMBOL, 'TAKE_PROFIT_MARKET',
                        'sell' if direction == 'LONG' else 'buy',
                        abs(amt), tp_p,
                        {'stopPrice': tp_p, 'positionSide': 'BOTH'})
                    exchange.create_order(SYMBOL, 'STOP_MARKET',
                        'sell' if direction == 'LONG' else 'buy',
                        abs(amt), None,
                        {'stopPrice': sl_p, 'positionSide': 'BOTH'})

                    log(f'  TP={tp_p:.4f}(+{TP_PCT}%) SL={sl_p:.4f}(-{SL_PCT}%)')
                    in_position = True
                    pos_dir = direction

                except Exception as e:
                    log(f'  开仓失败: {e}')

        except Exception as e:
            log(f'循环异常: {e}')
            import traceback; traceback.print_exc()

        time.sleep(POLL_SECONDS)

if __name__ == '__main__':
    main()
