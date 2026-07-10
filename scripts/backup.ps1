# Emily 备份脚本
# 用法：.\scripts\backup.ps1
# 输出：backups\YYYYMMDD_HHMMSS\ 目录，含数据库 SQL + 配置文件 + 附件

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

# 3. 附件目录备份
Write-Host "[3/4] 备份附件..." -ForegroundColor Yellow
Copy-Item -Recurse -Path ".\emily-data\attachments" -Destination "$backupDir\attachments" -ErrorAction SilentlyContinue
Copy-Item -Recurse -Path ".\emily-data\user_memory" -Destination "$backupDir\user_memory" -ErrorAction SilentlyContinue
Copy-Item -Recurse -Path ".\emily-data\journal" -Destination "$backupDir\journal" -ErrorAction SilentlyContinue
Copy-Item -Recurse -Path ".\emily-data\notebooks" -Destination "$backupDir\notebooks" -ErrorAction SilentlyContinue
Write-Host "  附件备份完成"

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
