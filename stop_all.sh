#!/bin/bash
# 手动停止全部自动交易（不会被自动重启）
WORKDIR="$(cd "$(dirname "$0")" && pwd)"
SENTINEL="/tmp/gateauto_manual_stop"

touch "$SENTINEL"
echo "[$(date '+%m-%d %H:%M:%S')] 已设置手动停止标记"

# 杀全部交易相关进程
PROCS=$(ps aux | grep -E 'gate_auto\.py|trail_monitor_gate\.py' | grep -v grep | awk '{print $2}')
if [ -n "$PROCS" ]; then
    echo "$PROCS" | while read pid; do
        kill "$pid" 2>/dev/null && echo "  已终止 PID $pid"
    done
    sleep 1
    # 强杀残留
    PROCS2=$(ps aux | grep -E 'gate_auto\.py|trail_monitor_gate\.py' | grep -v grep | awk '{print $2}')
    if [ -n "$PROCS2" ]; then
        echo "$PROCS2" | while read pid; do
            kill -9 "$pid" 2>/dev/null && echo "  强制终止 PID $pid"
        done
    fi
    echo "全部交易进程已停止"
else
    echo "没有运行中的交易进程"
fi
