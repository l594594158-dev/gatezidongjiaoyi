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


def _trade_log_path(coin):
    """根据币名返回交易日志路径"""
    # bnb_auto.py 使用 bnb_bn_trades.txt
    coin_lower = coin.lower()
    base = os.path.dirname(SIGNAL_DIR)
    return os.path.join(base, f'{coin_lower}_bn_trades.txt')


def _write_trade_log(coin, decision, reason, signal_data=None):
    """将LLM分析写入交易日志"""
    from datetime import datetime
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    label = 'LLM确认开仓' if decision == 'CONFIRMED' else 'LLM否决开仓'

    lines = [
        '═══════════════════════════════════',
        f'时间: {now}',
        f'操作: {label}',
        f'品种: {coin}',
    ]

    if signal_data:
        lines.append(f'方向: {signal_data.get("direction","?")}')
        lines.append(f'入场: {signal_data.get("entry_price","?")}  止损: {signal_data.get("stop_loss","?")}  止盈: {signal_data.get("take_profit","?")}')
        lines.append(f'仓位: {signal_data.get("qty","?")} × {signal_data.get("leverage","?")}x')

    lines.append(f'── LLM分析 ──')
    for line in reason.split('\n'):
        lines.append(f'  {line.strip()}')
    lines.append('═══════════════════════════════════')

    log_path = _trade_log_path(coin)
    with open(log_path, 'a') as f:
        f.write('\n'.join(lines) + '\n')

    # 同步写入统一LLM分析日志
    llm_log_path = os.path.join(os.path.dirname(_trade_log_path(coin)), 'llm_analysis.log')
    with open(llm_log_path, 'a') as f:
        f.write('\n'.join(lines) + '\n')


def llm_confirm(coin, reason=''):
    """LLM调用: 确认交易 → 写入response + 交易日志"""
    sig_file = os.path.join(SIGNAL_DIR, f'{coin}_signal.json')
    sig_data = {}
    ts = 0
    if os.path.exists(sig_file):
        with open(sig_file) as f:
            sig_data = json.load(f)
            ts = sig_data.get('timestamp', 0)

    resp = {
        'decision': 'CONFIRMED',
        'reason': reason,
        'signal_timestamp': ts,
        'timestamp': time.time(),
    }
    with open(os.path.join(SIGNAL_DIR, f'{coin}_response.json'), 'w') as f:
        json.dump(resp, f, indent=2, ensure_ascii=False)

    _write_trade_log(coin, 'CONFIRMED', reason, sig_data)
    return resp


def llm_reject(coin, reason=''):
    """LLM调用: 否决交易 → 写入response + 交易日志"""
    sig_file = os.path.join(SIGNAL_DIR, f'{coin}_signal.json')
    sig_data = {}
    ts = 0
    if os.path.exists(sig_file):
        with open(sig_file) as f:
            sig_data = json.load(f)
            ts = sig_data.get('timestamp', 0)

    resp = {
        'decision': 'REJECTED',
        'reason': reason,
        'signal_timestamp': ts,
        'timestamp': time.time(),
    }
    with open(os.path.join(SIGNAL_DIR, f'{coin}_response.json'), 'w') as f:
        json.dump(resp, f, indent=2, ensure_ascii=False)

    _write_trade_log(coin, 'REJECTED', reason, sig_data)
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
