﻿# ============================================================
# setup_test_env.ps1 — Emily 测试环境一键工具
#
# 用法（在项目根目录 d:\app\Emily 下执行）:
#   powershell -File .claude\tool\env-test\setup_test_env.ps1                  完整重置+种子+文件+权限+RAG库
#   powershell -File .claude\tool\env-test\setup_test_env.ps1 -Recreate       删库重建（结构层重置）+ 种子等全流程
#   powershell -File .claude\tool\env-test\setup_test_env.ps1 -ResetOnly       仅空库重置（TRUNCATE，数据层）
#   powershell -File .claude\tool\env-test\setup_test_env.ps1 -SeedOnly        仅种子（库已空）
#   powershell -File .claude\tool\env-test\setup_test_env.ps1 -SkipAdvanced    跳过010高级数据
#   powershell -File .claude\tool\env-test\setup_test_env.ps1 -SkipMockFiles   跳过磁盘空文件
#   powershell -File .claude\tool\env-test\setup_test_env.ps1 -SkipFileMgmtTests  跳过文件管理测试数据
#   powershell -File .claude\tool\env-test\setup_test_env.ps1 -SkipRAG         跳过 RAG 知识库阶段
#   powershell -File .claude\tool\env-test\setup_test_env.ps1 -SkipAlign       跳过存量归属对齐阶段
#
# 归属对齐（为何默认开）:
#   种子步骤 [11]（007_migrate_project_events.sql）按**特性前**口径把暂不知去向的事件
#   挂到全局假节点 node_id='UNASSIGNED'。现口径（US-15.9 / 15.10）要求事件归属必须指向
#   **本项目真实存在的收容节点**。故 seed 完成后统一跑一次对齐脚本（幂等、可重复执行），
#   使布置出的环境满足验收口径；verify_data.sql 亦据此断言「归属非法 = 0」。
#
# 重置层级（两个正交概念，勿混淆）:
#   数据层重置 : 默认流程 / -ResetOnly —— 跑 000_reset_all.sql 做 TRUNCATE。
#                前提是「表结构已存在且健康」；只清数据，不动结构。
#   结构层重建 : -Recreate —— DROP DATABASE + CREATE DATABASE，交给 emily-core
#                启动时 create_all 从零建表。用于结构缺失/损坏，或需要回归验证
#                「从零建表」链路的场景。
#   ⚠️ 以上两者都不重做 initdb。若 PG 存储层已损坏（WAL / 系统目录不一致），
#      需停容器 → 删除/改名 emily-data/postgres_data → 重启 emily-postgres
#      重新 initdb，之后再用本脚本灌种子。
#
# 依赖:
#   - Docker Desktop 运行中（emily-postgres + emily-core 容器）
#   - uv（Python 环境管理，用于 manage_nodes.py / align_unclassified_events.py / rag_test_harness.py）
#   - PowerShell 5.1+
# ============================================================
param(
    [switch]$Recreate,
    [switch]$ResetOnly,
    [switch]$SeedOnly,
    [switch]$SkipAdvanced,
    [switch]$SkipMockFiles,
    [switch]$SkipFileMgmtTests,
    [switch]$SkipRAG,
    [switch]$SkipAlign,
    [string]$Project = "EMERALD-01"
)

$ErrorActionPreference = "Continue"

# ── 终端 & 环境 UTF-8 编码（避免 docker exec psql 中文乱码）──
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:LESSCHARSET = "utf-8"

# ── 路径常量（相对于项目根目录 d:\app\Emily）──
$BASE = "emily-core/emily_core/infrastructure/database/scripts"
$COMPOSE_FILE = "docker-compose-napcat.yml"
$ATTACHMENTS_ROOT = "emily-data/attachments"
$ENV_TOOL = ".claude/tool/env-test"

# ============================================================
# 工具函数：执行 SQL 文件
# ============================================================
function ExecSql {
    param([string]$SqlFilePath)

    if (-not (Test-Path $SqlFilePath)) {
        Write-Host "[ERROR] SQL 文件不存在: $SqlFilePath" -ForegroundColor Red
        exit 1
    }

    # 通过 docker cp 将文件拷贝进容器再执行，避免 PowerShell 管道损坏 UTF-8 编码
    $tmpName = "/tmp/emily_seed_$(Get-Random).sql"
    docker cp $SqlFilePath "emily-postgres:${tmpName}" 2>$null
    $output = docker exec emily-postgres psql -U emily -d emily -f $tmpName 2>&1
    docker exec emily-postgres rm -f $tmpName 2>$null

    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] SQL 执行失败: $SqlFilePath" -ForegroundColor Red
        Write-Host $output -ForegroundColor Red
        exit 1
    }
}

# ============================================================
# 功能零：结构层重建（DROP DATABASE → CREATE DATABASE → 等自动建表）
#   与 Invoke-ResetDatabase（TRUNCATE 数据层重置）正交：
#   用于「结构缺失/损坏」，或需要回归验证「从零建表」链路的场景。
#   ⚠️ 不重做 initdb：仍沿用现有 postgres 数据目录；存储层已损坏时
#      需先删除/改名 emily-data/postgres_data 再重启 emily-postgres。
# ============================================================
function Invoke-RecreateDatabase {
    Write-Host "[重建] 结构层重建：DROP + CREATE DATABASE，由 emily-core 从零建表..." -ForegroundColor Yellow

    # 确保 postgres 容器在运行
    $pgRunning = docker inspect -f '{{.State.Running}}' emily-postgres 2>$null
    if ($pgRunning -ne 'true') {
        Write-Host "  [等待] 启动 emily-postgres 容器..." -ForegroundColor DarkGray
        docker compose -f $COMPOSE_FILE up -d emily-postgres 2>$null | Out-Null
        Start-Sleep -Seconds 3
    }

    # 先停 emily-core：释放连接池，否则 DROP DATABASE 会被活动连接阻塞
    $coreRunning = docker inspect -f '{{.State.Running}}' emily-core 2>$null
    if ($coreRunning -eq 'true') {
        docker compose -f $COMPOSE_FILE stop emily-core 2>$null | Out-Null
    }
    # 兜底：踢掉残留连接
    docker exec emily-postgres psql -U emily -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'emily' AND pid <> pg_backend_pid();" 2>$null | Out-Null

    Write-Host "  [执行] DROP DATABASE emily → CREATE DATABASE emily..." -ForegroundColor DarkGray
    docker exec emily-postgres psql -U emily -d postgres -c "DROP DATABASE IF EXISTS emily;" 2>$null | Out-Null
    docker exec emily-postgres psql -U emily -d postgres -c "CREATE DATABASE emily OWNER emily;" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] 重建数据库失败，请检查 emily-postgres 容器状态" -ForegroundColor Red
        exit 1
    }

    # 启动 emily-core，等待 bootstrap 自动建表（create_all）
    Write-Host "  [启动] emily-core 并等待自动建表..." -ForegroundColor DarkGray
    docker compose -f $COMPOSE_FILE up -d emily-core 2>$null | Out-Null

    $tableCount = 0
    for ($i = 1; $i -le 30; $i++) {
        Start-Sleep -Seconds 2
        # docker exec 可能返回多行（含空行）；取首个非空纯数字行，避免数组转 int 失败
        $raw = docker exec emily-postgres psql -U emily -d emily -t -A -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>$null
        $txt = ($raw | Where-Object { $_ -and "$_".Trim() -ne "" } | Select-Object -First 1)
        if ($txt -and "$txt".Trim() -match '^\d+$') {
            $tableCount = [int]"$txt".Trim()
            if ($tableCount -gt 40) { break }
        }
    }
    if ($tableCount -le 40) {
        Write-Host "  [WARN] 建表未达预期（当前 $tableCount 张），emily-core 最近错误:" -ForegroundColor Yellow
        docker logs --tail 30 emily-core 2>&1 | Select-String -Pattern "Database init failed|ERROR" | Select-Object -Last 5
    } else {
        Write-Host "  [OK] 数据库已重建并自动建表: $tableCount 张" -ForegroundColor Green
    }

    # ── 增量迁移校验（表数量达标 ≠ 增量迁移已生效）──
    # 若 emily-core 启动时 postgres 仍在崩溃恢复，bootstrap 会记 "Database init failed"
    # 后继续启动，session.py 的 _PENDING_COLUMNS / _PENDING_INDEXES 就不会执行——
    # 表现为「表都在，但新增列 / 部分唯一索引缺失」。表数量检查覆盖不到这种情形，
    # 故此处显式校验本模块依赖的两列与两个部分唯一索引。
    if ($tableCount -gt 40) {
        $colRaw = docker exec emily-postgres psql -U emily -d emily -t -A -c "SELECT count(*) FROM information_schema.columns WHERE table_name='project_nodes' AND column_name IN ('node_role','planned_start_at');" 2>$null
        $colTxt = ($colRaw | Where-Object { $_ -and "$_".Trim() -ne "" } | Select-Object -First 1)
        $colCount = if ($colTxt -and "$colTxt".Trim() -match '^\d+$') { [int]"$colTxt".Trim() } else { 0 }

        if ($colCount -lt 2) {
            Write-Host "  [WARN] project_nodes 增量列缺失（$colCount/2）——疑似 DB 初始化未完成即启动，重启 emily-core 补齐..." -ForegroundColor Yellow
            docker compose -f $COMPOSE_FILE restart emily-core 2>$null | Out-Null
            Start-Sleep -Seconds 12
            $colRaw = docker exec emily-postgres psql -U emily -d emily -t -A -c "SELECT count(*) FROM information_schema.columns WHERE table_name='project_nodes' AND column_name IN ('node_role','planned_start_at');" 2>$null
            $colTxt = ($colRaw | Where-Object { $_ -and "$_".Trim() -ne "" } | Select-Object -First 1)
            $colCount = if ($colTxt -and "$colTxt".Trim() -match '^\d+$') { [int]"$colTxt".Trim() } else { 0 }
        }

        $idxRaw = docker exec emily-postgres psql -U emily -d emily -t -A -c "SELECT count(*) FROM pg_indexes WHERE tablename='project_nodes' AND indexname IN ('uq_pn_project_temp_milestone','uq_pn_project_sink');" 2>$null
        $idxTxt = ($idxRaw | Where-Object { $_ -and "$_".Trim() -ne "" } | Select-Object -First 1)
        $idxCount = if ($idxTxt -and "$idxTxt".Trim() -match '^\d+$') { [int]"$idxTxt".Trim() } else { 0 }

        if ($colCount -ge 2 -and $idxCount -ge 2) {
            Write-Host "  [OK] 增量迁移齐备（node_role / planned_start_at + 2 个部分唯一索引）" -ForegroundColor Green
        } else {
            Write-Host "  [ERROR] 增量迁移不完整：列 $colCount/2、部分唯一索引 $idxCount/2 —— 请检查 emily-core 启动日志（Database init failed）" -ForegroundColor Red
        }
    }

    # 清附件 mock 目录（与 ResetDatabase 口径一致）
    if (Test-Path "$ATTACHMENTS_ROOT/mock") {
        Remove-Item "$ATTACHMENTS_ROOT/mock" -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  [OK] mock 附件目录已清空" -ForegroundColor Green
    }

    Write-Host ""
}

# ============================================================
# 功能一：一键重置数据库（空库，保留 schema）
# ============================================================
function Invoke-ResetDatabase {
    Write-Host "[重置] 清空全部业务表（保留 schema）..." -ForegroundColor Yellow

    # 确保 postgres 容器在运行
    $pgRunning = docker inspect -f '{{.State.Running}}' emily-postgres 2>$null
    if ($pgRunning -ne 'true') {
        Write-Host "  [等待] 启动 emily-postgres 容器..." -ForegroundColor DarkGray
        docker compose -f $COMPOSE_FILE up -d emily-postgres 2>$null
        Start-Sleep -Seconds 3
        Write-Host "  [OK] emily-postgres 已启动" -ForegroundColor Green
    }

    # 执行 TRUNCATE 脚本
    Write-Host "  [执行] 000_reset_all.sql TRUNCATE 全表..." -ForegroundColor DarkGray
    $resetPath = "$BASE/000_reset_all.sql"
    if (-not (Test-Path $resetPath)) {
        Write-Host "[ERROR] 找不到 $resetPath" -ForegroundColor Red
        exit 1
    }
    docker cp $resetPath "emily-postgres:/tmp/reset_all.sql" 2>$null
    docker exec emily-postgres psql -U emily -d emily -f /tmp/reset_all.sql 2>$null
    docker exec emily-postgres rm -f /tmp/reset_all.sql 2>$null

    # 验证空库
    Write-Host "  [验证] 确认数据已清空..." -ForegroundColor DarkGray
    $result1 = docker exec emily-postgres psql -U emily -d emily -t -c 'SELECT count(*) FROM information_schema.tables WHERE table_schema=''public'';'
    $tableCount = $result1.Trim()
    $result2 = docker exec emily-postgres psql -U emily -d emily -t -c 'SELECT coalesce(sum(cnt),0) FROM (SELECT count(*) cnt FROM users UNION ALL SELECT count(*) FROM projects UNION ALL SELECT count(*) FROM files UNION ALL SELECT count(*) FROM messages UNION ALL SELECT count(*) FROM project_nodes) t;'
    $dataRows = $result2.Trim()

    Write-Host "  [OK] 表数: $tableCount, 关键表行数: $dataRows" -ForegroundColor Green

    # 清附件 mock 目录
    if (Test-Path "$ATTACHMENTS_ROOT/mock") {
        Remove-Item "$ATTACHMENTS_ROOT/mock" -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  [OK] mock 附件目录已清空" -ForegroundColor Green
    }

    Write-Host ""
}

# ============================================================
# 功能二：导入核心种子数据（步骤 [1]-[10]）
# ============================================================
function Invoke-SeedData {
    Write-Host "[种子] 导入核心种子数据..." -ForegroundColor Yellow

    # [1] 公司 + 用户 (002)
    Write-Host "  [1/11] 公司 + 用户 (002)..." -ForegroundColor DarkGray
    ExecSql "$BASE/002_seed_test_data.sql"
    Write-Host "    [OK] 5家公司 + 7名用户" -ForegroundColor Green

    # [2] 补用户 (002_patch)
    $patchPath = "$ENV_TOOL/002_seed_test_data_patch.sql"
    if (Test-Path $patchPath) {
        Write-Host "  [2/11] 补充用户 (patch)..." -ForegroundColor DarkGray
        ExecSql $patchPath
        Write-Host "    [OK] 新增3名用户 (L5/L2/L2)" -ForegroundColor Green
    } else {
        Write-Host "  [2/11] 补充用户 (patch)... 跳过 (文件不存在: $patchPath)" -ForegroundColor DarkGray
    }

    # [3] 建设单位专业人员 (003)
    $usersPath = "$ENV_TOOL/003_seed_users_patch.sql"
    if (Test-Path $usersPath) {
        Write-Host "  [3/11] 建设单位专业人员 (003)..." -ForegroundColor DarkGray
        ExecSql $usersPath
        Write-Host "    [OK] 4名专业人员 (建筑/土建/安装/景观精装)" -ForegroundColor Green
    } else {
        Write-Host "  [3/11] 建设单位专业人员... 跳过 (文件不存在: $usersPath)" -ForegroundColor DarkGray
    }

    # [4] 分包单位 + 监理/分包人员 (015)
    $subPath = "$ENV_TOOL/015_seed_subcontract_users.sql"
    if (Test-Path $subPath) {
        Write-Host "  [4/11] 分包单位 + 监理/分包人员 (015)..." -ForegroundColor DarkGray
        ExecSql $subPath
        Write-Host "    [OK] 5家专业分包 + 2名专业监理 + 5名分包人员" -ForegroundColor Green
    } else {
        Write-Host "  [4/11] 分包单位 + 监理/分包人员... 跳过 (文件不存在: $subPath)" -ForegroundColor DarkGray
    }

    # [5] 项目 + 文件元数据 (007)
    $projPath = "$ENV_TOOL/007_seed_emerald_project.sql"
    if (Test-Path $projPath) {
        Write-Host "  [5/11] 项目 + 文件元数据 (007)..." -ForegroundColor DarkGray
        ExecSql $projPath
        Write-Host "    [OK] EMERALD-01 项目 + 5指标 + 19文件" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] 找不到 $projPath" -ForegroundColor Red
        exit 1
    }

    # [6] 节点树 (008 YAML -> manage_nodes.py)
    $nodesYaml = "$ENV_TOOL/008_seed_emerald_nodes.yaml"
    if (Test-Path $nodesYaml) {
        Write-Host "  [6/11] 全景节点树 (008 YAML)..." -ForegroundColor DarkGray
        $env:PYTHONPATH = "emily-core"
        $nodeResult = uv run python scripts/manage_nodes.py create --file $nodesYaml 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "    [WARN] manage_nodes.py 返回非零退出码，请检查输出:" -ForegroundColor Yellow
            Write-Host $nodeResult -ForegroundColor Yellow
        } else {
            Write-Host "    [OK] 节点树创建完成" -ForegroundColor Green
        }
    } else {
        Write-Host "[ERROR] 找不到 $nodesYaml" -ForegroundColor Red
        exit 1
    }

    # [7] 节点责任人分配 (012)
    $respPath = "$ENV_TOOL/012_seed_node_responsible.sql"
    if (Test-Path $respPath) {
        Write-Host "  [7/11] 节点责任人分配 (012)..." -ForegroundColor DarkGray
        ExecSql $respPath
        Write-Host "    [OK] 34个节点按专业分配责任人" -ForegroundColor Green
    } else {
        Write-Host "  [7/11] 节点责任人分配... 跳过 (文件不存在: $respPath)" -ForegroundColor DarkGray
    }

    # [8] 节点参与人分配 (013)
    $participantsPath = "$ENV_TOOL/013_seed_node_participants.sql"
    if (Test-Path $participantsPath) {
        Write-Host "  [8/11] 节点参与人分配 (013)..." -ForegroundColor DarkGray
        ExecSql $participantsPath
        Write-Host "    [OK] 21名用户（除访客外）按专业分配到各节点" -ForegroundColor Green
    } else {
        Write-Host "  [8/11] 节点参与人分配... 跳过 (文件不存在: $participantsPath)" -ForegroundColor DarkGray
    }

    # [9] 业务数据 (009)
    $bizPath = "$ENV_TOOL/009_seed_emerald_business.sql"
    if (Test-Path $bizPath) {
        Write-Host "  [9/11] 业务数据 (009)..." -ForegroundColor DarkGray
        ExecSql $bizPath
        Write-Host "    [OK] 事件/任务/会议/流转单/指令单/计划/会话/消息 + 材料进场" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] 找不到 $bizPath" -ForegroundColor Red
        exit 1
    }

    # [10] 权限体系 (006)
    Write-Host "  [10/11] 权限体系 (006)..." -ForegroundColor DarkGray
    ExecSql "$BASE/006_seed_permission_data.sql"
    Write-Host "    [OK] 权限组 + SOP + 绑定 + 授权" -ForegroundColor Green

    # [11] 统一项目事件回填 (007_migrate_project_events.sql)
    $peMigratePath = "$BASE/007_migrate_project_events.sql"
    if (Test-Path $peMigratePath) {
        Write-Host "  [11/11] 统一项目事件回填 (007)..." -ForegroundColor DarkGray
        ExecSql $peMigratePath
        Write-Host "    [OK] project_events 回填 + 临时节点 UNASSIGNED" -ForegroundColor Green
    } else {
        Write-Host "  [11/11] 统一项目事件回填... 跳过 (文件不存在: $peMigratePath)" -ForegroundColor DarkGray
    }

    Write-Host ""
}

# ============================================================
# 功能二续：导入高级种子数据（010）
# ============================================================
function Invoke-SeedAdvanced {
    param([string]$AdvancedScriptPath)

    if (-not (Test-Path $AdvancedScriptPath)) {
        Write-Host "[高级] 010脚本不存在 ($AdvancedScriptPath)，已跳过" -ForegroundColor Yellow
        return
    }

    Write-Host "[高级] 导入运行时/进化/调度种子数据 (010)..." -ForegroundColor Yellow
    ExecSql $AdvancedScriptPath
    Write-Host "  [OK] 调度器 + 进化闭环 + 路由日志 + RAG + 反馈 + 权限运行时 + 附件 + 节点文件" -ForegroundColor Green
    Write-Host ""
}

# ============================================================
# 功能二续：修复 IM 绑定（simulator → napcat，sim_* → QQ号）
# ============================================================
function Invoke-FixIMBindings {
    Write-Host "[IM] 修复 IM 绑定数据..." -ForegroundColor Yellow

    $sql = @"
UPDATE user_im_bindings uib
SET im_platform = 'napcat',
    im_user_id = u.qq
FROM users u
WHERE uib.user_id = u.id
  AND u.qq IS NOT NULL
  AND u.qq != '';
"@
    $tmpPath = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmpPath, $sql, [System.Text.UTF8Encoding]::new($false))
    docker cp $tmpPath "emily-postgres:/tmp/fix_im.sql" 2>$null
    docker exec emily-postgres psql -U emily -d emily -f /tmp/fix_im.sql 2>$null
    docker exec emily-postgres rm -f /tmp/fix_im.sql 2>$null
    Remove-Item $tmpPath -Force -ErrorAction SilentlyContinue

    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [WARN] IM 绑定修复可能失败，请手动检查" -ForegroundColor Yellow
    } else {
        Write-Host "  [OK] im_platform → napcat, im_user_id → QQ号" -ForegroundColor Green
    }
    Write-Host ""
}

# ============================================================
# 功能二续：磁盘空文件生成 + storage_path 修复
# ============================================================
function New-MockFiles {
    Write-Host "[文件] 生成磁盘空文件 + 修复 storage_path..." -ForegroundColor Yellow

    # 确保 postgres 容器运行
    $pgRunning = docker inspect -f '{{.State.Running}}' emily-postgres 2>$null
    if ($pgRunning -ne 'true') {
        Write-Host "[ERROR] emily-postgres 容器未运行" -ForegroundColor Red
        return
    }

    # 清旧 mock 文件
    if (Test-Path "$ATTACHMENTS_ROOT/mock") {
        Remove-Item "$ATTACHMENTS_ROOT/mock" -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  [OK] 旧 mock 文件已清理" -ForegroundColor Green
    }

    # 从 DB 拉文件清单（含 project_code）—— 容器内写文件 + docker cp，绕过 PowerShell 编码问题
    $tmpFile = "$env:TEMP\emily_files_list.txt"
    $containerPath = "/tmp/emily_files_list.txt"
    docker exec emily-postgres psql -U emily -d emily -t -A -F '|' -c "COPY (SELECT f.file_no, f.filename, f.file_category, p.code FROM files f JOIN projects p ON f.project_id = p.id WHERE f.is_deleted = false) TO '$containerPath' WITH (FORMAT csv, DELIMITER '|', ENCODING 'UTF8');" 2>$null
    docker cp "emily-postgres:$containerPath" $tmpFile 2>$null
    docker exec emily-postgres rm -f $containerPath 2>$null

    $count = 0
    $lines = Get-Content $tmpFile -Encoding UTF8
    foreach ($line in $lines) {
        $trimmed = $line.Trim()
        if ([string]::IsNullOrEmpty($trimmed)) { continue }

        $parts = $trimmed -split '\|'
        if ($parts.Count -lt 4) { continue }

        $fileNo = $parts[0].Trim()
        $filename = $parts[1].Trim()
        $category = $parts[2].Trim()
        $projectCode = $parts[3].Trim()

        if ([string]::IsNullOrEmpty($projectCode) -or [string]::IsNullOrEmpty($filename)) { continue }

        # 规范化相对路径: mock/{projectCode}/{category}/{filename}
        $relPath = "mock/$projectCode/$category/$filename" -replace '\\', '/'
        $fullPath = Join-Path $ATTACHMENTS_ROOT $relPath

        # 创建目录 + 空文件
        $dir = Split-Path $fullPath -Parent
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        try {
            New-Item -ItemType File -Path $fullPath -Force | Out-Null
        } catch {
            Write-Host "    [WARN] 跳过: $filename" -ForegroundColor DarkGray
            continue
        }

        # 修复 DB storage_path（去前导 /，改用相对路径）
        $escaped = $relPath -replace "'", "''"
        docker exec emily-postgres psql -U emily -d emily -c "UPDATE files SET storage_path = '$escaped' WHERE file_no = '$fileNo';" 2>$null

        $count++
    }
    Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue

    Write-Host "  [OK] 已生成 $count 个磁盘空文件，storage_path 已修复为相对路径" -ForegroundColor Green
    Write-Host ""
}

# ============================================================
# 功能二续四：文件管理系统测试专用 — session_accessible_files 种子
# ============================================================
function Invoke-SeedFileAccess {
    Write-Host "[文件管理] 创建文件可见性权限记录 (session_accessible_files)..." -ForegroundColor Yellow

    $sql = @"
-- 王建国 (level=6, admin) 可看前 9 个文件 (PROJECT_LICENSE + CONTRACT + PHASE_DELIVERABLE)
INSERT INTO session_accessible_files (id, user_id, file_id, access_type, granted_at)
SELECT gen_random_uuid(),
       (SELECT id FROM users WHERE username = '王建国' AND is_deleted = false LIMIT 1),
       id, 'explicit', '2026-07-26T00:00:00'
FROM (
    SELECT id FROM files WHERE is_deleted = false ORDER BY file_no LIMIT 9
) f;

-- 李景利 (level=4) 只能看前 4 个文件 (PROJECT_LICENSE only)
INSERT INTO session_accessible_files (id, user_id, file_id, access_type, granted_at)
SELECT gen_random_uuid(),
       (SELECT id FROM users WHERE username = '李景利' AND is_deleted = false LIMIT 1),
       id, 'explicit', '2026-07-26T00:00:00'
FROM (
    SELECT id FROM files WHERE is_deleted = false ORDER BY file_no LIMIT 4
) f;
"@

    $tmpPath = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmpPath, $sql, [System.Text.UTF8Encoding]::new($false))
    docker cp $tmpPath "emily-postgres:/tmp/seed_file_access.sql" 2>$null
    docker exec emily-postgres psql -U emily -d emily -f /tmp/seed_file_access.sql 2>$null
    docker exec emily-postgres rm -f /tmp/seed_file_access.sql 2>$null
    Remove-Item $tmpPath -Force -ErrorAction SilentlyContinue

    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [WARN] session_accessible_files 种子可能失败，请检查" -ForegroundColor Yellow
    } else {
        Write-Host "  [OK] 王建国 9 条 + 李景利 4 条 = 13 条 session_accessible_files 记录" -ForegroundColor Green
    }
    Write-Host ""
}

# ============================================================
# 功能二续五：M3 附件自动下载测试用文件
# ============================================================
function New-TestAttachment {
    Write-Host "[文件管理] 生成 M3 附件下载测试文件..." -ForegroundColor Yellow

    $testFile = "$ATTACHMENTS_ROOT/test_attachment.txt"
    $content = "Emily 文件管理系统 M3 附件自动下载测试文件。`r`n创建时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`r`n用途: 验证 AttachmentDownloader.download_for_message 流程。"

    try {
        [System.IO.File]::WriteAllText($testFile, $content, [System.Text.UTF8Encoding]::new($false))
        Write-Host "  [OK] test_attachment.txt 已生成 → $testFile" -ForegroundColor Green
        Write-Host "  容器内 URL: file:///app/attachments/test_attachment.txt" -ForegroundColor DarkGray
    } catch {
        Write-Host "  [WARN] 测试文件创建失败: $_" -ForegroundColor Yellow
    }
    Write-Host ""
}

# ============================================================
# 功能二续：存量未归类事件归属对齐（US-15.9 / 15.10）
#   种子步骤 [11]（007_migrate_project_events.sql）按**特性前**口径创建全局假节点
#   node_id='UNASSIGNED' 并把暂不知去向的事件挂上去。现口径要求事件的归属必须指向
#   **本项目真实存在且在项目内可解析的节点**（未归类事件落到本项目唯一的收容节点）。
#   此处复用模块自带脚本做对齐：按项目懒建收容节点 → 逐条改挂 + 写归位留痕。
#   脚本幂等，可重复执行；执行前自动写备份到 emily-data/runtime/backups/。
# ============================================================
function Invoke-AlignUnclassified {
    Write-Host "[对齐] 存量未归类事件归属（US-15.9 / 15.10）..." -ForegroundColor Yellow

    $alignScript = "scripts/align_unclassified_events.py"
    if (-not (Test-Path $alignScript)) {
        Write-Host "  [跳过] 找不到 $alignScript" -ForegroundColor DarkGray
        Write-Host ""
        return
    }

    # 对齐前快照（供对照；扫描为只读）
    $before = uv run python $alignScript --scan 2>&1 | Select-String -Pattern "^\[扫描\]" | Select-Object -First 1
    if ($before) { Write-Host "  $before" -ForegroundColor DarkGray }

    # 执行对齐（--db-url 默认走宿主机映射端口，见脚本 docstring）
    $out = uv run python $alignScript 2>&1
    $out | Select-String -Pattern "^\[对齐\]|^\[备份\]|^\[复核\]|^\[跳过\]" | ForEach-Object {
        Write-Host "  $_" -ForegroundColor DarkGray
    }

    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [WARN] 对齐脚本返回非零退出码（可能仍有非法归属，见上方输出）" -ForegroundColor Yellow
    } else {
        Write-Host "  [OK] 归属已闭合（全库无「归属指向非节点」的记录）" -ForegroundColor Green
    }
    Write-Host ""
}

# ============================================================
# 功能三：验证数据完整性
# ============================================================
function Invoke-Verify {
    Write-Host "[验证] 数据完整性检查..." -ForegroundColor Yellow
    Write-Host ""

    # 使用 docker cp 避免 PowerShell 管道损坏 UTF-8 编码
    $verifyPath = "$ENV_TOOL/verify_data.sql"
    docker cp $verifyPath "emily-postgres:/tmp/verify_data.sql" 2>$null
    docker exec emily-postgres psql -U emily -d emily -f /tmp/verify_data.sql
    docker exec emily-postgres rm -f /tmp/verify_data.sql 2>$null

    Write-Host ""

    # storage_path 修复验证
    Write-Host "[检查] storage_path 绝对路径残留..." -ForegroundColor DarkGray
    $badPaths = docker exec emily-postgres psql -U emily -d emily -t -c 'SELECT count(*) FROM files WHERE storage_path LIKE ''/%'' AND is_deleted = false;'
    $badCount = $badPaths.Trim()
    if ($badCount -eq '0') {
        Write-Host "  [OK] 所有 storage_path 均已修复为相对路径" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] 仍有 $badCount 条记录的 storage_path 为绝对路径" -ForegroundColor Yellow
    }

    # 磁盘文件验证
    if (Test-Path "$ATTACHMENTS_ROOT/mock") {
        $fileCount = (Get-ChildItem -Recurse "$ATTACHMENTS_ROOT/mock" -File -ErrorAction SilentlyContinue).Count
        Write-Host "  [OK] mock 目录下磁盘文件数: $fileCount" -ForegroundColor Green
    }

    # 文件管理测试数据验证
    $safCount = docker exec emily-postgres psql -U emily -d emily -t -c 'SELECT count(*) FROM session_accessible_files;'
    $safNum = $safCount.Trim()
    if ($safNum -gt 0) {
        Write-Host "  [OK] session_accessible_files 记录数: $safNum" -ForegroundColor Green
    } else {
        Write-Host "  [提示] session_accessible_files 为空（跳过文件管理测试数据时正常）" -ForegroundColor DarkGray
    }

    if (Test-Path "$ATTACHMENTS_ROOT/test_attachment.txt") {
        Write-Host "  [OK] M3 测试附件: test_attachment.txt" -ForegroundColor Green
    }

    Write-Host ""
}

# ============================================================
# 功能五：RAG 知识库阶段（env 模式）
#   通过 scripts/rag_test_harness.py --env --setup 把 18 个真实内容
#   测试文件作为 EMERALD-01 模拟项目的一部分建库并保留（含 TEI 向量化）。
#   前置：emily-embed 容器运行（TEI 127.0.0.1:8082）、用户种子已入。
# ============================================================
function Invoke-SeedRAGEnv {
    Write-Host "[RAG库] 搭建 RAG 知识库（18 内容文件 → EMERALD-01 + 供应商隔离项目）..." -ForegroundColor Yellow

    # 1) 访客用户（RAG TC-03/TC-10 需要无公司 L1 用户）
    $visitorPath = "$ENV_TOOL/014_seed_rag_visitor.sql"
    if (Test-Path $visitorPath) {
        ExecSql $visitorPath
        Write-Host "  [OK] 访客用户 周访客 (company=NULL, L1)" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] 找不到 014_seed_rag_visitor.sql，访客用户缺失" -ForegroundColor Yellow
    }

    # 2) env 模式建库（幂等重建：清理旧 RAG 库后重建，18 文件 → EMERALD-01 + 供应商隔离项目）
    $env:PYTHONPATH = "emily-core"
    $ragResult = uv run python scripts/rag_test_harness.py --env --setup --rebuild 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [WARN] RAG 库搭建可能失败，请检查输出:" -ForegroundColor Yellow
        Write-Host $ragResult -ForegroundColor Yellow
    } else {
        Write-Host "  [OK] RAG 知识库已就绪（18 文件入库 + 关系 + chunk）" -ForegroundColor Green
        Write-Host "       运行验收: uv run python scripts/rag_test_harness.py --env" -ForegroundColor DarkGray
    }
    Write-Host ""
}

# ============================================================
# 主流程入口
# ============================================================
# 验证项目根目录
if (-not (Test-Path $COMPOSE_FILE)) {
    Write-Host "[ERROR] 请在项目根目录 d:\app\Emily 下执行此脚本" -ForegroundColor Red
    exit 1
}

# -Recreate 会重建空库；-SeedOnly 假定「库空但结构已建」。语义冲突，禁止同时使用。
if ($Recreate -and $SeedOnly) {
    Write-Host "[ERROR] -Recreate 与 -SeedOnly 互斥，请二选一" -ForegroundColor Red
    exit 1
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Emily 测试环境搭建工具" -ForegroundColor Cyan
Write-Host "  项目: $Project" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if ($Recreate) {
    # 结构层重建：DROP + CREATE DATABASE，由 emily-core 从零建表。
    # 重建后库必为空，无需再跑 TRUNCATE，故跳过 Invoke-ResetDatabase。
    Invoke-RecreateDatabase
} elseif (-not $SeedOnly) {
    Invoke-ResetDatabase
}

if (-not $ResetOnly) {
    Invoke-SeedData
    if (-not $SkipAdvanced) {
        Invoke-SeedAdvanced "$ENV_TOOL/010_seed_runtime_data.sql"
    }
    Invoke-FixIMBindings
    if (-not $SkipMockFiles) {
        New-MockFiles
    }
    if (-not $SkipFileMgmtTests) {
        Invoke-SeedFileAccess
        New-TestAttachment
    }

    # ── 世界书 tier 补丁（补全 T2/T3 数据缺口，确保 tier≥3 激活）──
    $patchPath = "$ENV_TOOL/011_seed_world_book_patch.sql"
    if (Test-Path $patchPath) {
        Write-Host "[补丁] 世界书 tier 数据补丁 (011)..." -ForegroundColor Yellow
        ExecSql $patchPath
        Write-Host "  [OK] 总监理工程师 + 里程碑节点已补全" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] 找不到 011_seed_world_book_patch.sql，世界书可能无法达到 tier≥3" -ForegroundColor Yellow
    }
    Write-Host ""

    # ── 世界书重建（reset 后 project_id 已变，旧世界书失效，必须重跑）──
    #     build_world_book.py 位于仓库根 scripts/（非 emily-core 内），须在根目录执行
    Write-Host "[世界书] 重建项目世界书..." -ForegroundColor Yellow
    $env:PYTHONPATH = "emily-core"
    $wbResult = uv run python scripts/build_world_book.py --all 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [WARN] 世界书构建可能失败，请手动检查:" -ForegroundColor Yellow
        Write-Host $wbResult -ForegroundColor Yellow
    } else {
        Write-Host "  [OK] 世界书已重建" -ForegroundColor Green
    }
    Write-Host ""

    # ── 世界书验证 ──
    Write-Host "[世界书] 验证层级与激活状态..." -ForegroundColor Yellow
    $tierCheck = docker exec emily-postgres psql -U emily -d emily -t -A -c "SELECT initialization_tier, is_activated, length(content_text) FROM project_world_books WHERE project_id = (SELECT id FROM projects WHERE code = '$Project' AND is_deleted = false);"
    if ($tierCheck) {
        $tierParts = $tierCheck -split '\|'
        $wbTier = $tierParts[0].Trim()
        $wbActivated = $tierParts[1].Trim()
        $wbTextLen = $tierParts[2].Trim()
        Write-Host "  tier=${wbTier}  activated=${wbActivated}  text_len=${wbTextLen}" -ForegroundColor DarkGray
        if ($wbTier -ge 3 -and $wbActivated -eq 't') {
            Write-Host "  [OK] 世界书已激活 (tier>=3)，Emily 将自动注入项目上下文" -ForegroundColor Green
        } elseif ($wbTier -ge 1) {
            Write-Host "  [提示] 世界书 tier=${wbTier}，未达激活阈值(tier>=3)" -ForegroundColor Yellow
            Write-Host "         当前缺失项不影响基本问答，Emily 可识别项目基本信息" -ForegroundColor Yellow
        } else {
            Write-Host "  [WARN] 世界书 tier=0，请检查种子数据是否完整" -ForegroundColor Yellow
        }
    }
    Write-Host ""

    # ── 重启 emily-core 使 Session 缓存失效（加载新世界书）──
    Write-Host "[缓存] 刷新 emily-core Session 缓存..." -ForegroundColor Yellow
    $coreRunning = docker inspect -f '{{.State.Running}}' emily-core 2>$null
    if ($coreRunning -eq 'true') {
        docker compose -f $COMPOSE_FILE restart emily-core 2>$null
        Write-Host "  [OK] emily-core 已重启，新会话将加载最新世界书" -ForegroundColor Green
    } else {
        Write-Host "  [跳过] emily-core 未运行" -ForegroundColor DarkGray
    }
    Write-Host ""

    # ── RAG 知识库阶段（默认开，-SkipRAG 跳过）──
    if (-not $SkipRAG) {
        Invoke-SeedRAGEnv
    }

    # ── 存量未归类事件归属对齐（默认开，-SkipAlign 跳过）──
    # 必须在全部 seed 之后（步骤 [11] 是 UNASSIGNED 事件的来源），验证之前。
    if (-not $SkipAlign) {
        Invoke-AlignUnclassified
    }

    Invoke-Verify
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  测试环境搭建完成!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "快速测试:" -ForegroundColor Yellow
Write-Host '  uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我查一下翠湖庭院项目的整体进度情况" --sender "李景利"' -ForegroundColor Gray
Write-Host ""
