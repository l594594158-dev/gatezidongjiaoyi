#!/usr/bin/env python3
"""
进程守护: 每1分钟检查自动交易进程，多余进程自动杀。
目标: 9个进程(每品种1个)，超出部分按PID排序保留最新9个。
"""
import subprocess, os, sys
from datetime import datetime

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'proc_guard.log')

def log(msg):
    line = f"[{datetime.now().strftime('%m-%d %H:%M:%S')}] {msg}"
    # Only log when action taken (killed processes) — silent mode otherwise
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')

TARGET = 9
SCRIPTS = ['btc_bnb_auto.py', 'hype_bnb_auto.py', 'zec_bnb_auto.py',
           'near_bnb_auto.py', 'xlm_bnb_auto.py', 'wld_bnb_auto.py',
           'ena_bnb_auto.py', 'sui_bnb_auto.py', 'bnb_auto.py']

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
        # Sort by PID descending (newest first), keep top TARGET
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
