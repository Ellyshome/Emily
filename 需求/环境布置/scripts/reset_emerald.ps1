# ============================================================
# reset_emerald.ps1 —— 一键重置翠湖庭院测试环境
#
# 用法（在项目根目录 d:\app\Emily 下执行）:
#   pwsh -File 需求\环境布置\scripts\reset_emerald.ps1
#
# 说明:
#   1. 停服并清除 PostgreSQL 数据卷
#   2. 重启 Docker 服务
#   3. 等待 emily-core 启动完成并自动建表
#   4. 按顺序导入全部种子数据
#   5. 运行验证查询
#
# 参数:
#   -SkipAdvanced  跳过高级数据导入（阶段三: WorldBook/进化/日志等）
#   -SkipData      仅重置基础 schema，跳过所有种子数据（空库启动）
# ============================================================
param(
    [switch]$SkipAdvanced,
    [switch]$SkipData
)

$ErrorActionPreference = "Stop"
$BASE = "emily-core/emily_core/infrastructure/database/scripts"
$SEED = "需求/环境布置/scripts"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  翠湖庭院 EMERALD-01 环境重置" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ============================================================
# 阶段一: 重置数据库
# ============================================================
Write-Host "[1/6] 停服并清除数据卷..." -ForegroundColor Yellow
docker compose -f docker-compose-napcat.yml down -v 2>&1 | Out-Null
Write-Host "  [OK] 服务已停止，数据卷已清除" -ForegroundColor Green

Write-Host "[2/6] 启动 Docker 服务..." -ForegroundColor Yellow
docker compose -f docker-compose-napcat.yml up -d 2>&1 | Out-Null
Write-Host "  [OK] 服务已启动" -ForegroundColor Green

if ($SkipData) {
    Write-Host ""
    Write-Host "已跳过种子数据导入（仅空库 + schema）" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "验证:" -ForegroundColor Cyan
    docker exec emily-postgres psql -U emily -d emily -c "SELECT count(*) AS tables_count FROM information_schema.tables WHERE table_schema='public';"
    exit 0
}

# 等待 emily-core bootstrap 完成建表
Write-Host "[3/6] 等待 emily-core 启动并自动建表..." -ForegroundColor Yellow
$maxWait = 30
for ($i = 1; $i -le $maxWait; $i++) {
    Start-Sleep -Seconds 2
    $result = docker exec emily-postgres psql -U emily -d emily -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>$null
    if ($result -ne $null -and $result.Trim() -ne "") {
        $tableCount = [int]$result.Trim()
        if ($tableCount -gt 40) {
            Write-Host "  [OK] emily-core 已启动，检测到 $tableCount 张表" -ForegroundColor Green
            break
        }
    }
    Write-Host "  ... 等待中 ($i/$maxWait)" -ForegroundColor DarkGray
}
if ($i -gt $maxWait) {
    Write-Host "  [WARN] 建表可能尚未完成，继续导入..." -ForegroundColor Yellow
}

# ============================================================
# 阶段二: 导入基础种子数据
# ============================================================
Write-Host ""
Write-Host "[4/6] 导入基础种子数据..." -ForegroundColor Yellow

Write-Host "  [4.1] 公司 + 用户（002）..." -ForegroundColor DarkGray
Get-Content "$BASE/002_seed_test_data.sql" | docker exec -i emily-postgres psql -U emily -d emily 2>&1 | Out-Null
Write-Host "    [OK] 5家公司 + 7名用户" -ForegroundColor Green

Write-Host "  [4.2] 补充用户（patch）..." -ForegroundColor DarkGray
Get-Content "$SEED/002_seed_test_data_patch.sql" | docker exec -i emily-postgres psql -U emily -d emily 2>&1 | Out-Null
Write-Host "    [OK] 新增3名用户（L5/L2/L2）" -ForegroundColor Green

Write-Host "  [4.3] 项目 + 文件（007）..." -ForegroundColor DarkGray
Get-Content "$SEED/007_seed_emerald_project.sql" | docker exec -i emily-postgres psql -U emily -d emily 2>&1 | Out-Null
Write-Host "    [OK] EMERALD-01项目 + 5指标 + 18文件" -ForegroundColor Green

Write-Host "  [4.4] 全景节点树（008）..." -ForegroundColor DarkGray
$env:PYTHONPATH = "emily-core"
uv run python scripts/manage_nodes.py create --file "$SEED/008_seed_emerald_nodes.yaml" 2>&1 | Out-Null
Write-Host "    [OK] 节点树创建完成" -ForegroundColor Green

Write-Host "  [4.5] 业务数据（009）..." -ForegroundColor DarkGray
Get-Content "$SEED/009_seed_emerald_business.sql" | docker exec -i emily-postgres psql -U emily -d emily 2>&1 | Out-Null
Write-Host "    [OK] 事件/任务/会议/流转单/指令单/计划/会话/消息" -ForegroundColor Green

Write-Host "  [4.6] 权限体系（006）..." -ForegroundColor DarkGray
Get-Content "$BASE/006_seed_permission_data.sql" | docker exec -i emily-postgres psql -U emily -d emily 2>&1 | Out-Null
Write-Host "    [OK] 权限组 + SOP + 绑定 + 授权" -ForegroundColor Green

# ============================================================
# 阶段三: 高级数据（可选）
# ============================================================
if (-not $SkipAdvanced) {
    Write-Host ""
    Write-Host "[5/6] 导入高级种子数据..." -ForegroundColor Yellow

    if (Test-Path "$SEED/010_seed_emerald_advanced.sql") {
        Write-Host "  [5.1] World Book + 进化 + 调度器..." -ForegroundColor DarkGray
        Get-Content "$SEED/010_seed_emerald_advanced.sql" | docker exec -i emily-postgres psql -U emily -d emily 2>&1 | Out-Null
        Write-Host "    [OK]" -ForegroundColor Green
    } else {
        Write-Host "  [5.1] 010_seed_emerald_advanced.sql 不存在，已跳过" -ForegroundColor DarkGray
    }

    if (Test-Path "$SEED/011_seed_emerald_logs.sql") {
        Write-Host "  [5.2] LLM + Pipeline + Feedback 日志..." -ForegroundColor DarkGray
        Get-Content "$SEED/011_seed_emerald_logs.sql" | docker exec -i emily-postgres psql -U emily -d emily 2>&1 | Out-Null
        Write-Host "    [OK]" -ForegroundColor Green
    } else {
        Write-Host "  [5.2] 011_seed_emerald_logs.sql 不存在，已跳过" -ForegroundColor DarkGray
    }
} else {
    Write-Host ""
    Write-Host "[5/6] 已跳过高级数据导入（-SkipAdvanced）" -ForegroundColor Yellow
}

# ============================================================
# 验证
# ============================================================
Write-Host ""
Write-Host "[6/6] 验证数据完整性..." -ForegroundColor Yellow
Write-Host ""

docker exec emily-postgres psql -U emily -d emily -c "
SELECT '--- 数据统计 ---' AS section
UNION ALL
SELECT 'users(' || count(*)::text || ')' FROM users WHERE is_deleted = false
UNION ALL
SELECT 'company_info(' || count(*)::text || ')' FROM company_info WHERE is_deleted = false
UNION ALL
SELECT 'projects(' || count(*)::text || ')' FROM projects WHERE is_deleted = false
UNION ALL
SELECT 'project_nodes(' || count(*)::text || ')' FROM project_nodes WHERE project_id = (SELECT id FROM projects WHERE code = 'EMERALD-01' AND is_deleted = false)
UNION ALL
SELECT 'node_deliverables(' || count(*)::text || ')' FROM node_deliverables
UNION ALL
SELECT 'files(' || count(*)::text || ')' FROM files WHERE is_deleted = false
UNION ALL
SELECT 'events(' || count(*)::text || ')' FROM events
UNION ALL
SELECT 'tasks(' || count(*)::text || ')' FROM tasks
UNION ALL
SELECT 'meetings(' || count(*)::text || ')' FROM meetings
UNION ALL
SELECT 'business_flow_orders(' || count(*)::text || ')' FROM business_flow_orders
UNION ALL
SELECT 'instruction_orders(' || count(*)::text || ')' FROM instruction_orders
UNION ALL
SELECT 'plan_items(' || count(*)::text || ')' FROM plan_items
UNION ALL
SELECT 'conversations(' || count(*)::text || ')' FROM conversations
UNION ALL
SELECT 'messages(' || count(*)::text || ')' FROM messages;
"

Write-Host ""
docker exec emily-postgres psql -U emily -d emily -c "
SELECT u.username, u.level,
    CASE u.level
        WHEN 1 THEN '访客' WHEN 2 THEN '参建执行'
        WHEN 3 THEN '参建管理' WHEN 4 THEN '建设主管'
        WHEN 5 THEN '管理员' WHEN 6 THEN '系统管理员'
        ELSE '未知'
    END AS level_name,
    b.im_display_name AS name
FROM users u
LEFT JOIN user_im_bindings b ON u.id = b.user_id AND b.im_platform = 'simulator'
WHERE u.is_deleted = false
ORDER BY u.level DESC;
"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  翠湖庭院 EMERALD-01 环境部署完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "快速测试:" -ForegroundColor Yellow
Write-Host '  uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我查一下翠湖庭院项目的整体进度情况" --sender "李景利"' -ForegroundColor Gray
