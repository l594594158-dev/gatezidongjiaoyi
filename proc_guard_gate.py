#!/usr/bin/env python3
"""
进程守护 (Gate.io版) v2:
  - 每1分钟检查8个进程是否存活
  - 异常停止/自然停止 → 自动重启
  - 手动停止标记存在时 → 不重启
  - 超出8个 → 清理僵尸进程

手动停止: touch /tmp/gateauto_manual_stop
恢复自动: rm /tmp/gateauto_manual_stop
"""
import subprocess, os, sys, time
from datetime import datetime

WORKDIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(WORKDIR, 'proc_guard_gate.log')
SENTINEL = '/tmp/gateauto_manual_stop'

# 期望的8个进程: (脚本名, 参数, 日志文件)
EXPECTED = [
    ('btc_gate_auto.py',      [],  'btc_gate_stdout.log'),
    ('eth_gate_auto.py',      [],  'eth_gate_stdout.log'),
    ('sol_gate_auto.py',      [],  'sol_gate_stdout.log'),
    ('bnb_gate_auto.py',      [],  'bnb_gate_stdout.log'),
    ('trail_monitor_gate.py', ['BTC'], 'trail_btc.log'),
    ('trail_monitor_gate.py', ['ETH'], 'trail_eth.log'),
    ('trail_monitor_gate.py', ['SOL'], 'trail_sol.log'),
    ('trail_monitor_gate.py', ['BNB'], 'trail_bnb.log'),
]

TARGET = len(EXPECTED)
PYTHON = os.path.join(WORKDIR, 'venv', 'bin', 'python3')

def log(msg):
    line = f"[{datetime.now().strftime('%m-%d %H:%M:%S')}] {msg}"
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')

def get_running():
    """返回 {(script, args_key): [pids]}"""
    result = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5)
    running = {}
    for line in result.stdout.strip().split('\n'):
        if 'python3' not in line:
            continue
        parts = line.split()
        try:
            pid = int(parts[1])
        except (IndexError, ValueError):
            continue
        cmdline = ' '.join(parts[10:])
        # 匹配预期的进程
        for script, args, _logfile in EXPECTED:
            if script in cmdline:
                # 对于 trail_monitor，还要匹配参数
                if script == 'trail_monitor_gate.py':
                    for arg in args:
                        if arg not in cmdline:
                            break
                    else:
                        key = (script, ' '.join(args))
                        running.setdefault(key, []).append(pid)
                        break
                else:
                    key = (script, '')
                    running.setdefault(key, []).append(pid)
                    break
    return running

def main():
    manual_stop = os.path.exists(SENTINEL)

    running = get_running()

    # 统计
    total_pids = sum(len(v) for v in running.values())

    # 1. 清理超标僵尸进程 (同一个key下多余进程)
    killed = []
    for key, pids in running.items():
        if len(pids) > 1:
            # 保留最老的，杀更新的
            pids.sort()
            for pid in pids[1:]:
                try:
                    os.kill(pid, 9)
                    killed.append(pid)
                except ProcessLookupError:
                    pass

    # 2. 检查缺失进程 → 自动重启
    restarted = []
    skipped = []
    for script, args, logfile in EXPECTED:
        key = (script, ' '.join(args))
        if key in running and len(running[key]) > 0:
            continue  # 已经在运行

        if manual_stop:
            skipped.append(script)
            continue

        # 重启
        try:
            cmd = [PYTHON, '-u', os.path.join(WORKDIR, script)] + args
            log_path = os.path.join(WORKDIR, logfile)
            with open(log_path, 'a') as f:
                subprocess.Popen(
                    cmd,
                    cwd=WORKDIR,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            restarted.append(script)
        except Exception as e:
            log(f'RESTART FAIL {script}: {e}')

    # 3. 写日志
    parts = []
    if killed:
        parts.append(f'KILLED {len(killed)} zombie(s): {killed}')
    if restarted:
        parts.append(f'RESTARTED {len(restarted)}: {restarted}')
    if skipped:
        parts.append(f'SKIPPED {len(skipped)} (manual stop): {skipped}')
    if not parts:
        parts.append(f'OK ({total_pids}/{TARGET} running)')

    log(' | '.join(parts))

if __name__ == '__main__':
    main()
