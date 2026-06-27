# Emily Test 技能 - 权限测试说明

## 📋 概述

本技能已升级，支持从数据库选择用户进行消息模拟，便于测试不同权限级别的用户与 Emily Core 的交互。

## 🔧 数据库初始化

### 1. 清理无用字段（可选）

如果 users 表有历史遗留的测试字段需要清理，执行：

```bash
# 连接到 PostgreSQL 数据库
docker exec -i emily-postgres psql -U emily -d emily < emily-core/emily_core/infrastructure/database/scripts/001_cleanup_users_table.sql
```

### 2. 导入测试数据

为了测试不同权限级别，先导入预设的测试用户和单位数据：

```bash
docker exec -i emily-postgres psql -U emily -d emily < emily-core/emily_core/infrastructure/database/scripts/002_seed_test_data.sql
```

### 测试数据包含的用户

| 用户 | 权限级别 | 所属单位 | 说明 |
|------|----------|----------|------|
| 王总 | 系统管理员 (6) | XX地产建设集团 | 最高权限 |
| 李经理 | 建设主管 (4) | XX地产建设集团 | 甲方工程部经理 |
| 张工 | 参建管理 (3) | 中天建设集团 | 总包项目经理 |
| 陈监理 | 参建管理 (3) | 恒大监理有限公司 | 监理工程师 |
| 赵工 | 参建管理 (3) | 上海建筑设计研究院 | 设计师 |
| 孙师傅 | 参建执行 (2) | 中天建设集团 | 施工员 |
| 周业务员 | 访客 (1) | 鑫达建材供应商 | 最低权限 |

## 🚀 启动 Web 测试控制台

```bash
python .claude/skills/emy-test/emy_web/app.py
```

默认访问地址：http://localhost:8000

## 🎯 功能特性

### 1. 用户下拉选择
- 自动从数据库加载所有活跃用户
- 按权限级别从高到低排序显示
- 每条显示：姓名（权限级别 - 所属单位）
- 支持"🔄 刷新用户列表"按钮动态刷新

### 2. 权限测试场景

选择不同权限的用户发送消息，验证权限控制逻辑：

#### 场景 A：系统管理员（王总）
- 预期：可以执行所有操作，无权限限制
- 测试命令示例："查看所有项目"

#### 场景 B：建设主管（李经理）
- 预期：可以查看和管理项目，但部分系统配置不可访问
- 测试命令示例："审批设计变更"

#### 场景 C：参建管理（张工 / 陈监理 / 赵工）
- 预期：可以处理本职工作相关的任务，但无法跨权限操作
- 测试命令示例："更新施工进度"

#### 场景 D：参建执行（孙师傅）
- 预期：只能上报和查看自己负责的任务
- 测试命令示例："上报今日完成工作"

#### 场景 E：访客（周业务员 / 自定义访客）
- 预期：只能查询公开信息，所有写入操作被拒绝
- 测试命令示例："查询供应商列表"

### 3. 消息追踪
- 每条用户消息显示发送者身份和权限
- 格式：`👤 姓名（权限级别 - 所属单位）`
- 便于验证权限系统是否正确识别用户身份

## 🔍 验证清单

启动测试后，请验证以下功能是否正常：

- [ ] 下拉菜单正确显示所有测试用户
- [ ] 用户按权限级别从高到低排序
- [ ] 点击"刷新用户列表"按钮能重新加载
- [ ] 选择不同用户发送消息，消息头显示正确的身份
- [ ] 选择"自定义访客"时使用最低权限
- [ ] 不同权限用户发送相同指令得到不同结果（符合权限控制）
- [ ] 数据库连接失败时 UI 仍能正常工作（降级到访客模式）

## 📝 配置说明

### 环境变量

创建或修改项目根目录 `.env` 文件：

```env
# Emily Core 地址
EMILY_CORE_URL=http://localhost:18080

# PostgreSQL 连接
EMILY_DATABASE_URL=postgresql://emily:emily_secret_2026@localhost:25432/emily
```

### 依赖要求

确保已安装 SQLAlchemy：

```bash
pip install sqlalchemy psycopg2-binary
```

## 🚨 故障排查

### 问题：下拉菜单不显示用户

**原因**：数据库连接失败或测试数据未导入

**解决方案**：
1. 检查 PostgreSQL 容器是否运行
2. 验证测试数据是否正确导入
3. 检查 `.env` 中的数据库连接配置

### 问题：用户发送消息无响应

**原因**：Emily Core 未启动或网络不通

**解决方案**：
1. 检查 emily-core 容器状态
2. 验证 `EMILY_CORE_URL` 配置正确
3. 查看浏览器控制台和 Python 终端日志

### 问题：权限测试无效果

**原因**：权限模块未启用或用户数据错误

**解决方案**：
1. 确认 Emily Core 已集成权限系统
2. 验证数据库中 users 表的 permission_level 字段值正确
3. 查看后端日志排查权限检查逻辑

## 📂 文件结构

```
.claude/skills/emy-test/
├── emy_web/
│   └── app.py              # Web UI（已升级：下拉选择用户）
├── config_loader.py        # 配置加载（新增数据库查询函数）
├── tester.py               # 核心测试引擎
├── emys_tester.py          # 入口文件
├── cli.py                  # 命令行接口
├── README_权限测试.md       # 本文档
└── SKILL.md               # 技能元数据

emily-core/emily_core/infrastructure/database/scripts/
├── 001_cleanup_users_table.sql    # 清理无用字段脚本
└── 002_seed_test_data.sql        # 测试数据脚本
```
