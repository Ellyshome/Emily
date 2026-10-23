#!/usr/bin/env bash
# ============================================================
# Emily 服务器初始化（ECS / 单机 Docker）
#
# 做四件事：
#   1. 检查依赖与 .env 必填项
#   2. 创建运行态目录（仓库同级 Emily-var/）
#   3. 启动数据库与 emily-core（首次启动自动建表）
#   4. 按台账应用业务种子 SQL（可重复执行，已应用的会跳过）
#
# 用法（仓库根目录）：
#   bash deploy/prepare.sh
#
# 之后还需：创建首个管理员 + 启动网关 + 配置 nginx（见 deploy/README.md）
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
COMPOSE="docker-compose.server.yml"
SQL_DIR="emily-core/emily_core/infrastructure/database/scripts"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[警告] %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31m[错误] %s\033[0m\n' "$*" >&2; exit 1; }

# 从 .env 取变量（不 source，避免值里的空格/引号被 shell 解释）
env_get() {
  [ -f .env ] || return 0
  grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true
}

# ── 1. 依赖与配置 ──────────────────────────────────────────
say "检查依赖与配置"
[ -f "$COMPOSE" ] || fail "未找到 $COMPOSE（请在仓库根目录执行）"
command -v docker >/dev/null 2>&1 || fail "未安装 docker"
docker compose version >/dev/null 2>&1 || fail "未安装 docker compose v2（需要 docker compose 子命令）"

if [ ! -f .env ]; then
  cp .env.example .env
  fail "已从 .env.example 生成 .env。请填写 POSTGRES_PASSWORD / EMILY_API_TOKEN / EMILY_LLM_API_KEY 后重新执行本脚本"
fi

missing=()
for k in POSTGRES_PASSWORD EMILY_API_TOKEN EMILY_LLM_API_KEY; do
  v="$(env_get "$k")"
  if [ -z "$v" ] || [[ "$v" == \<* ]]; then
    missing+=("$k")
  fi
done
[ ${#missing[@]} -eq 0 ] || fail "以下必填项在 .env 中为空或仍是占位符：${missing[*]}"

case "$(env_get POSTGRES_PASSWORD)" in
  *[:@/]*) warn "POSTGRES_PASSWORD 含 : @ / 会破坏数据库 URL，请改用字母数字与 -_ 组合" ;;
esac

PG_USER="$(env_get POSTGRES_USER)"; PG_USER="${PG_USER:-emily}"
PG_DB="$(env_get POSTGRES_DB)";     PG_DB="${PG_DB:-emily}"
VAR_DIR="$(env_get VAR_DIR)";       VAR_DIR="${VAR_DIR:-../Emily-var}"

# ── 2. 运行态目录 ──────────────────────────────────────────
say "创建运行态目录：$VAR_DIR"
mkdir -p "$VAR_DIR"/{logs,attachments,session_archives,user_memory,journal,notebooks,runtime,state,evolution,knowledge_library,wechat-gateway-uploads}
printf '     %s\n' "$(cd "$VAR_DIR" && pwd)"
warn "knowledge_library 为空属正常：业务资料需另行上传（可沿用本地 Emily-var/knowledge_library 的内容）"

# ── 3. 启动数据库与 core ───────────────────────────────────
wait_healthy() {
  local name="$1" timeout="${2:-180}" waited=0 status
  while [ "$waited" -lt "$timeout" ]; do
    status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$name" 2>/dev/null || echo missing)"
    case "$status" in
      healthy) echo "    $name 就绪"; return 0 ;;
      exited|dead) fail "$name 已退出，请查看：docker logs --tail 100 $name" ;;
    esac
    sleep 2; waited=$((waited + 2))
  done
  fail "等待 $name 就绪超时（${timeout}s），请查看：docker logs --tail 100 $name"
}

say "构建镜像（首次较慢：apt 依赖 + OfficeCLI 下载）"
docker compose -f "$COMPOSE" build emily-core wechat-gateway

say "启动数据库"
docker compose -f "$COMPOSE" up -d emily-postgres
wait_healthy emily-postgres 120

say "启动 emily-core（首次启动会自动建表）"
docker compose -f "$COMPOSE" up -d emily-core
wait_healthy emily-core 240

# ── 4. 应用种子 SQL（台账去重）─────────────────────────────
say "应用业务种子 SQL"
PSQL=(docker compose -f "$COMPOSE" exec -T emily-postgres
      psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB")

"${PSQL[@]}" -q -c "CREATE TABLE IF NOT EXISTS schema_migrations (
    filename text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);"

apply_sql() {
  local file="$1" name
  name="$(basename "$file")"
  if [ -z "$("${PSQL[@]}" -tAc "SELECT filename FROM schema_migrations WHERE filename='$name'")" ]; then
    printf '    应用 %s ... ' "$name"
    "${PSQL[@]}" -q < "$file"
    "${PSQL[@]}" -q -c "INSERT INTO schema_migrations(filename) VALUES ('$name') ON CONFLICT DO NOTHING;"
    echo "完成"
  else
    echo "    跳过 $name（已应用）"
  fi
}

# 顺序：基础 SOP 登记 → 新增 SOP 登记（010~015，均 ON CONFLICT 幂等）→ 工具注册表
for f in \
  deploy/sql/001_seed_sop_flows.sql \
  "$SQL_DIR/010_set_knowledge_library_sop_min_level.sql" \
  "$SQL_DIR/011_register_video_download_sop.sql" \
  "$SQL_DIR/012_register_planting_schedule_review_sop.sql" \
  "$SQL_DIR/013_register_site_report_sop.sql" \
  "$SQL_DIR/014_register_traceability_sop.sql" \
  "$SQL_DIR/015_register_doc_reading_note_sop.sql" \
  "$SQL_DIR/seed_tool_registry.sql" ; do
  [ -f "$f" ] || fail "缺少 SQL 文件：$f"
  apply_sql "$f"
done

# ── 5. 汇总 ────────────────────────────────────────────────
say "校验关键表行数"
"${PSQL[@]}" -c "SELECT
    (SELECT count(*) FROM sop_business_flows) AS sop_flows,
    (SELECT count(*) FROM tool_registry)      AS tools,
    (SELECT count(*) FROM users)              AS users;"

say "初始化完成"
cat <<EOF

下一步：
  1) 创建首个管理员（--wechat 填小程序侧上报的用户标识；可先 --list 查看现状）
     docker compose -f $COMPOSE exec -T emily-core \\
       python /app/deploy/bootstrap_admin.py --username 管理员 --level 6 --wechat <微信用户标识>

  2) 启动全部服务
     docker compose -f $COMPOSE up -d

  3) 配置 nginx 反代与证书：见 deploy/README.md

  4) 验证
     curl -s -H "X-Emily-Token: \$(grep -E '^EMILY_API_TOKEN=' .env | cut -d= -f2-)" \\
       http://127.0.0.1:18080/api/v1/health
     curl -s http://127.0.0.1:18090/health
EOF
