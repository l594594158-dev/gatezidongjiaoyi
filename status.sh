#!/bin/bash
# 查看自动交易运行状态
echo "═════════════════════════════════════════"
echo "  自动交易状态 ($(date '+%m-%d %H:%M:%S'))"
echo "═════════════════════════════════════════"

SENTINEL="/tmp/gateauto_manual_stop"
if [ -f "$SENTINEL" ]; then
    echo "⚠ 手动停止模式: 已启用 (不会自动重启)"
else
    echo "✅ 自动重启: 已启用"
fi
echo ""

echo "进程:"
ps aux | grep -E 'gate_auto\.py|trail_monitor_gate\.py' | grep -v grep | awk '{
    for(i=11;i<=NF;i++) cmd=cmd" "$i;
    printf "  PID %-7s %s\n", $2, cmd;
    cmd="";
}'

COUNT=$(ps aux | grep -cE 'gate_auto\.py|trail_monitor_gate\.py' | grep -v grep || echo 0)
echo ""
echo "进程数: $COUNT/8"
echo "═════════════════════════════════════════"
