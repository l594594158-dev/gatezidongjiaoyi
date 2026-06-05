#!/usr/bin/env python3
"""
市场数据增强层: 每4小时拉取资金费率/OI/多空比，供LLM分析使用。
所有数据走Binance API，零外部依赖，零成本。
"""
import ccxt, json, os, time
from datetime import datetime, timezone

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'market_enrich.json')

COINS = {
    'BTC': 'BTC/USDT:USDT', 'HYPE': 'HYPE/USDT:USDT', 'ZEC': 'ZEC/USDT:USDT',
    'NEAR': 'NEAR/USDT:USDT', 'XLM': 'XLM/USDT:USDT', 'WLD': 'WLD/USDT:USDT',
    'ENA': 'ENA/USDT:USDT', 'SUI': 'SUI/USDT:USDT', 'BNB': 'BNB/USDT:USDT',
}

EX = ccxt.binance({
    'apiKey': 'IlPevOWyWpnC2FgpcRlk7kQX24AjjBh6hhD0l5ki5g43AebJy1GwNPH4D3fzZcI9',
    'secret': 'cdw4Owv1y7llmXZqwHXSTW0pSDEI68EEP0FCMa09bi5r24YenCV4n6vnRzjQpF1I',
    'options': {'defaultType': 'future'},
})

def fetch_all():
    out = {'updated': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'), 'coins': {}}

    for name, sym in COINS.items():
        coin_data = {}
        try:
            # 1. 资金费率
            fr = EX.fetch_funding_rate(sym)
            coin_data['funding_rate'] = round(float(fr['fundingRate']) * 100, 4)
            coin_data['funding_next'] = fr.get('fundingTimestamp', 0)
        except Exception as e:
            coin_data['funding_rate'] = str(e)

        try:
            # 2. 未平仓合约 (直接HTTP)
            import requests as rq, hmac as hm, hashlib as hl, urllib.parse as up
            BASE = 'https://fapi.binance.com'
            KEY = EX.apiKey; SEC = EX.secret
            p = {'symbol': sym.replace('/USDT:USDT', 'USDT'), 'timestamp': int(time.time() * 1000)}
            q = up.urlencode(p)
            p['signature'] = hm.new(SEC.encode(), q.encode(), hl.sha256).hexdigest()
            r = rq.get(f'{BASE}/fapi/v1/openInterest?{up.urlencode(p)}',
                       headers={'X-MBX-APIKEY': KEY}).json()
            coin_data['oi_contracts'] = float(r['openInterest'])
            coin_data['oi_value'] = coin_data['oi_contracts'] * float(EX.fetch_ticker(sym)['last'])
        except Exception as e:
            coin_data['oi_contracts'] = str(e)

        try:
            # 3. 多空持仓量比 (大户+散户按仓位加权)
            p = {'symbol': sym.replace('/USDT:USDT', 'USDT'),
                 'period': '5m', 'limit': 1,
                 'timestamp': int(time.time() * 1000)}
            q = up.urlencode(p)
            p['signature'] = hm.new(SEC.encode(), q.encode(), hl.sha256).hexdigest()
            r = rq.get(f'{BASE}/futures/data/topLongShortPositionRatio?{up.urlencode(p)}',
                       headers={'X-MBX-APIKEY': KEY}).json()
            if r:
                coin_data['ls_position_ratio'] = float(r[0]['longShortRatio'])
        except Exception as e:
            coin_data['ls_position_ratio'] = str(e)

        try:
            # 4. 大户账户多空比
            p = {'symbol': sym.replace('/USDT:USDT', 'USDT'),
                 'period': '5m', 'limit': 1,
                 'timestamp': int(time.time() * 1000)}
            q = up.urlencode(p)
            p['signature'] = hm.new(SEC.encode(), q.encode(), hl.sha256).hexdigest()
            r = rq.get(f'{BASE}/futures/data/topLongShortAccountRatio?{up.urlencode(p)}',
                       headers={'X-MBX-APIKEY': KEY}).json()
            if r:
                coin_data['ls_account_ratio'] = float(r[0]['longShortRatio'])
        except Exception as e:
            coin_data['ls_account_ratio'] = str(e)

        out['coins'][name] = coin_data

    with open(CACHE_FILE, 'w') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    return out


def summary():
    """人类/LLM可读的摘要"""
    if not os.path.exists(CACHE_FILE):
        fetch_all()
    with open(CACHE_FILE) as f:
        data = json.load(f)

    lines = [f"=== 市场增强数据 ({data['updated']}) ==="]
    lines.append(f"{'币种':>5} {'费率%':>8} {'OI(M)':>10} {'持仓多空比':>10} {'大户多空比':>10}")
    lines.append("-" * 50)

    for name in sorted(data['coins'].keys()):
        c = data['coins'][name]
        fr = c.get('funding_rate', '?')
        oi = c.get('oi_value', 0)
        ls_pos = c.get('ls_position_ratio', '?')
        ls_acc = c.get('ls_account_ratio', '?')

        fr_s = f"{fr:+.4f}" if isinstance(fr, (int, float)) else '?'
        oi_s = f"{oi/1e6:.1f}" if isinstance(oi, (int, float)) else '?'
        pos_s = f"{ls_pos:.2f}:1" if isinstance(ls_pos, (int, float)) else '?'
        acc_s = f"{ls_acc:.2f}:1" if isinstance(ls_acc, (int, float)) else '?'

        lines.append(f"{name:>5} {fr_s:>8} {oi_s:>10} {pos_s:>10} {acc_s:>10}")

    return '\n'.join(lines)


if __name__ == '__main__':
    fetch_all()
    print(summary())
