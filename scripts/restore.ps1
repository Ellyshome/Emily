# Emily 恢复脚本
# 用法：.\scripts\restore.ps1 <backup_directory>
# 示例：.\scripts\restore.ps1 .\backups\20260710_230000

param(
    [Parameter(Mandatory=$true)]
    [string]$BackupDir
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $BackupDir)) {
    Write-Host "错误: 备份目录不存在: $BackupDir" -ForegroundColor Red
    exit 1
}

Write-Host "=== Emily 恢复开始 ===" -ForegroundColor Cyan
Write-Host "备份源: $BackupDir"
Write-Host ""

# 1. 恢复 PostgreSQL
$dbFile = "$BackupDir\emily_db.sql"
if (Test-Path $dbFile) {
    Write-Host "[1/3] 恢复数据库..." -ForegroundColor Yellow
    Write-Host "  警告: 这将覆盖当前数据库中的所有数据！" -ForegroundColor Red
    $confirm = Read-Host "  确认继续? (输入 yes 继续)"
    if ($confirm -ne "yes") {
        Write-Host "  已取消"
        exit 0
    }
    Get-Content $dbFile | docker exec -i emily-postgres psql -U emily emily
    if ($LASTEXITCODE -ne 0) { Write-Host "  数据库恢复失败，请检查" -ForegroundColor Red }
    Write-Host "  数据库恢复完成"
} else {
    Write-Host "  跳过: 未找到数据库备份文件"
}

# 2. 恢复配置
Write-Host "[2/3] 恢复配置文件..." -ForegroundColor Yellow
$dirs = @("config", "sops", "prompts", "skills")
foreach ($d in $dirs) {
    $src = "$BackupDir\$d"
    $dst = ".\emily-data\$d"
    if (Test-Path $src) {
        Write-Host "  请手动恢复: $d (从 $src 到 $dst)"
    }
}

# 3. 恢复附件
Write-Host "[3/3] 恢复附件..." -ForegroundColor Yellow
$dirs = @("attachments", "user_memory", "journal", "notebooks")
foreach ($d in $dirs) {
    $src = "$BackupDir\$d"
    $dst = ".\emily-data\$d"
    if (Test-Path $src) {
        Write-Host "  请手动恢复: $d (从 $src 到 $dst)"
    }
}

Write-Host ""
Write-Host "=== 恢复完成 ===" -ForegroundColor Green
Write-Host "请重启容器使配置生效: docker compose -f docker-compose-napcat.yml restart"
