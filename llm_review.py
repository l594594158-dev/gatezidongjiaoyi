#!/usr/bin/env python3
"""
LLM二次确认模块: bot信号触发 → 写数据 → LLM分析 → 回写决策 → bot执行
集成到每个bot的open_position流程中。
"""
import json, os, time

SIGNAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'signals')
os.makedirs(SIGNAL_DIR, exist_ok=True)


def submit_signal(coin, direction, entry_price, stop_loss, take_profit, qty, leverage, analysis):
    """bot调用: 提交信号等待LLM确认"""
    signal = {
        'coin': coin,
        'direction': direction,
        'entry_price': round(entry_price, 4),
        'stop_loss': round(stop_loss, 4),
        'take_profit': round(take_profit, 4),
        'qty': qty,
        'leverage': leverage,
        'analysis': analysis,  # 指标分析摘要
        'timestamp': time.time(),
        'status': 'pending',
    }

    # 加载市场增强数据
    enrich_file = os.path.join(os.path.dirname(SIGNAL_DIR), 'market_enrich.json')
    if os.path.exists(enrich_file):
        with open(enrich_file) as f:
            enrich = json.load(f)
            if coin in enrich.get('coins', {}):
                signal['enrich'] = enrich['coins'][coin]

    filepath = os.path.join(SIGNAL_DIR, f'{coin}_signal.json')
    with open(filepath, 'w') as f:
        json.dump(signal, f, indent=2, ensure_ascii=False)

    return filepath


def check_response(coin):
    """bot调用: 检查LLM是否已回复"""
    resp_file = os.path.join(SIGNAL_DIR, f'{coin}_response.json')
    if not os.path.exists(resp_file):
        return None

    with open(resp_file) as f:
        resp = json.load(f)

    # 检查是否是对当前信号的回复(时间戳匹配)
    sig_file = os.path.join(SIGNAL_DIR, f'{coin}_signal.json')
    if os.path.exists(sig_file):
        with open(sig_file) as f:
            sig = json.load(f)
        if resp.get('signal_timestamp') != sig.get('timestamp'):
            return None  # 过期回复

    return resp


def llm_confirm(coin, reason=''):
    """LLM调用: 确认交易"""
    sig_file = os.path.join(SIGNAL_DIR, f'{coin}_signal.json')
    ts = 0
    if os.path.exists(sig_file):
        with open(sig_file) as f:
            ts = json.load(f).get('timestamp', 0)

    resp = {
        'decision': 'CONFIRMED',
        'reason': reason,
        'signal_timestamp': ts,
        'timestamp': time.time(),
    }
    with open(os.path.join(SIGNAL_DIR, f'{coin}_response.json'), 'w') as f:
        json.dump(resp, f, indent=2, ensure_ascii=False)
    return resp


def llm_reject(coin, reason=''):
    """LLM调用: 否决交易"""
    sig_file = os.path.join(SIGNAL_DIR, f'{coin}_signal.json')
    ts = 0
    if os.path.exists(sig_file):
        with open(sig_file) as f:
            ts = json.load(f).get('timestamp', 0)

    resp = {
        'decision': 'REJECTED',
        'reason': reason,
        'signal_timestamp': ts,
        'timestamp': time.time(),
    }
    with open(os.path.join(SIGNAL_DIR, f'{coin}_response.json'), 'w') as f:
        json.dump(resp, f, indent=2, ensure_ascii=False)
    return resp


def list_pending():
    """列出所有待确认信号"""
    pending = []
    if not os.path.exists(SIGNAL_DIR):
        return pending
    for f in os.listdir(SIGNAL_DIR):
        if f.endswith('_signal.json'):
            filepath = os.path.join(SIGNAL_DIR, f)
            with open(filepath) as fp:
                sig = json.load(fp)
            coin = f.replace('_signal.json', '')
            resp_file = os.path.join(SIGNAL_DIR, f'{coin}_response.json')
            if not os.path.exists(resp_file):
                pending.append(sig)
    return pending
