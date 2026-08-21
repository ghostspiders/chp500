#!/usr/bin/env bash
# =====================================================================
# CHP500 常年运行调度（系统 cron，无需常驻进程）
#
# 用法：
#   ./scripts/scheduler.sh install    # 将下列 crontab 写入当前用户
#   ./scripts/scheduler.sh show       # 仅打印 crontab 片段
#   ./scripts/scheduler.sh remove     # 移除写入的片段
#
# 节奏对齐 config.yaml：rebalance_freq=quarterly / iwf_refresh=weekly
#   - 每日补点净值（交易日收盘后）
#   - 每周 IWF/股本刷新（调除数保持连续）
#   - 季度再平衡（1/4/7/10 月 1 日）
#   - 基准刷新（沪深300，每日）
#
# 幂等：每次运行都「覆盖式重算 + 写库」，可重跑、可补跑。
# =====================================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="${CHP500_PYTHON:-python3}"
OUT="$REPO_DIR/outputs"
TAG="# CHP500 scheduler (managed by scripts/scheduler.sh)"

CRON=$(cat <<EOF
$TAG
# 每日补点指数净值（交易日 16:30 后；周一至周五）
30 16 * * 1-5  cd $REPO_DIR && $PY scripts/build_index.py --daily --universe expanded >> $OUT/cron.log 2>&1
# 每周 IWF/股本刷新（周一 17:00；重算快照并调除数保持连续）
0 17 * * 1     cd $REPO_DIR && $PY scripts/build_index.py --iwf-refresh --universe expanded >> $OUT/cron.log 2>&1
# 季度再平衡（1/4/7/10 月 1 日 18:00）
0 18 1 1,4,7,10 *  cd $REPO_DIR && $PY scripts/build_index.py --rebalance --universe expanded >> $OUT/cron.log 2>&1
# 基准刷新（沪深300，每日 17:30）
30 17 * * 1-5  cd $REPO_DIR && $PY scripts/build_index.py --benchmarks --universe expanded >> $OUT/cron.log 2>&1
EOF
)

case "${1:-show}" in
  show)
    echo "$CRON"
    ;;
  install)
    ( crontab -l 2>/dev/null | grep -v "$TAG" ; echo "$CRON" ) | crontab -
    echo "已写入 crontab。可用 'crontab -l' 查看。"
    ;;
  remove)
    crontab -l 2>/dev/null | grep -v "$TAG" | crontab -
    echo "已移除 CHP500 调度片段。"
    ;;
  *)
    echo "用法: $0 {install|show|remove}" >&2
    exit 1
    ;;
esac
