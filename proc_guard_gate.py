#!/usr/bin/env python3
"""
进程守护 (Gate.io版): 每1分钟检查，超出8个进程自动杀。
目标: 4个主策略 + 4个移动止盈监控 = 8个进程
"""
import subprocess, os, sys
from datetime import datetime

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'proc_guard_gate.log')

def log(msg):
    line = f"[{datetime.now().strftime('%m-%d %H:%M:%S')}] {msg}"
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')

TARGET = 8
SCRIPTS = ['btc_gate_auto.py', 'eth_gate_auto.py', 'sol_gate_auto.py',
           'bnb_gate_auto.py', 'trail_monitor_gate.py']

try:
    result = subprocess.run(
        ['ps', 'aux'], capture_output=True, text=True, timeout=5
    )
    lines = result.stdout.strip().split('\n')

    pids = []
    for line in lines:
        if 'python3' not in line and 'python' not in line:
            continue
        if any(s in line for s in SCRIPTS):
            parts = line.split()
            pid = int(parts[1])
            pids.append(pid)

    if len(pids) > TARGET:
        pids.sort(reverse=True)
        keep = pids[:TARGET]
        kill = pids[TARGET:]

        for pid in kill:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass

        log(f'KILLED {len(kill)} zombie(s): {kill} | kept: {keep}')

except Exception as e:
    log(f'ERROR: {e}')
