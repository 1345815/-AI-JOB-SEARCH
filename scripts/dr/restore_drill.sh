#!/usr/bin/env bash
# 备份恢复演练：最新备份 → 恢复副本 → 与原库行数对比
# 用法：
#   bash scripts/dr/restore_drill.sh --backup-dir web/data/backups --source-db web/data/careerpilot.db
#   bash scripts/dr/restore_drill.sh --backup-dir web/data/backups --source-db web/data/careerpilot.db --verify-only
set -euo pipefail

BACKUP_DIR=""
SOURCE_DB=""
VERIFY_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup-dir) BACKUP_DIR="$2"; shift 2 ;;
    --source-db) SOURCE_DB="$2"; shift 2 ;;
    --verify-only) VERIFY_ONLY=1; shift ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$BACKUP_DIR" && -n "$SOURCE_DB" ]] || { echo "必须提供 --backup-dir 与 --source-db"; exit 2; }

LATEST=$(ls -t "$BACKUP_DIR"/careerpilot-*.db 2>/dev/null | head -1)
[[ -n "$LATEST" ]] || { echo "备份目录无备份文件"; exit 1; }
echo "最新备份: $LATEST"

RESTORE_DIR="$(cd "$(dirname "$0")/../.." && pwd)/.pytest_tmp"
mkdir -p "$RESTORE_DIR"
RESTORE_TARGET="$RESTORE_DIR/dr_restore_$$.db"
# Git Bash 下把 /c/... 转成 C:/... 供 Python 使用
if command -v cygpath >/dev/null 2>&1; then
  RESTORE_TARGET="$(cygpath -w "$RESTORE_TARGET")"
  LATEST="$(cygpath -w "$LATEST")"
  SOURCE_DB="$(cygpath -w "$SOURCE_DB")"
fi
if [[ "$VERIFY_ONLY" -eq 1 ]]; then
  echo "[verify-only] 使用 verify_database 校验备份完整性（不恢复）"
  python -c "
import sys; sys.path.insert(0, 'web')
from db_backup import verify_database
import sys
r = verify_database('$LATEST')
print('integrity:', r['integrity'])
sys.exit(0 if r['integrity'] == 'ok' else 1)
" || exit 1
else
  echo "恢复到临时副本: $RESTORE_TARGET"
  DR_LATEST="$LATEST" DR_TARGET="$RESTORE_TARGET" python -c "
import os, sys; sys.path.insert(0, 'web')
from db_backup import restore_backup
restore_backup(os.environ['DR_LATEST'], os.environ['DR_TARGET'])
" || exit 1

  echo "行数对比（原库 vs 恢复副本）："
  DR_SRC="$SOURCE_DB" DR_DST="$RESTORE_TARGET" python -c "
import os, sqlite3, sys
src = sqlite3.connect(os.environ['DR_SRC'])
dst = sqlite3.connect(os.environ['DR_DST'])
tables = [r[0] for r in src.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'\")]
ok = True
for t in sorted(tables):
    try:
        a = src.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0]
        b = dst.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0]
        flag = 'OK ' if a == b else 'DIFF'
        if a != b: ok = False
        print('  %-24s src=%d dst=%d %s' % (t, a, b, flag))
    except Exception as e:
        print('  %-24s ERR %s' % (t, e))
src.close(); dst.close()
sys.exit(0 if ok else 1)
" || { echo "行数不一致，演练失败"; rm -f "$RESTORE_TARGET"; exit 1; }
fi

echo "演练通过 ✓"
[[ "$VERIFY_ONLY" -eq 0 ]] && rm -f "$RESTORE_TARGET"
exit 0
