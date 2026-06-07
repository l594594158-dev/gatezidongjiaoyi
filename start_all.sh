#!/bin/bash
# 启动全部自动交易（移除手动停止标记，启动全部8个进程）
WORKDIR="$(cd "$(dirname "$0")" && pwd)"
SENTINEL="/tmp/gateauto_manual_stop"
PYTHON="$WORKDIR/venv/bin/python3"

rm -f "$SENTINEL"
echo "[$(date '+%m-%d %H:%M:%S')] 已移除手动停止标记，开始启动..."

cd "$WORKDIR"

# 先检查哪些已在运行
ALREADY=$(ps aux | grep -cE 'gate_auto\.py|trail_monitor_gate\.py' | grep -v grep || echo 0)
if [ "$ALREADY" -gt 0 ]; then
    echo "⚠ 已有 $ALREADY 个进程在运行，先不重复启动"
    echo "  如需重启请先执行 ./stop_all.sh"
    exit 1
fi

# 启动4个gate_auto
for coin in btc eth sol bnb; do
    nohup "$PYTHON" -u "${coin}_gate_auto.py" >> "${coin}_gate_stdout.log" 2>&1 &
    echo "  ${coin}_gate_auto.py  PID $!"
    sleep 1
done

# 启动4个trail_monitor
for coin in BTC ETH SOL BNB; do
    nohup "$PYTHON" -u trail_monitor_gate.py "$coin" >> "trail_${coin,,}.log" 2>&1 &
    echo "  trail_monitor $coin  PID $!"
    sleep 1
done

echo ""
echo "全部8个进程已启动"
echo "查看状态: ps aux | grep -E 'gate_auto|trail_monitor'"
