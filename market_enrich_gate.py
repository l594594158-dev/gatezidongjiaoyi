#!/usr/bin/env python3
"""
市场数据增强层 (Gate.io版): 每4小时拉取资金费率/OI数据，供LLM分析使用。
"""
import ccxt, json, os, time
from datetime import datetime, timezone
from gate_config import GATE_API_KEY, GATE_API_SECRET

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'market_enrich.json')

COINS = {
    'BTC': 'BTC/USDT:USDT', 'ETH': 'ETH/USDT:USDT',
    'SOL': 'SOL/USDT:USDT', 'BNB': 'BNB/USDT:USDT',
}

EX = ccxt.gate({
    'apiKey': GATE_API_KEY, 'secret': GATE_API_SECRET,
    'options': {'defaultType': 'swap'},
})

def fetch_all():
    out = {'updated': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'), 'coins': {}}

    for name, sym in COINS.items():
        coin_data = {}

        # 1. 资金费率
        try:
            fr = EX.fetch_funding_rate(sym)
            coin_data['funding_rate'] = round(float(fr['fundingRate']) * 100, 4)
            coin_data['funding_next'] = fr.get('fundingTimestamp', 0)
        except Exception as e:
            coin_data['funding_rate'] = str(e)

        # 2. OI (Gate: 合约统计)
        try:
            ticker = EX.fetch_ticker(sym)
            # Gate.io ticker 通常不直接提供OI，用合约数量×价格估算
            if 'info' in ticker and 'total_size' in ticker['info']:
                oi_contracts = float(ticker['info']['total_size'])
                coin_data['oi_contracts'] = oi_contracts
                coin_data['oi_value'] = oi_contracts * ticker['last']
            else:
                coin_data['oi_contracts'] = 'N/A (Gate)'
                coin_data['oi_value'] = 'N/A'
        except Exception as e:
            coin_data['oi_contracts'] = str(e)
            coin_data['oi_value'] = str(e)

        # 3. 多空比 (Gate: 无公开API，标记为N/A)
        coin_data['ls_position_ratio'] = 'N/A (Gate)'
        coin_data['ls_account_ratio'] = 'N/A (Gate)'

        out['coins'][name] = coin_data

    with open(CACHE_FILE, 'w') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    return out


def summary():
    if not os.path.exists(CACHE_FILE):
        fetch_all()
    with open(CACHE_FILE) as f:
        data = json.load(f)

    lines = [f"=== 市场增强数据 ({data['updated']}) ==="]
    lines.append(f"{'币种':>5} {'费率%':>8} {'OI(估算)':>15}")
    lines.append("-" * 40)

    for name in sorted(data['coins'].keys()):
        c = data['coins'][name]
        fr = c.get('funding_rate', '?')
        oi = c.get('oi_value', 'N/A')

        fr_s = f"{fr:+.4f}" if isinstance(fr, (int, float)) else '?'
        oi_s = f"${oi/1e6:.1f}M" if isinstance(oi, (int, float)) else 'N/A'

        lines.append(f"{name:>5} {fr_s:>8} {oi_s:>15}")

    return '\n'.join(lines)


if __name__ == '__main__':
    fetch_all()
    print(summary())
