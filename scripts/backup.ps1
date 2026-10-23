# Emily 备份脚本
# 用法：.\scripts\backup.ps1
# 输出：backups\YYYYMMDD_HHMMSS\ 目录，含数据库 SQL + 配置文件 + 附件 + 资料库

$ErrorActionPreference = "Stop"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = ".\backups\$timestamp"
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

Write-Host "=== Emily 备份开始 ===" -ForegroundColor Cyan
Write-Host "备份目录: $backupDir"

# 1. PostgreSQL 全库备份
Write-Host "[1/4] 备份数据库..." -ForegroundColor Yellow
$dbFile = "$backupDir\emily_db.sql"
docker exec emily-postgres pg_dump -U emily emily > $dbFile
if ($LASTEXITCODE -ne 0) { throw "数据库备份失败" }
Write-Host "  数据库备份完成: $dbFile ($((Get-Item $dbFile).Length) bytes)"

# 2. 配置文件备份
Write-Host "[2/4] 备份配置文件..." -ForegroundColor Yellow
Copy-Item -Recurse -Path ".\emily-data\config" -Destination "$backupDir\config" -ErrorAction SilentlyContinue
Copy-Item -Recurse -Path ".\emily-data\sops" -Destination "$backupDir\sops" -ErrorAction SilentlyContinue
Copy-Item -Recurse -Path ".\emily-data\prompts" -Destination "$backupDir\prompts" -ErrorAction SilentlyContinue
Copy-Item -Recurse -Path ".\emily-data\skills" -Destination "$backupDir\skills" -ErrorAction SilentlyContinue
Write-Host "  配置文件备份完成"

# 3. 运行态数据备份
# 运行态目录均位于仓库同级的 Emily-var/（不在仓库内）：attachments / session_archives /
# user_memory / journal / notebooks / runtime / knowledge_library。
# 不备份 logs（运行日志）与模型缓存（可再生产物）。
Write-Host "[3/4] 备份运行态数据..." -ForegroundColor Yellow
$varDir = "..\Emily-var"
foreach ($name in @("attachments", "session_archives", "user_memory", "journal", "notebooks", "runtime")) {
    $src = Join-Path $varDir $name
    if (Test-Path $src) {
        Copy-Item -Recurse -Path $src -Destination (Join-Path $backupDir $name) -ErrorAction SilentlyContinue
    } else {
        Write-Host "  提示：未找到 $src，跳过" -ForegroundColor DarkYellow
    }
}

# 资料库：备份体量最大（约 1.3GB）
$libraryDir = Join-Path $varDir "knowledge_library"
if (Test-Path $libraryDir) {
    Copy-Item -Recurse -Path $libraryDir -Destination "$backupDir\knowledge_library" -ErrorAction SilentlyContinue
} else {
    Write-Host "  警告：未找到 $libraryDir，资料库未纳入本次备份" -ForegroundColor Red
}
Write-Host "  运行态数据备份完成"

# 4. 创建备份元信息
Write-Host "[4/4] 创建备份元信息..." -ForegroundColor Yellow
@"
备份时间: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
备份目录: $backupDir
容器状态:
$(docker compose -f docker-compose-napcat.yml ps 2>$null)
"@ | Out-File -FilePath "$backupDir\backup_info.txt" -Encoding UTF8

Write-Host ""
Write-Host "=== 备份完成 ===" -ForegroundColor Green
Write-Host "目录: $backupDir"
Write-Host "大小: $((Get-ChildItem -Path $backupDir -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB) MB"
