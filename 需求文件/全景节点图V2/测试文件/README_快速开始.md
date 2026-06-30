# 全景节点图 V2 测试快速开始指南

> 📋 本文档帮助你快速开始全景节点图V2的完整测试

---

## 一、测试前准备

### 1.1 环境检查

```bash
# 进入项目目录
cd d:\app\Emily

# 确认容器运行
docker ps | findstr emily

# 预期输出应包含：
# emily-core
# emily-postgres
```

### 1.2 数据库检查

```bash
# 检查全景节点5张表是否存在
docker exec emily-postgres psql -U emily -d emily -c "\dt project_nodes node_dependencies node_deliverables node_accessible_files node_events"

# 如果表不存在，先执行迁移：
docker exec -i emily-postgres psql -U emily -d emily < emily-core/emily_core/infrastructure/database/scripts/005_create_panorama_tables.sql
```

### 1.3 导入测试用户数据

```bash
# 导入预设测试用户（王总、李经理、张工等）
docker exec -i emily-postgres psql -U emily -d emily < emily-core/emily_core/infrastructure/database/scripts/002_seed_test_data.sql

# 验证用户数据
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, real_name, permission_level FROM users ORDER BY permission_level DESC;"
```

---

## 二、执行测试

### 2.1 第一步：导入XLSX测试数据

这是后续测试的基础，必须先执行：

```bash
# 在容器内执行XLSX导入脚本
docker exec emily-core python scripts/import_nodes_xlsx.py \
    "需求文件/全景节点图V2/测试文件/附件13：【雄安青藤小镇书院项目】蓝城伟业项目内控计划节点分解表.xlsx" \
    --project-id project-xiongan-001 \
    --creator-id user-admin-wang

# 验证导入结果
docker exec emily-postgres psql -U emily -d emily -c "SELECT COUNT(*) as node_count FROM project_nodes WHERE project_id = 'project-xiongan-001';"
```

### 2.2 第二步：运行完整测试套件

```powershell
# 进入测试文件目录
cd 需求文件\全景节点图V2\测试文件

# 运行P0优先级测试（推荐先运行）
python panorama_test_runner.py --p0

# 运行完整测试套件
python panorama_test_runner.py --full
```

### 2.3 运行指定场景

```bash
# 只运行CRUD测试
python panorama_test_runner.py --scenario crud

# 运行多个场景
python panorama_test_runner.py --scenario crud,state,dep

# 可用场景：
#   xlsx      - XLSX导入验证
#   crud      - 节点CRUD
#   state     - 状态机流转
#   dep       - 依赖管理
#   perm      - 权限测试
#   integrity - 数据完整性
```

---

## 三、手动对话测试（使用emy-test）

如果你想手动模拟对话测试，可以使用以下方式：

### 3.1 启动Python交互模式

```python
import sys
sys.path.insert(0, ".claude/skills/emy-test")
from tester import EmysTester

# 创建测试器
emy = EmysTester()
emy.start()

# 发送消息测试
reply = emy.send_sync("创建节点SG-DEMO-001，名称'演示节点'，截止2026-12-31", sender_name="王总")
print(reply.content)

# 查询数据库验证
with emy.get_db_session() as conn:
    from sqlalchemy import text
    result = conn.execute(text("SELECT node_id, node_name, status FROM project_nodes WHERE node_id = 'SG-DEMO-001'"))
    for row in result:
        print(row)

# 完成后关闭
emy.stop()
```

### 3.2 常用测试对话

```python
# 1. 创建节点
emy.send_sync("创建节点SG-DEMO-001，名称'景观设计'，截止到2026年8月31日", sender_name="王总")

# 2. 添加成果
emy.send_sync("给SG-DEMO-001添加成果：景观施工图，目标1份", sender_name="王总")

# 3. 更新进度
emy.send_sync("更新SG-DEMO-001的景观施工图进度：完成1份", sender_name="王总")

# 4. 查看状态
reply = emy.send_sync("查看SG-DEMO-001的当前状态和进度", sender_name="王总")
print(reply.content)
```

---

## 四、数据库验证SQL

### 4.1 快速检查数据

```sql
-- 节点统计
SELECT status, COUNT(*) FROM project_nodes GROUP BY status;

-- 成果统计
SELECT node_id, COUNT(*) as deliverables, 
       SUM(CASE WHEN CAST(current_amount AS DECIMAL) >= CAST(target_amount AS DECIMAL) 
                THEN 1 ELSE 0 END) as completed
FROM node_deliverables 
GROUP BY node_id
ORDER BY node_id;

-- 事件日志
SELECT event_type, COUNT(*) FROM node_events GROUP BY event_type;
```

### 4.2 查看父子层级

```sql
-- 查看有子节点的父节点
SELECT 
    parent.node_id,
    parent.node_name,
    COUNT(child.id) as child_count,
    parent.progress as parent_progress
FROM project_nodes parent
JOIN project_nodes child ON parent.node_id = child.parent_node_id
GROUP BY parent.node_id, parent.node_name, parent.progress
ORDER BY child_count DESC;
```

---

## 五、测试文件说明

```
需求文件/全景节点图V2/测试文件/
├── README_快速开始.md                    # 本文档
├── 全景节点图V2_完整测试计划.md         # 详细测试计划文档
├── panorama_test_runner.py               # 自动测试执行脚本
├── 附件13：【雄安青藤小镇书院项目】蓝城伟业项目内控计划节点分解表.xlsx
                                            # 测试用XLSX文件
└── test_results_YYYYMMDD_HHMMSS.json     # 测试结果输出（自动生成）
```

---

## 六、常见问题

### Q1: 运行测试脚本时报错 "无法导入emy-test模块"

**A**: 确保你在正确的目录下运行，或者检查路径：
```powershell
# 应该在项目根目录 d:\app\Emily 下运行
cd d:\app\Emily
python 需求文件\全景节点图V2\测试文件\panorama_test_runner.py --p0
```

### Q2: XLSX导入失败

**A**: 检查路径是否正确，文件是否存在：
```bash
# 验证文件存在
ls "需求文件/全景节点图V2/测试文件/*.xlsx"
```

### Q3: 权限测试不生效

**A**: 确保已导入测试用户数据，并且检查用户ID是否正确：
```sql
SELECT id, real_name, permission_level FROM users WHERE real_name IN ('王总', '张工', '周业务员');
```

### Q4: 如何清理测试数据

**A**: 执行以下SQL清理测试节点（保留真实业务数据）：

```sql
-- 清理测试数据
DELETE FROM node_events WHERE node_id LIKE 'SG-TEST-%';
DELETE FROM node_dependencies WHERE node_id LIKE 'SG-TEST-%';
DELETE FROM node_deliverables WHERE node_id LIKE 'SG-TEST-%';
DELETE FROM project_nodes WHERE node_id LIKE 'SG-TEST-%';
```

---

## 七、测试通过标准

| 测试类型 | 通过标准 |
|---------|---------|
| XLSX导入 | 节点数 > 0 |
| 节点CRUD | 创建/查询/废弃全部成功 |
| 状态机 | 初始CONDITIONS_NOT_MET → 完成成果后COMPLETED |
| 依赖管理 | 前置未满足时下游为CONDITIONS_NOT_MET |
| 权限控制 | 访客无法创建节点 |
| 数据完整性 | 5张表结构完整有数据 |

---

**如有问题，请查看完整测试计划文档：`全景节点图V2_完整测试计划.md`**
