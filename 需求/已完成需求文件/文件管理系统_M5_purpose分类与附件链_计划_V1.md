# 文件管理系统 M5 — purpose 分类与附件链实施计划

> **基于设计**:[需求/文件管理系统.md](../需求/文件管理系统.md)(权威设计依据)
> **计划版本**:v1.0
> **前置**:M1(FileManager 收敛)已完成;M3(AttachmentDownloader)已存在;M4 的版本链/删除工具若未实施则由本计划 M5-4 承接
> **目标**:落地三层正交分类(purpose/关联/RAG 索引)+ attachment_of 主从附件链(修订 M4 的 group_id 方案)+ 分辨规则引擎 + 工具 description 内嵌原则(按需加载)
> **不在范围**:用户面下载 API、NAS/OSS 迁移、文件内容识别(见 [需求/强化文件分类识别.md](../需求/强化文件分类识别.md) backlog)

---

## Context

**为什么做 M5**:M1-M4 解决了文件能力闭环(发文件/自动下载/关联版本/权限统一),但**分类学根源问题**没解决——`file_category` 7 类混淆"业务意图"和"文档类别","优秀工艺参考"和"梗图"都落 OTHER。同时 M4 原方案的 `group_id` 成组被用户修订为更符合工程语义的 **`attachment_of` 主从附件链**(合格单←各专业施工图)。

**M5 范围**:
- 三层正交分类落地:`purpose`(5 类)+ `attachment_of`(附件链)+ `rag_indexed`/`rag_collection`(RAG 标志)
- 分辨规则引擎(入站候选 purpose)
- 工具 description 内嵌原则(按需加载,符合 P1-1)
- 4 个新工具:update_file_purpose / link_to_master / unlink_attachment / list_attachments
- REFERENCE 异步入 RAG
- 规则书加"七、文件处理原则"章节(人读,不注入 LLM)
- 历史数据迁移(按 file_category 推断 purpose)

**预期收益**:文件分类学清晰(业务意图/文档类别/关联结构正交);附件链表达"一套文件有攒总主文件"的工程语义;REFERENCE 自动入通用 RAG 库跨项目可检索;CHAT 不入库避免污染。

---

## 探查发现的关键约束(影响实施)

1. **available_tools 渲染含 description**([workitem_agent.py:322](../emily-core/emily_core/workitem/workitem_agent.py#L322)):`tool_entries.append(f"- {name}: {tool.description}{schema_summary}")`。**工具 description 是 LLM 按需看到原则的现成载体**,不需要新占位符。且按 `session_api_ids` 过滤——无权限用户看不到 record_file,也看不到原则。
2. **P1-1 已移除 `{rule_book}` 占位符**([session_context.py:365-368](../emily-core/emily_core/session/session_context.py#L365-L368)):规则书当前**不注入 LLM prompt**。规则书只能作人读文档,LLM 看不到。purpose 原则走工具 description,不走规则书。
3. **`_ensure_columns` 自动补齐**([bootstrap.py](../emily-core/emily_core/bootstrap.py) `init_db` 内置):新字段需注册到 `_PENDING_COLUMNS` 映射,`init_db()` 启动时自动 ALTER TABLE 补齐(CLAUDE.md 踩坑:`create_all()` 不 ALTER 已有表)。
4. **五处注册同步**(新增工具必须):`tools/registry.py`(`_register_business`)+ `tools_consistency.py`(`REGISTERED_TOOLS`/`TOOL_META_MAP`/`TOOL_SCHEMA_MAP`)+ `seed_tool_registry.sql` + 涉及的 Skill YAML。漏一处 V13 一致性检查报警。
5. **embed_and_index 工具已存在**([embed_tool.py](../emily-core/emily_core/tools/embed_tool.py)):M5-5 复用,不新建。需注入 `tei_client` + `knowledge_chunk_repo`。
6. **File 模型版本/关联字段已就位**([models.py:354-376](../emily-core/emily_core/infrastructure/database/models.py#L354-L376)):`version`/`is_latest`/`parent_file_id`/`source_module_id`/`source_module_type`/`confidentiality` 全部存在。M5 只加 5 个新字段,不改已有。
7. **AttachmentDownloader 已存在**(M3 新建):M5-2 在其 `_download_one` 内调规则引擎,不新建下载逻辑。
8. **FileManager 已存在**(M1 新建):M5-3/M5-4 在其上扩展方法,不改 M1 已有接口。
9. **`__pycache__` 不自动刷新**(CLAUDE.md 踩坑):每次代码变更后 `docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +`。

---

## 你的角色

你作为 **Emily 开发者资深架构师** + **实施计划编制专家**,严格按 M5-1→M5-2→M5-3→M5-4→M5-5→M5-6 顺序执行,逐模块验证,验证不通过不进入下一个模块。

## 硬约束(违反即失败)

1. **不改现有 4 个工具的外部 schema 契约**:`record_file` 仅**新增** `purpose` 字段(默认 RECORD,向后兼容),`query_files`/`update_file_category`/`search_files` 不变
2. **业务内核独立**(CLAUDE.md 约束 1):`emily_core` 不 import `astrbot.*`
3. **分层不可跳**(CLAUDE.md 约束 2):工具 handler → Application → FileManager(Service) → Repository → DB。业务规则归 Service,Repository 纯 CRUD
4. **attachment_of 禁止嵌套**:校验在 `FileManager.link_to_master` 内(Service 层),Repository 只管 `update_attachment_of` 字段
5. **purpose 原则走工具 description**:不注入 workitem.md,不加 `{rule_book}` 占位符(违背 P1-1)。规则书章节是人读,不注入 LLM
6. **REFERENCE 入 RAG 异步**:record_file handler 内 `asyncio.create_task`,不阻塞用户回复
7. **CHAT 不入 files 表**:规则引擎判定 CHAT 的附件,AttachmentDownloader 跳过下载(只留 messages.attachments URL)
8. **五处注册同步**:每个新工具必须同步 5 处(`registry.py` + `tools_consistency.py` 3 处 + `seed_tool_registry.sql` + Skill YAML)
9. **每模块验收**:验收检测必须通过,否则停止报告
10. **改完代码清 `__pycache__`**:`docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +`
11. **历史数据迁移不读文件内容**:按 file_category 标签映射,OTHER 标 `purpose_confirmed=False`

## 代码模式参照表

| 层 | 参照源 | 要模仿的要点 |
|----|------|-------------|
| 字段注册 | `_PENDING_COLUMNS` 映射(bootstrap.py init_db 内) | `{表名: [(列名, DDL), ...]}` |
| 枚举类 | [models.py:288-327](../emily-core/emily_core/infrastructure/database/models.py#L288-L327) `FileCategory` | 类属性 + ALL + DISPLAY_NAMES + validate + display |
| Repository 纯 CRUD | [file_repo.py:136-146](../emily-core/emily_core/repositories/file_repo.py#L136-L146) `update_category` | 只改字段,不懂业务规则 |
| Service 业务规则 | [file_service.py:63-79](../emily-core/emily_core/services/file_service.py#L63-L79) `update_file_category` | 校验 + 调 Repo + 日志 |
| 工具 handler | [file_tool.py:79-145](../emily-core/emily_core/tools/file_tool.py#L79-L145) `handle_record_file` | `handle_xxx(params, app, **kwargs)` + schema 常量 |
| 工具 description | [file_tool.py:64-76](../emily-core/emily_core/tools/file_tool.py#L64-L76) `_FILE_TOOL_DESCRIPTION` | 多行字符串,字段分级 + 判断依据 |
| 五处注册 | [tools_consistency.py:33-106](../emily-core/emily_core/infrastructure/tools_consistency.py#L33-L106) | REGISTERED_TOOLS set + TOOL_META_MAP + TOOL_SCHEMA_MAP |
| available_tools 渲染 | [workitem_agent.py:313-323](../emily-core/emily_core/workitem/workitem_agent.py#L313-L323) | `f"- {name}: {tool.description}{schema_summary}"` |
| embed_and_index 调用 | [embed_tool.py](../emily-core/emily_core/tools/embed_tool.py) `handle_embed_and_index` | 注入 tei + repo,异步包装 |
| 规则书章节 | [规则书.md](../emily-data/rules/规则书.md) 现有 6 章 | `## 七、xxx` + 编号列表 |
| 迁移脚本 | [scripts/manage_nodes.py](../scripts/manage_nodes.py) CLI 模式 | argparse + dry-run + 实际写入 |

## 模块依赖图

```
M5-1(字段+迁移) ──→ M5-2(规则引擎+description)
       │                    │
       │                    ↓
       │              M5-5(RAG 自动化)
       ↓
M5-3(附件链工具) ──→ M5-4(purpose 校正+版本/删除)
                            │
                            ↓
                       M5-6(规则书+文档)
```

- **M5-1 独立奠基**:5 个新字段 + 历史数据迁移。其他模块都依赖
- **M5-2 依赖 M5-1**:规则引擎写 purpose 字段;description 内嵌不改字段但依赖 purpose 概念
- **M5-3 依赖 M5-1**:attachment_of 字段
- **M5-4 依赖 M5-1 + M5-3**:purpose 字段 + 复用附件链校验模式
- **M5-5 依赖 M5-1 + M5-2**:rag_indexed 字段 + purpose 判断(REFERENCE 才入库)
- **M5-6 最后**:文档同步
- **M5-2 与 M5-3 可并行**:都依赖 M5-1,互不依赖

---

## 交付物总览

| 模块 | 交付文件 | 新增/修改 | 核心改动 |
|------|----------|----------|----------|
| M5-1 | `emily-core/emily_core/infrastructure/database/models.py` | 修改 | File 模型加 5 字段 + FilePurpose 枚举类 |
| M5-1 | `emily-core/emily_core/bootstrap.py` | 修改 | `_PENDING_COLUMNS` 注册 5 字段 |
| M5-1 | `scripts/migrate_file_purpose.py` | **新增** | 一次性迁移:file_category → purpose |
| M5-2 | `emily-core/emily_core/services/file_rule_engine.py` | **新增** | FileRuleEngine.guess_purpose |
| M5-2 | `emily-core/emily_core/services/attachment_downloader.py`(M3 已建) | 修改 | `_download_one` 调规则引擎;CHAT 跳过 |
| M5-2 | `emily-core/emily_core/tools/file_tool.py` | 修改 | `_FILE_TOOL_DESCRIPTION` 内嵌原则 + `_FILE_TOOL_SCHEMA` 加 purpose + handler 接受 purpose |
| M5-3 | `emily-core/emily_core/services/file_manager.py`(M1 已建) | 修改 | link_to_master / unlink_attachment / list_attachments + 禁止嵌套校验 |
| M5-3 | `emily-core/emily_core/repositories/file_repo.py` | 修改 | update_attachment_of / query_attachments |
| M5-3 | `emily-core/emily_core/tools/file_tool.py` | 修改 | 3 个新 handler + schema |
| M5-3 | `emily-core/emily_core/tools/registry.py` + `tools_consistency.py` + `seed_tool_registry.sql` | 修改 | 注册 3 工具(五处同步) |
| M5-4 | `emily-core/emily_core/services/file_manager.py` | 修改 | update_purpose / create_version / soft_delete / list_versions |
| M5-4 | `emily-core/emily_core/tools/file_tool.py` | 修改 | 4 个新 handler(update_file_purpose / new_file_version / delete_file / list_file_versions) |
| M5-4 | `emily-core/emily_core/tools/registry.py` + `tools_consistency.py` + `seed_tool_registry.sql` | 修改 | 注册 4 工具(若 M4 已建版本/删除则只补 update_file_purpose) |
| M5-5 | `emily-core/emily_core/tools/file_tool.py` | 修改 | `handle_record_file` 内 REFERENCE 异步触发 embed_and_index |
| M5-5 | `emily-core/emily_core/services/file_manager.py` | 修改 | set_rag_indexed 方法 |
| M5-6 | `emily-data/rules/规则书.md` | 修改 | 加"七、文件处理原则"章节 |
| M5-6 | `docs/业务模块与运转全景.md` + `docs/接口协议与调用约定.md` + `docs/数据库设计.md` + `docs/代码文件目录.md` | 修改 | 同步文档 |

---

## M5-1:数据库字段扩展 + 历史数据迁移

### 目标

files 表加 5 个新字段,启动时自动 ALTER 补齐;历史数据按 file_category 推断 purpose。

### 改 `infrastructure/database/models.py`

新增 `FilePurpose` 枚举类(参照 `FileCategory` 模式):

```python
class FilePurpose:
    """文件业务意图枚举(主分类)。"""
    EVIDENCE = "EVIDENCE"      # 凭证证据
    RECORD = "RECORD"          # 工作记录
    DESIGN = "DESIGN"          # 设计图纸
    REFERENCE = "REFERENCE"    # 参考样例
    CHAT = "CHAT"              # 闲聊素材(不入 files 表,枚举完整性)

    ALL_IN_DB = [EVIDENCE, RECORD, DESIGN, REFERENCE]  # CHAT 不入库
    DISPLAY_NAMES = {
        EVIDENCE: "凭证证据", RECORD: "工作记录",
        DESIGN: "设计图纸", REFERENCE: "参考样例", CHAT: "闲聊素材",
    }

    @classmethod
    def validate(cls, value: str) -> str:
        return value if value in cls.ALL_IN_DB else cls.RECORD

    @classmethod
    def display(cls, value: str) -> str:
        return cls.DISPLAY_NAMES.get(value, "工作记录")
```

`File` 模型加 5 字段(在现有字段后):

```python
    # ── M5: 三层正交分类新增字段 ──
    purpose = Column(String(50), default="RECORD", comment="业务意图:EVIDENCE/RECORD/DESIGN/REFERENCE(CHAT 不入库)")
    purpose_confirmed = Column(Boolean, default=False, comment="LLM/用户是否已确认 purpose")
    attachment_of = Column(String, ForeignKey("files.id"), nullable=True, comment="附件链:指向主文件 id,NULL=主/独立")
    rag_indexed = Column(Boolean, default=False, comment="是否已入 RAG 知识库")
    rag_collection = Column(String(100), default="", comment="RAG 集合名:general_reference/project_<id>")
```

### 改 `bootstrap.py` `_PENDING_COLUMNS`

注册 5 字段(参照现有 `_PENDING_COLUMNS` 映射格式):

```python
_PENDING_COLUMNS = {
    ...,
    "files": [
        ("purpose", "VARCHAR(50) DEFAULT 'RECORD'"),
        ("purpose_confirmed", "BOOLEAN DEFAULT FALSE"),
        ("attachment_of", "VARCHAR"),
        ("rag_indexed", "BOOLEAN DEFAULT FALSE"),
        ("rag_collection", "VARCHAR(100) DEFAULT ''"),
    ],
}
```

### 新建 `scripts/migrate_file_purpose.py`

一次性迁移脚本(参照 `manage_nodes.py` CLI 模式):

```python
"""一次性迁移:按 file_category 推断 purpose,标 purpose_confirmed。"""

# 映射表(设计文档 §十三 事项 1)
CATEGORY_TO_PURPOSE = {
    "PROJECT_LICENSE": "EVIDENCE",
    "CONTRACT": "EVIDENCE",
    "WORK_RECORD": "RECORD",
    "PHASE_DELIVERABLE": "RECORD",
    "PROCESS_DOC": "DESIGN",
    "MANAGEMENT_SPEC": "REFERENCE",
    "OTHER": "RECORD",  # 兜底,标 purpose_confirmed=False
}

def migrate(dry_run=True):
    with get_session() as session:
        files = session.query(File).filter(File.is_deleted == False).all()
        for f in files:
            new_purpose = CATEGORY_TO_PURPOSE.get(f.file_category or "OTHER", "RECORD")
            f.purpose = new_purpose
            f.purpose_confirmed = (f.file_category != "OTHER")  # OTHER 标未确认
        if not dry_run:
            session.commit()
```

CLI:`--dry-run` 预览,`--apply` 实际写入。

### M5-1 验收检测

```bash
# 1. 清 pycache + 重启(触发 _ensure_columns)
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core

# 2. 验证字段存在
docker exec emily-postgres psql -U emily -d emily -c "\d files" | grep -E "purpose|attachment_of|rag_indexed|rag_collection"

# 3. 迁移脚本预览
uv run python scripts/migrate_file_purpose.py --dry-run

# 4. 实际迁移
uv run python scripts/migrate_file_purpose.py --apply

# 5. 验证迁移结果
docker exec emily-postgres psql -U emily -d emily -c "
SELECT purpose, purpose_confirmed, COUNT(*) FROM files WHERE is_deleted=false GROUP BY purpose, purpose_confirmed;"
# 预期:EVIDENCE/RECORD/DESIGN/REFERENCE 都有分布,OTHER 类 purpose_confirmed=False
```

**通过标准**:5 字段存在;迁移后所有非删除文件有 purpose 值;OTHER 类 purpose_confirmed=False。

---

## M5-2:分辨规则引擎 + 工具 description 内嵌

### 目标

入站附件由规则引擎给候选 purpose;LLM 录入时从 record_file 工具 description 看到判断原则(按需加载)。

### 新建 `services/file_rule_engine.py`

```python
"""FileRuleEngine —— 入站附件候选 purpose 推断。

基于文件名/扩展名/MIME/来源渠道的规则匹配。不读文件内容。
未来增强方向见 需求/强化文件分类识别.md。
"""

import re

class FileRuleEngine:
    # 规则表(按优先级,首个命中返回)
    RULES = [
        # CHAT:梗图/表情包(不入库)
        (lambda f: f["ext"] in (".gif",) or _match(f["name"], ["表情", "meme", "梗", "包情"]), "CHAT"),
        # EVIDENCE:证照/合同/批复
        (lambda f: _match(f["name"], ["证", "许可", "执照", "合同", "批复", "合格单", "审批"]), "EVIDENCE"),
        # DESIGN:图纸
        (lambda f: f["ext"] in (".dwg", ".dxf") or _match(f["name"], ["施工图", "设计图", "图纸", "竣工图"]), "DESIGN"),
        # REFERENCE:参考样例
        (lambda f: _match(f["name"], ["参考", "样例", "规范", "工艺", "做法", "样板"]), "REFERENCE"),
    ]

    @staticmethod
    def guess_purpose(filename: str, mime: str = "", context: dict | None = None) -> str:
        """推断候选 purpose。返回 EVIDENCE/RECORD/DESIGN/REFERENCE/CHAT。"""
        f = {
            "name": filename or "",
            "ext": _ext(filename),
            "mime": mime or "",
        }
        for rule, purpose in FileRuleEngine.RULES:
            try:
                if rule(f):
                    return purpose
            except Exception:
                continue
        return "RECORD"  # 默认

def _match(name: str, keywords: list[str]) -> bool:
    return any(k in name for k in keywords)

def _ext(filename: str) -> str:
    import os
    return os.path.splitext(filename)[1].lower()
```

### 改 `services/attachment_downloader.py`(M3 已建)

`_download_one` 内:

```python
async def _download_one(self, message_id, att):
    url = att.get("url", "")
    if not url:
        return
    filename = att.get("file_name", "") or att.get("summary", "")

    # M5: 规则引擎判定候选 purpose
    from ..services.file_rule_engine import FileRuleEngine
    candidate_purpose = FileRuleEngine.guess_purpose(filename, att.get("mime", ""))

    # CHAT 不入库(只留 messages.attachments URL)
    if candidate_purpose == "CHAT":
        logger.info("Attachment skipped (CHAT): msg=%s file=%s", message_id, filename)
        return

    # 下载 + 入库(带候选 purpose)
    result = await self._fm.store_attachment(
        message_id=message_id,
        attachment_url=url,
        attachment_type=att.get("type", 0),
        source_filename=filename,
        purpose=candidate_purpose,           # 传入候选 purpose
        purpose_confirmed=False,             # 规则给的,未确认
    )
```

`FileStorageService.store_attachment_async` / `_finalize_store` 需补 `purpose` / `purpose_confirmed` 参数,写入 files 表。

### 改 `tools/file_tool.py` `_FILE_TOOL_DESCRIPTION` + `_FILE_TOOL_SCHEMA`

description 内嵌原则(设计文档 §十一):

```python
_FILE_TOOL_DESCRIPTION = (
    "记录一个文件信息。\n"
    "⚠️ 调用前必须完成拟录入单流程(见系统指令第10条)。\n"
    "\n"
    "purpose(业务意图,必填)5 类:\n"
    "  EVIDENCE — 凭证证据(证照/许可/合同/批复/合格单),永久保留\n"
    "  RECORD — 工作记录(验收附图/施工记录/会议附件),项目周期\n"
    "  DESIGN — 设计图纸(施工图/过程图),走版本链\n"
    "  REFERENCE — 参考样例(优秀工艺/做法参考),入通用 RAG\n"
    "  CHAT — 闲聊素材(不入库,record_file 不会收到)\n"
    "\n"
    "判断依据:文件名 + 用户对话 + 上下文(不读文件内容)。\n"
    "  文件名含'证/许可/执照/合同/批复/合格单' → EVIDENCE\n"
    "  文件名含'图/.dwg/施工图/设计' → DESIGN\n"
    "  文件名含'参考/样例/规范/工艺' → REFERENCE\n"
    "  其余 → RECORD(默认)\n"
    "\n"
    "RAG 入库策略(代码自动,LLM 无需关心):仅 REFERENCE 自动异步入通用 RAG 库。\n"
    "\n"
    "字段分级:\n"
    "  [必有] filename — 文件名\n"
    "  [必有] purpose — 业务意图(上述 5 类,默认 RECORD)\n"
    "  [应有] project_name — 关联项目名称\n"
    "  [应有] file_type — 文件类型(从后缀推断)\n"
    "\n"
    "守护核验三选一(仅在核验不通过时):\n"
    "  force=false — 正常录入\n"
    "  force=true + guardian_notes — 坚持录入(写入待解决清单)"
)
```

`_FILE_TOOL_SCHEMA` 的 `data.properties` 加 purpose:

```python
"purpose": {
    "type": "string",
    "description": "业务意图:EVIDENCE/RECORD/DESIGN/REFERENCE(CHAT 不入库,默认 RECORD)",
    "default": "RECORD",
},
```

`handle_record_file` 接受 purpose,传入 RouteResult.data:

```python
data["purpose"] = data.get("purpose") or params.get("purpose") or "RECORD"
```

`FileApplication.handle_file` + `FileService.create_file_record` 补 purpose 参数写入。

### M5-2 验收检测

```bash
# 1. 清 pycache + 重启
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core

# 2. 规则引擎单元测试
uv run python -c "
from emily_core.services.file_rule_engine import FileRuleEngine
assert FileRuleEngine.guess_purpose('施工许可证.pdf') == 'EVIDENCE'
assert FileRuleEngine.guess_purpose('建筑施工图.dwg') == 'DESIGN'
assert FileRuleEngine.guess_purpose('优秀工艺参考.jpg') == 'REFERENCE'
assert FileRuleEngine.guess_purpose('meme.gif') == 'CHAT'
assert FileRuleEngine.guess_purpose('会议纪要.docx') == 'RECORD'
print('OK')
"

# 3. 实战:发一张梗图,确认不入库
# (需 emy-test 支持 attachments,或真实 QQ 发图)
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "[梗图]" --sender "<用户名>"
docker exec emily-postgres psql -U emily -d emily -c "
SELECT COUNT(*) FROM files f JOIN message_attachments ma ON ma.file_id=f.id 
WHERE ma.message_id='<刚才消息id>';"
# 预期:0(CHAT 不入库)

# 4. 实战:record_file 时 LLM 从 description 看到 purpose 原则
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "归档施工许可证 file_test.pdf" --sender "<用户名>"
# 验证 LLM 输出 purpose=EVIDENCE(从 description 学到的判断依据)
docker exec emily-postgres psql -U emily -d emily -c "
SELECT file_no, purpose, purpose_confirmed FROM files ORDER BY created_at DESC LIMIT 1;"

# 5. dump planner prompt 确认 description 已注入
uv run python scripts/dump_session_prompt.py --planner --sop SOP-001-REC 2>&1 | grep -A3 "record_file"
# 预期:看到 record_file 描述含 purpose 5 类
```

**通过标准**:规则引擎 5 case 通过;CHAT 附件不入库;LLM 录入时 purpose 判断符合 description 原则;planner prompt 含 description。

---

## M5-3:附件链工具(attachment_of)

### 目标

落地主从附件链:link_to_master(挂载)/ unlink_attachment(卸载提升)/ list_attachments(列表)。禁止嵌套校验在 Service 层。

### 改 `repositories/file_repo.py` 补纯 CRUD

```python
@staticmethod
def update_attachment_of(file_id: str, master_file_id: str | None) -> File | None:
    """纯 CRUD:更新 attachment_of 字段。master_file_id=None 表示卸载。"""
    with get_session() as session:
        f = session.query(File).filter(File.id == file_id, File.is_deleted == False).first()
        if f is None:
            return None
        f.attachment_of = master_file_id  # None 或 id
        session.commit()
        return f

@staticmethod
def query_attachments(master_file_id: str) -> list[File]:
    """查询主文件下的所有附件(不含主文件本身)。"""
    with get_session() as session:
        return session.query(File).filter(
            File.attachment_of == master_file_id,
            File.is_deleted == False,
        ).order_by(File.created_at).all()
```

### 改 `services/file_manager.py`(M1 已建)补业务规则

```python
def link_to_master(self, file_id: str, master_file_id: str, operator_id: str = "") -> dict:
    """挂载附件到主文件。禁止嵌套校验(Service 层)。"""
    # 1. 主文件必须存在且未删除
    master = self._file_repo.get_by_id(master_file_id)
    if master is None or master.is_deleted:
        return {"success": False, "error": "主文件不存在或已删除"}
    # 2. 禁止嵌套:主文件自身 attachment_of 必须为 NULL
    if master.attachment_of is not None:
        return {"success": False, "error": "禁止嵌套:目标文件本身是附件,不能作为主文件"}
    # 3. 禁止自挂
    if file_id == master_file_id:
        return {"success": False, "error": "不能挂载到自己"}
    # 4. 调 Repository 更新
    result = self._file_repo.update_attachment_of(file_id, master_file_id)
    if result is None:
        return {"success": False, "error": "附件文件不存在"}
    logger.info("link_to_master: %s → %s by %s", file_id, master_file_id, operator_id)
    return {"success": True, "file_no": result.file_no}

def unlink_attachment(self, file_id: str, operator_id: str = "") -> dict:
    """卸载附件,提升为独立文件(attachment_of=NULL)。"""
    result = self._file_repo.update_attachment_of(file_id, None)
    if result is None:
        return {"success": False, "error": "文件不存在"}
    return {"success": True, "file_no": result.file_no}

def list_attachments(self, master_file_id: str) -> list[File]:
    """列出主文件下的附件。"""
    return self._file_repo.query_attachments(master_file_id)
```

### 在 `tools/file_tool.py` 加 3 个 handler

```python
_LINK_TO_MASTER_SCHEMA = {
    "type": "object",
    "properties": {
        "file_no": {"type": "string", "description": "要挂载的附件文件编号"},
        "master_file_no": {"type": "string", "description": "主文件编号"},
    },
    "required": ["file_no", "master_file_no"],
}

async def handle_link_to_master(params, file_manager, user_id="", **kwargs):
    file_no = params.get("file_no", "")
    master_no = params.get("master_file_no", "")
    f = file_manager._file_repo.get_by_file_no(file_no)
    m = file_manager._file_repo.get_by_file_no(master_no)
    if not f or not m:
        return {"success": False, "reply": "文件编号不存在"}
    result = file_manager.link_to_master(f.id, m.id, operator_id=user_id)
    return {"success": result["success"], "reply": result.get("error") or f"✅ 已挂载 {file_no} → {master_no}"}

# handle_unlink_attachment / handle_list_attachments 同模式
```

### 五处注册同步(3 工具)

`tools/registry.py` `_register_business`:
```python
_buc += _reg_biz(reg, "link_to_master", "挂载附件到主文件",
                 partial(_h("file_tool", "handle_link_to_master"), file_manager=core._file_manager), "business", "write")
_buc += _reg_biz(reg, "unlink_attachment", "卸载附件为独立文件",
                 partial(_h("file_tool", "handle_unlink_attachment"), file_manager=core._file_manager), "business", "write")
_buc += _reg_biz(reg, "list_attachments", "列出主文件下的附件",
                 partial(_h("file_tool", "handle_list_attachments"), file_manager=core._file_manager), "business", "all")
```

`tools_consistency.py`:`REGISTERED_TOOLS` / `TOOL_META_MAP` / `TOOL_SCHEMA_MAP` 三处同步。

`seed_tool_registry.sql`:3 条 INSERT。

### M5-3 验收检测

```bash
# 1. 清 pycache + 重启
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core

# 2. 工具一致性检查
uv run python scripts/check_tools_consistency.py
# 预期:0 fatal,3 工具三方同步

# 3. 实战:挂载附件
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "把文件 FIL-XXXXXXXX-XXXX 挂载到主文件 FIL-YYYYYYYY-YYYY 下" --sender "<用户名>"
docker exec emily-postgres psql -U emily -d emily -c "
SELECT file_no, attachment_of FROM files WHERE file_no='FIL-XXXXXXXX-XXXX';"
# 预期:attachment_of = 主文件 id

# 4. 禁止嵌套验证:尝试把文件挂到另一个附件下
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "把 FIL-ZZZZ 挂载到 FIL-XXXX(本身是附件)下" --sender "<用户名>"
# 预期:reply "禁止嵌套"

# 5. 卸载 + 列表
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "把 FIL-XXXX 卸载为独立文件" --sender "<用户名>"
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "列出 FIL-YYYY 下的所有附件" --sender "<用户名>"
```

**通过标准**:一致性 0 fatal;挂载/卸载/列表正常;禁止嵌套生效;自挂拒绝。

---

## M5-4:purpose 校正 + 版本/删除工具

### 目标

补 update_file_purpose(改 purpose)+ 承接 M4 的 new_file_version/delete_file/list_file_versions(若 M4 未实施)。

### 改 `services/file_manager.py`

```python
def update_purpose(self, file_id: str, purpose: str, operator_id: str = "") -> dict:
    """校正 purpose,标 purpose_confirmed=True。"""
    from ..infrastructure.database.models import FilePurpose
    validated = FilePurpose.validate(purpose)
    with get_session() as session:
        f = session.query(File).filter(File.id == file_id, File.is_deleted == False).first()
        if f is None:
            return {"success": False, "error": "文件不存在"}
        f.purpose = validated
        f.purpose_confirmed = True
        session.commit()
        return {"success": True, "file_no": f.file_no, "purpose": validated}

def create_version(self, parent_file_id, new_file_id, version_label, operator_id="") -> dict:
    """创建新版本:旧版本 is_latest=False,新版本 parent_file_id 指向旧版本。"""
    # 参照 M4 计划,若 M4 已建则复用
    ...

def soft_delete(self, file_id, operator_id="") -> dict:
    """软删除:is_deleted=True。附件保持原状(设计文档 §四 事项 2)。"""
    ...

def list_versions(self, file_no) -> list:
    """列出某文件的所有版本(按 version 排序)。"""
    ...
```

### 在 `tools/file_tool.py` 加 handler

```python
async def handle_update_file_purpose(params, file_manager, user_id="", **kwargs):
    file_no = params.get("file_no", "")
    purpose = params.get("purpose", "RECORD")
    f = file_manager._file_repo.get_by_file_no(file_no)
    if not f:
        return {"success": False, "reply": "文件不存在"}
    result = file_manager.update_purpose(f.id, purpose, operator_id=user_id)
    return {"success": result["success"], "reply": f"✅ purpose 已改为 {purpose}"}

# handle_new_file_version / handle_delete_file / handle_list_file_versions
# 若 M4 已建则跳过,未建则在此实现(参照 M4 计划骨架)
```

### 五处注册同步

`update_file_purpose` 必注册。`new_file_version`/`delete_file`/`list_file_versions` 若 M4 已注册则跳过,否则在此注册。

### M5-4 验收检测

```bash
# 1. 清 pycache + 重启
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core

# 2. 工具一致性检查
uv run python scripts/check_tools_consistency.py

# 3. purpose 校正
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "把文件 FIL-XXXX 的 purpose 改为 EVIDENCE" --sender "<用户名>"
docker exec emily-postgres psql -U emily -d emily -c "
SELECT file_no, purpose, purpose_confirmed FROM files WHERE file_no='FIL-XXXX';"
# 预期:purpose=EVIDENCE, purpose_confirmed=True

# 4. 版本/删除/列表(若 M4 未实施)
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "把 FIL-XXXX 升版到 V2.0" --sender "<用户名>"
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "删除 FIL-XXXX" --sender "<用户名>"
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "列出 FIL-XXXX 的所有版本" --sender "<用户名>"
```

**通过标准**:一致性 0 fatal;purpose 校正后 confirmed=True;版本链正确;软删除生效;版本列表有序。

---

## M5-5:RAG 索引自动化(REFERENCE 异步入库)

### 目标

record_file 时若 purpose=REFERENCE,异步触发 embed_and_index 入通用 RAG 库。其余 purpose 不入库。

### 改 `tools/file_tool.py` `handle_record_file`

在 `file_app.handle_file` 成功后:

```python
# M5: REFERENCE 自动异步入 RAG
if result.success and data.get("purpose") == "REFERENCE":
    import asyncio
    asyncio.create_task(_index_reference_file(result.object_id, config))
    logger.info("REFERENCE file scheduled for RAG indexing: %s", result.object_id)
```

```python
async def _index_reference_file(file_id: str, config):
    """异步入 RAG 通用参考库。失败标 rag_indexed=False。"""
    try:
        from .embed_tool import handle_embed_and_index
        from ..infrastructure.database.session import get_session
        from ..infrastructure.database.models import File

        # 取本地路径
        with get_session() as session:
            f = session.query(File).filter(File.id == file_id).first()
            if not f or not f.storage_path:
                return
            local_path = f.storage_path

        # 调 embed_and_index(通用库)
        await handle_embed_and_index({
            "file_path": local_path,
            "collection": "general_reference",
            "metadata": {"file_no": f.file_no, "purpose": "REFERENCE"},
        }, tei=config._tei_client, repo=config._knowledge_chunk_repo)

        # 标记成功
        from ..services.file_manager import FileManager
        # 通过 FileManager.set_rag_indexed 标记
        ...
    except Exception as e:
        logger.warning("REFERENCE RAG indexing failed: %s — %s", file_id, e)
        # 失败 rag_indexed 保持 False,留兜底重试
```

### 改 `services/file_manager.py`

```python
def set_rag_indexed(self, file_id: str, indexed: bool, collection: str = "") -> bool:
    """标记 RAG 入库状态。"""
    with get_session() as session:
        f = session.query(File).filter(File.id == file_id).first()
        if f is None:
            return False
        f.rag_indexed = indexed
        if collection:
            f.rag_collection = collection
        session.commit()
        return True
```

### M5-5 验收检测

```bash
# 1. 清 pycache + 重启
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core

# 2. 实战:录入 REFERENCE 文件
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "归档优秀工艺参考图 sample.jpg" --sender "<用户名>"

# 3. 验证异步入库(等几秒)
sleep 5
docker exec emily-postgres psql -U emily -d emily -c "
SELECT file_no, purpose, rag_indexed, rag_collection FROM files 
ORDER BY created_at DESC LIMIT 1;"
# 预期:purpose=REFERENCE, rag_indexed=True, rag_collection=general_reference

# 4. 验证非 REFERENCE 不入库
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "归档施工日志 record.docx" --sender "<用户名>"
docker exec emily-postgres psql -U emily -d emily -c "
SELECT file_no, purpose, rag_indexed FROM files ORDER BY created_at DESC LIMIT 1;"
# 预期:purpose=RECORD, rag_indexed=False

# 5. 验证 knowledge_search 能搜到 REFERENCE
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "搜索优秀工艺相关参考" --sender "<用户名>"
# 预期:返回刚入库的 REFERENCE 文件内容
```

**通过标准**:REFERENCE 录入后 rag_indexed=True;非 REFERENCE 不入库;knowledge_search 能搜到;异步不阻塞 record_file 回复。

---

## M5-6:规则书章节 + 文档同步

### 目标

规则书加"七、文件处理原则"章节(人读权威);docs/ 同步。

### 改 `emily-data/rules/规则书.md`

在第六章后加:

```markdown
## 七、文件处理原则

1. 文件按业务意图(purpose)分 5 类:EVIDENCE(凭证)/RECORD(记录)/DESIGN(图纸)/REFERENCE(参考)/CHAT(闲聊,不入库)
2. purpose 判断依据:文件名 + 用户对话 + 上下文,不读文件内容
3. 文件关联三维度正交:版本链(parent_file_id)/ 附件链(attachment_of)/ 业务关联(source_module_id)
4. 附件链禁止嵌套:附件只能挂到主文件,不能挂到另一个附件
5. 一套文件有且仅有一个攒总主文件(attachment_of=NULL),其余为附件
6. RAG 入库策略:仅 REFERENCE 自动入通用参考库,跨项目可检索;其余不入库
7. CHAT 类(梗图/gif/表情包)不入 files 表,只留 IM URL,过期即失
8. purpose 原则载体:record_file 工具 description(LLM 按需可见);本章节为人读权威,不注入 LLM
```

### 改 docs/

1. [docs/业务模块与运转全景.md](../docs/业务模块与运转全景.md):文件管理模块补 purpose 5 类 + 附件链模型
2. [docs/接口协议与调用约定.md](../docs/接口协议与调用约定.md):新工具 schema + record_file purpose 字段
3. [docs/数据库设计.md](../docs/数据库设计.md):files 表 5 个新字段 + FilePurpose 枚举
4. [docs/代码文件目录.md](../docs/代码文件目录.md):file_rule_engine.py / migrate_file_purpose.py 新文件

### M5-6 验收检测

```bash
# 1. 规则书热重载
curl -X POST http://localhost:18080/api/v1/meta/reload-rule-book
# 或 docker exec emily-core curl ...

# 2. 验证章节存在
grep "七、文件处理原则" emily-data/rules/规则书.md

# 3. 文档同步检查
git diff --name-only | grep -E "docs/业务模块|docs/接口协议|docs/数据库设计|docs/代码文件目录"
# 预期:4 份 docs 都有改动
```

**通过标准**:规则书章节存在且热重载成功;4 份 docs 已更新。

---

## 端到端验收(全模块完成后)

```bash
# 完整生命周期测试
# 1. 用户发梗图 → 规则引擎判 CHAT → 不入库(M5-2)
# 2. 用户发施工许可证 → 规则引擎候选 EVIDENCE → record_file 确认(M5-2)
# 3. 录入 REFERENCE → 异步入 RAG(M5-5)
# 4. 把图纸挂到合格单下 → link_to_master(M5-3)
# 5. 发现 purpose 标错 → update_file_purpose(M5-4)
# 6. 图纸升版 → new_file_version(M5-4 或 M4)
# 7. knowledge_search 搜 REFERENCE → 命中(M5-5)

# 数据一致性
docker exec emily-postgres psql -U emily -d emily -c "
SELECT purpose, COUNT(*), SUM(CASE WHEN rag_indexed THEN 1 ELSE 0 END) as rag_count 
FROM files WHERE is_deleted=false GROUP BY purpose;"
# 预期:只有 REFERENCE 类 rag_indexed 有值

# 工具一致性
uv run python scripts/check_tools_consistency.py
# 预期:0 fatal
```

---

## 风险与回退

| 风险 | 影响 | 回退方案 |
|------|------|----------|
| M5-1 迁移脚本映射错误 | 中 | dry-run 预览先验证;回退:重新跑迁移脚本(幂等) |
| M5-2 规则引擎误判 CHAT | 中 | 用户显式"归档"可覆盖;update_file_purpose 事后校正 |
| M5-2 description 过长影响 token | 低 | 只影响 record_file 一条工具;超长可拆 purpose 定义到独立 prompt 片段 |
| M5-3 attachment_of FK 约束 | 中 | 主文件软删除时附件保持原状(设计文档已确认),不级联 |
| M5-5 RAG 异步入库失败 | 中 | rag_indexed=False 留兜底;定时任务重试(后续任务) |
| M5-5 embed_and_index 参数不匹配 | 中 | 参照 [embed_tool.py](../emily-core/emily_core/tools/embed_tool.py) 实际签名,必要时调整 _index_reference_file |
| 历史数据 purpose 不准 | 低 | OTHER 类 purpose_confirmed=False,后续 LLM/人工修正 |

---

## 文档同步清单(CLAUDE.md 维护约定 1)

全模块完成后必须更新:
1. [docs/业务模块与运转全景.md](../docs/业务模块与运转全景.md) — 文件管理模块 + purpose 5 类 + 附件链
2. [docs/接口协议与调用约定.md](../docs/接口协议与调用约定.md) — 新工具 schema + record_file purpose 字段
3. [docs/数据库设计.md](../docs/数据库设计.md) — files 表 5 新字段 + FilePurpose 枚举
4. [docs/代码文件目录.md](../docs/代码文件目录.md) — file_rule_engine.py / migrate_file_purpose.py
5. [docs/技术踩坑备忘录.md](../docs/技术踩坑备忘录.md) — purpose 迁移/附件链/RAG 异步入库相关踩坑(如有)
6. [需求/文件管理系统.md](../需求/文件管理系统.md) — 标注 M5 已实施状态

---

## 与 M1-M4 的衔接说明

| 模块 | M5 假设状态 | 衔接点 |
|------|------------|--------|
| M1 FileManager | ✅ 已完成 | M5-3/M5-4/M5-5 在 FileManager 上扩展方法 |
| M2 send_file | 独立,无依赖 | M5 不动 |
| M3 AttachmentDownloader | ✅ 已存在 | M5-2 在 _download_one 内调规则引擎 |
| M4 版本链/删除 | 若未实施,M5-4 承接 | M5-4 的 create_version/soft_delete/list_versions 复用 M4 设计 |
| M4 group_id 成组 | ❌ 被 M5 修订 | M5-3 的 attachment_of 替代 group_id |
