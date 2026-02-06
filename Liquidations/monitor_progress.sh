#!/bin/bash
# 监控批量获取进度

echo "Kamino 数据获取进度监控"
echo "========================"
echo ""

# 检查最新的文件夹
LATEST_DATA=$(ls -td kamino_data_7d_* 2>/dev/null | head -1)
LATEST_LIQ=$(ls -td kamino_liquidations_* 2>/dev/null | head -1)

if [ -n "$LATEST_DATA" ]; then
    echo "数据文件夹: $LATEST_DATA"
    BATCH_COUNT=$(ls -1 "$LATEST_DATA"/*.json 2>/dev/null | wc -l)
    echo "  已保存批次: $BATCH_COUNT 个"
    echo "  预计交易数: $((BATCH_COUNT * 1000)) 笔"
fi

if [ -n "$LATEST_LIQ" ]; then
    echo ""
    echo "清算文件夹: $LATEST_LIQ"
    LIQ_COUNT=$(ls -1 "$LATEST_LIQ"/*.json 2>/dev/null | wc -l)
    echo "  已发现清算: $LIQ_COUNT 笔 🎯"

    if [ $LIQ_COUNT -gt 0 ]; then
        echo ""
        echo "清算列表:"
        ls -lht "$LATEST_LIQ"/*.json | head -5
    fi
fi

echo ""
echo "后台任务状态:"
ps aux | grep "batch_fetch_kamino_full.py" | grep -v grep

echo ""
echo "========================"
echo "按 Ctrl+C 退出监控"
