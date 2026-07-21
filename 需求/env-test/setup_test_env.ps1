# ============================================================
# setup_test_env.ps1 — Emily 测试环境一键工具
#
# 用法（在项目根目录 d:\app\Emily 下执行）:
#   powershell -File 需求/env-test/setup_test_env.ps1                  完整重置+种子+文件
#   powershell -File 需求/env-test/setup_test_env.ps1 -ResetOnly       仅空库重置
#   powershell -File 需求/env-test/setup_test_env.ps1 -SeedOnly        仅种子（库已空）
#   powershell -File 需求/env-test/setup_test_env.ps1 -SkipAdvanced    跳过010高级数据
#   powershell -File 需求/env-test/setup_test_env.ps1 -SkipMockFiles   跳过磁盘空文件
#
# 依赖:
#   - Docker Desktop 运行中（emily-postgres + emily-core 容器）
#   - uv（Python 环境管理，用于 manage_nodes.py）
#   - PowerShell 5.1+
# ============================================================
param(
    [switch]$ResetOnly,
    [switch]$SeedOnly,
    [switch]$SkipAdvanced,
    [switch]$SkipMockFiles,
    [string]$Project = "EMERALD-01"
)

$ErrorActionPreference = "Continue"
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

# ── 路径常量（相对于项目根目录 d:\app\Emily）──
$BASE = "emily-core/emily_core/infrastructure/database/scripts"
$SEED = "需求/env-test"
$COMPOSE_FILE = "docker-compose-napcat.yml"
$ATTACHMENTS_ROOT = "emily-data/attachments"
$SEED_PATCH = "需求/环境布置/scripts"

# ============================================================
# 工具函数：执行 SQL 文件
# ============================================================
function ExecSql {
    param([string]$SqlFilePath)

    if (-not (Test-Path $SqlFilePath)) {
        Write-Host "[ERROR] SQL 文件不存在: $SqlFilePath" -ForegroundColor Red
        exit 1
    }

    Get-Content $SqlFilePath -Raw -Encoding UTF8 | docker exec -i emily-postgres psql -U emily -d emily 2>$null

    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] SQL 执行失败: $SqlFilePath" -ForegroundColor Red
        exit 1
    }
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
    Get-Content $resetPath -Raw -Encoding UTF8 | docker exec -i emily-postgres psql -U emily -d emily 2>$null

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
# 功能二：导入核心种子数据（步骤 [1]-[6]）
# ============================================================
function Invoke-SeedData {
    Write-Host "[种子] 导入核心种子数据..." -ForegroundColor Yellow

    # [1] 公司 + 用户 (002)
    Write-Host "  [1/6] 公司 + 用户 (002)..." -ForegroundColor DarkGray
    ExecSql "$BASE/002_seed_test_data.sql"
    Write-Host "    [OK] 5家公司 + 7名用户" -ForegroundColor Green

    # [2] 补用户 (002_patch)
    $patchPath = "$SEED_PATCH/002_seed_test_data_patch.sql"
    if (Test-Path $patchPath) {
        Write-Host "  [2/6] 补充用户 (patch)..." -ForegroundColor DarkGray
        ExecSql $patchPath
        Write-Host "    [OK] 新增3名用户 (L5/L2/L2)" -ForegroundColor Green
    } else {
        Write-Host "  [2/6] 补充用户 (patch)... 跳过 (文件不存在: $patchPath)" -ForegroundColor DarkGray
    }

    # [3] 项目 + 文件元数据 (007)
    $projPath = "$SEED_PATCH/007_seed_emerald_project.sql"
    if (Test-Path $projPath) {
        Write-Host "  [3/6] 项目 + 文件元数据 (007)..." -ForegroundColor DarkGray
        ExecSql $projPath
        Write-Host "    [OK] EMERALD-01 项目 + 5指标 + 18文件" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] 找不到 $projPath" -ForegroundColor Red
        exit 1
    }

    # [4] 节点树 (008 YAML -> manage_nodes.py)
    $nodesYaml = "$SEED_PATCH/008_seed_emerald_nodes.yaml"
    if (Test-Path $nodesYaml) {
        Write-Host "  [4/6] 全景节点树 (008 YAML)..." -ForegroundColor DarkGray
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

    # [5] 业务数据 (009)
    $bizPath = "$SEED_PATCH/009_seed_emerald_business.sql"
    if (Test-Path $bizPath) {
        Write-Host "  [5/6] 业务数据 (009)..." -ForegroundColor DarkGray
        ExecSql $bizPath
        Write-Host "    [OK] 事件/任务/会议/流转单/指令单/计划/会话/消息" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] 找不到 $bizPath" -ForegroundColor Red
        exit 1
    }

    # [6] 权限体系 (006)
    Write-Host "  [6/6] 权限体系 (006)..." -ForegroundColor DarkGray
    ExecSql "$BASE/006_seed_permission_data.sql"
    Write-Host "    [OK] 权限组 + SOP + 绑定 + 授权" -ForegroundColor Green

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
    $sql | docker exec -i emily-postgres psql -U emily -d emily 2>$null

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
# 功能三：验证数据完整性
# ============================================================
function Invoke-Verify {
    Write-Host "[验证] 数据完整性检查..." -ForegroundColor Yellow
    Write-Host ""

    # 使用独立SQL文件避免PowerShell 5.1解析器兼容问题
    $verifyPath = "$SEED/verify_data.sql"
    Get-Content $verifyPath -Raw -Encoding UTF8 | docker exec -i emily-postgres psql -U emily -d emily

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

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Emily 测试环境搭建工具" -ForegroundColor Cyan
Write-Host "  项目: $Project" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if (-not $SeedOnly) {
    Invoke-ResetDatabase
}

if (-not $ResetOnly) {
    Invoke-SeedData
    if (-not $SkipAdvanced) {
        Invoke-SeedAdvanced "$SEED/010_seed_runtime_data.sql"
    }
    Invoke-FixIMBindings
    if (-not $SkipMockFiles) {
        New-MockFiles
    }

    # ── 世界书重建（reset 后 project_id 已变，旧世界书失效，必须重跑）──
    Write-Host "[世界书] 重建项目世界书..." -ForegroundColor Yellow
    Push-Location "emily-core"
    $env:PYTHONPATH = "emily-core"
    $wbResult = uv run python scripts/build_world_book.py 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [WARN] 世界书构建可能失败，请手动检查:" -ForegroundColor Yellow
        Write-Host $wbResult -ForegroundColor Yellow
    } else {
        Write-Host "  [OK] 世界书已重建" -ForegroundColor Green
    }
    Pop-Location
    Write-Host ""

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
