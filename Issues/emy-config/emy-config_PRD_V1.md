# emy-config 配置中心 — PRD（规格）V1

> **模块标识**：emy-config（Emily 配置中心，前端页面 + 只读清单 API）
> **版本**：V1
> **日期**：2026-09-16
> **状态**：待评审（**尚未实施**，本文档为计划与规格）
> **与 emy-console 的关系**：并列的独立页面。emy-console = 运行时操作与观测；emy-config = 配置的声明面与生效面
> **决策记录**（已确认）：
> 1. 页面形态 = **独立页面，挂在 `/config`**
> 2. 写入范围 = **只读展示 + 补环境变量入口**（不引入写盘风险）
> 3. 生效方式 = **仅提示需要重启**（不做自动重启、不做热重载）

## 一、概述

**问题**：Emily 的配置分散在六个来源（`.env` / 三份 `docker-compose-*.yml` / `emily-data/config/*` / `Config` 数据类默认值 / `_config_from_env()` 映射白名单 / AstrBot 插件 schema）。用户无法在一处回答「这项配置现在是什么值、值从哪来、改了会不会生效」。

**目标**：把全部配置项汇总到一处，对每项做「代码默认值 · 宿主机 `.env` 声明 · 容器生效值」三方对照，并显式标出「写了但不生效」的部分。

**范围**：只读配置清单 API + 前端展示页 + 补齐缺失的环境变量入口。

**不范围**：写盘改配置；自动重启容器；配置热重载；密钥的查看与轮换；AstrBot 插件侧配置的前端编辑。

### 1.1 现状：配置分散在六个来源

| # | 来源 | 内容 | 证据 |
|---|---|---|---|
| 1 | `.env` | 密钥、域名、渠道参数，30+ 键 | `.env.example` |
| 2 | `docker-compose-minimal/napcat/wecom.yml` | 三份各自注入一份 env 清单，默认值各自写 `${VAR:-...}` | `docker-compose-napcat.yml:41-77` 等 |
| 3 | `emily-data/config/*` | 7 个配置文件（core_config / hook_config / mcp_servers / retrieval_channels / retrieval_strategy / scheduler_config / scripts_registry.yaml） | 见附录 B |
| 4 | `emily_core/config.py` | `Config` 数据类 **79 个字段**的硬编码默认值 | `config.py:7-302` |
| 5 | `bootstrap._config_from_env()` | env → Config 映射白名单，改造前仅 **22 条** | `bootstrap.py:55-78` |
| 6 | `data/plugins/emily_agent/_conf_schema.json` | AstrBot 插件侧配置（含重复键名兼容） | 同文件 |

> 根目录 `settings.json` 是 Claude Code 的 IDE 设置，不属 Emily 运行时配置，排除在本需求之外。

### 1.2 三个真实痛点

#### P1｜「改了不生效」的配置无法自查

- `bootstrap.init()` **不读取任何配置文件**——全程没有 `json.load`，配置只走「环境变量 → Config」。因此 `emily-data/config/core_config.json` 虽然挂进了容器（`/app/config/core_config.json`），却**根本不参与运行**（`api/server.py:41` → `bootstrap.py:124`）。
- 更隐蔽的是映射里的这行：

  ```python
  if val is None or data.get(cfg_key):
      continue
  ```

  （`bootstrap.py:93`）——文件里已有值时**静默忽略环境变量**，用户改 `.env` 没反应且日志中无任何提示。
- 同类问题还有 `scheduler_config.json`：全仓无代码读取，真实作业行来自数据库表 `scheduler_jobs`（见 `docs/Manual/技术踩坑备忘录.md` §1.11）。

#### P2｜「根本改不了」的配置

`llm_agent_loop_model` / `llm_router_model` / `llm_guardian_model` / `expert_model` 等字段**没有任何环境变量入口**，只能改代码。

**本次实际触发案例**：`.env` 设 `EMILY_LLM_MODEL=deepseek-chat`，但日志中持续出现 `deepseek-v4-pro` 的调用记录。根因即 `llm_agent_loop_model`（agent loop 工具调用模型）硬编码默认 `deepseek-v4-pro`（`config.py:67`），且无 env 入口——用户无法通过 `.env` 覆盖它，也无从得知还有这一层模型配置。

#### P3｜改配置成本高、无校验、无审计

语义相同的项在 4 处可写（`.env` / compose / `core_config.json` / `config.py`），默认值不完全一致；改动需 SSH 改文件 + `docker compose up -d`；无类型校验，改错只能等容器起不来才发现。

### 1.3 本机实测证据（2026-09-16，emily-core 容器）

用一次性探针脚本（已在验证后**全部退回**）实测，运行时事实如下：

| 指标 | 实测值 |
|---|---|
| `Config` 字段总数 | 79 |
| 有环境变量入口的字段（改造前 → 补入口后） | 22 → 35 |
| 容器内环境变量总数 | 30 |
| 宿主机 `.env` 可读键数 | 14 |
| `core_config.json` 与生效值冲突项 | 5 |
| `core_config.json` 非 Config 字段 | 3 |
| 容器内存在但未映射到 Config 的 `EMILY_*` | 4 |

**实测命中告警（warn 类）**：

1. `EMILY_CAPABILITY_CALL_TIMEOUT_SECONDS` 已声明但未注入容器 —— `.env` 写了值，compose 未把该变量列入 `environment` 段，改了不生效。
2. `core_config.json` 有 5 项与生效值冲突（该文件不参与运行）：

   | 键 | 文件值 | 实际生效值 |
   |---|---|---|
   | `bot_name` | `'Emily'` | `'Emy'` |
   | `takeover_mode` | `'collaborate'` | `'monitor'` |
   | `log_dir` | `'/app/logs'` | `'logs/'` |
   | `llm_model` | `'deepseek-v4-flash'` | `'deepseek-chat'` |
   | `kb_enabled` | `False` | `True` |

3. `core_config.json` 不被任何运行时代码读取。
4. `scheduler_config.json` 不被任何运行时代码读取。

**实测 info 类告警**：

- `core_config.json` 有 3 个非 Config 字段（`pipeline_mode` / `maxkb_search_mode` / `email_poll_interval`）。
- 10 个变量「容器有值但 `.env` 未声明」，由 compose 写死：`EMILY_DATABASE_URL` / `EMILY_STORAGE_ROOT` / `EMILY_HOOK_CONFIG_PATH` / `EMILY_KB_ENABLED` / `EMILY_EMBEDDING_API_URL` / `EMILY_EMBEDDING_API_KEY` / `EMILY_EMBEDDING_MODE` / `EMILY_VLM_API_URL` / `EMILY_VLM_API_KEY` / `EMILY_VLM_MODEL`。
- 4 个容器内 `EMILY_*` 未映射到 Config 字段：`EMILY_API_TOKEN` / `EMILY_EMAIL_IDKEY` / `EMILY_EMAIL_PASSWORD` / `EMILY_WXMP_DOMAIN`（前者的邮箱类两项实际由 `bootstrap.py:701-702` 直接读取，属"非 Config 字段但确有消费方"）。

> 该实测同时验证了本方案的可行性：探针以只读方式拿到了上述全部结论，页面渲染无控制台报错，交互（分组切换、字段展开、草稿导出）可用。

## 二、用户故事与验收标准

### US-01 配置全局可观测（三方对照）

**AC-US-01.1** 每个配置字段展示四列：字段名（含类型与字段说明）、环境变量名、代码默认值、`.env` 声明值、容器生效值。
**AC-US-01.2** 每项标注「来源」：`环境变量` / `代码默认` / `声明未注入`。
**AC-US-01.3** 字段说明取自 `config.py` 源码解析，**不得在清单里手抄副本**（防漂移）。
**AC-US-01.4** 无环境变量入口的字段显式标注，不得留空让人误以为"未配置"。

### US-02 幽灵字段可配（补环境变量入口）

**AC-US-02.1** 原 P2 类字段全部具备环境变量入口，至少覆盖：`llm_router_model` / `llm_guardian_model` / `llm_agent_loop_model` / `expert_model` / `llm_temperature` / `llm_max_tokens` / `llm_agent_loop_max_tokens` / `llm_context_window_override` / `takeover_mode` / `bot_name` / `log_level` / `scheduler_enabled` / `session_archive_enabled`（清单见附录 A）。
**AC-US-02.2** env → Config 映射以**模块级常量单一来源**存放，清单页反查同一常量，不得两处维护。
**AC-US-02.3** 回归：`.env` 中设 `EMILY_LLM_AGENT_LOOP_MODEL` 后，agent loop 实际调用该模型。
**AC-US-02.4** 未设新入口时行为与改造前完全一致（默认值不变）。

### US-03 差异可诊断（告警）

**AC-US-03.1** 覆盖五类差异，分级 warn / info：

| 类型 | 判定 | 级别 |
|---|---|---|
| `dotenv-uninjected` | `.env` 声明了内核该读的变量，但容器环境里没有 | warn |
| `file-conflict` | 配置文件键值与生效值不一致（且该文件不被读取） | warn |
| `file-not-loaded` | 已登记的配置文件不被任何运行时代码读取 | warn |
| `env-without-dotenv` | 容器有值但 `.env` 未声明（compose / 镜像提供） | info |
| `env-unmapped` | 容器内 `EMILY_*` 未映射到 Config 字段 | info |

**AC-US-03.2** 每条告警给出可执行的下一步（改哪个文件、是否需要重建容器），不得只报差异不给处置。
**AC-US-03.3** 口径准确：`env-unmapped` 不得断言"没人读"——部分变量由代码其它位置直接 `os.environ` 读取，文案须说明需先确认消费方。

### US-04 配置填写人机交互

**AC-US-04.1** 点字段行可展开，填写"目标值"；字段说明与当前生效值同屏可见。
**AC-US-04.2** 填写后生成 `ENV=value` 行预览，可一键汇总导出全部草稿。
**AC-US-04.3** 导出的片段含显式提示：需重建容器、`docker restart` 不重读 `.env`。
**AC-US-04.4** 草稿仅存在于前端内存，刷新即失效（**不落盘、不写任何配置文件**）。

### US-05 只读不改盘（安全边界）

**AC-US-05.1** 本模块不提供任何写配置文件的接口。
**AC-US-05.2** 查看配置文件内容的接口按文件名白名单限定（仅附录 B 登记的 7 个文件），拒绝路径穿越。
**AC-US-05.3** 配置页面与 emy-console 同模式挂载，仅监听内网 + 经 nginx 反代。

### US-06 密钥不泄密

**AC-US-06.1** 密钥类字段（含 `api_key` / `password` / `secret` / `token` / `idkey`）任何接口都不得返回明文。
**AC-US-06.2** 页面展示为「已配置（N 字符）」形式，仅用于判断是否配置。
**AC-US-06.3** 宿主机 `.env` 即使被读取，也只输出键名、是否注入、与非敏感值；敏感键只输出"是否已配置 + 长度"。

### US-07 配置文件的「生效 / 不生效」可见

**AC-US-07.1** 列出容器内 `/app/config` 下全部已登记文件，标注存在性、大小、修改时间。
**AC-US-07.2** 每个文件标注**是否被运行时代码读取**，被读取的附加载位置（`文件:行号`）证据。
**AC-US-07.3** 不被读取的文件显式提示"改它无效"，并给出应改的位置。
**AC-US-07.4** 宿主机侧声明（`.env` / compose）单列，明确"容器内不可读"的边界。

### US-08 与 emy-console 分工清晰

**AC-US-08.1** 本页面不承载运行时操作（脚本执行、资源清单、日志聚合等仍归 emy-console）。
**AC-US-08.2** 两页互链，emy-config 顶部提供 `emy-console ↗` 入口。
**AC-US-08.3** 运行期可改项（如出站静默）在页面上注明"请走 emy-console"，不重复实现。

## 三、术语

| 术语 | 定义 |
|------|------|
| 声明值 | 宿主机 `.env` 里写的值。需经 compose `environment` 段注入才能到容器 |
| 生效值 | 容器内 `Config` 实例上的实际取值，即运行时真正使用的值 |
| 代码默认值 | `config.py` 中 `Config` 字段的默认值；无 env 入口时即为生效值 |
| 环境变量入口 | 某字段是否在 `ENV_CONFIG_MAP` 中被映射。无入口 = 只能改代码 |
| 幽灵字段 | 有默认值、有运行语义，但无环境变量入口的字段（P2 类） |
| 重建 vs 重启 | 环境变量在容器**创建**时固化，改 `.env` 须 `docker compose up -d`（重建）；`docker restart` 不重读 `.env` |
| 不生效文件 | 挂在容器里、但无任何运行时代码读取的配置文件 |

## 四、约束（约束型技术决策）

**C-01 环境变量在容器创建时固化。** 改 `.env` 后必须 `docker compose up -d <service>` 重建容器；`docker restart` 无效。所有生效提示必须按此口径表述，不得含糊成"重启即可"。

**C-02 代码为只读挂载，改 `config.py` 默认值只需 `docker restart`。** 与 C-01 是两种不同操作，页面须分别提示。

**C-03 声明值需宿主机 `.env` 可见。** 容器内默认读不到宿主 `.env`。方案为在 emily-core 服务增加只读挂载 `./.env:/app/host/.env:ro`（三份 compose 同步）。**该挂载不参与容器启动**，容器环境仍由 `environment` 段决定。

**C-04 env → Config 映射单一来源。** 现 `env_map` 是 `_config_from_env()` 内的局部变量，清单页无法复用。须提升为模块级常量 `ENV_CONFIG_MAP`（连同 `ENV_BOOL_FIELDS` / `ENV_INT_FIELDS` / `ENV_FLOAT_FIELDS` / `ENV_LIST_FIELDS`），清单页反查同一常量。

**C-05 字段说明不重复维护。** `Config` 的字段级 docstring 已包含完整语义，清单页通过在运行时解析 `config.py` 源码获取，避免第二份副本。

**C-06 类型转换必须显式。** 环境变量是字符串，`bool("false")` 为真。新增入口须同步登记到对应的类型字段集；`llm_temperature` 需新增 float 转换分支（原实现仅有 bool/int/list）。

**C-07 只读边界。** 页面不写盘；配置内容接口按白名单限名。原因是本方案的价值在"可观测"，写入带来的误改生产风险与之不成比例。

**C-08 密钥一律掩码。** 清单 API 的返回体中不得出现任何密钥明文，包括 `.env` 内容接口不对外开放。

**C-09 访问控制沿用 emy-console 模式。** 静态挂载 `/config`，路由前缀 `/api/v1/config`，二者加入 `AuthMiddleware` 白名单。

**C-10 页面骨架独立。** 与 emy-console 共用配色与视觉语言（`#1a1a2e` / `#16213e` / `#00d4ff` 等），但不共享 DOM 骨架与 JS（两页信息架构差异大，强行复用会互相牵制）。

## 五、功能设计

### 5.1 页面形态与信息架构

- 路径：`/config`（静态文件挂载，`html=True`）
- 布局：顶栏（品牌 + 状态徽标 + 互链）+ 左导航 + 右主区
- 左导航两项固定区 + 配置分组区：

  | 导航项 | 内容 |
  |---|---|
  | 总览 | 统计卡、告警摘要、`.env` 可见性、不生效文件、生效方式说明、运行时信息 |
  | 差异告警 | 按 warn / info 分组的完整告警列表 |
  | 配置文件 | 容器内 7 个文件卡片（含加载状态）+ 宿主机侧声明 |
  | 环境变量 | `.env` 声明总览表 + 容器内未映射变量表 |
  | 配置分组 ×11 | 字段三方对照表（见 5.2） |

- 配置分组（按字段名前缀推断，重要字段显式覆盖）：
  LLM 模型与采样 / Agent Loop 与编排 / 会话与上下文 / 知识库与 RAG / VLM 视觉 / 数据库与文件存储 / 日志与归档与记忆 / 邮箱渠道 / 计划任务 / 权限与准入 / 其他（仅代码默认值）

### 5.2 三方对照字段模型

每行字段的返回结构：

| 字段 | 含义 |
|---|---|
| `field` / `type` / `note` | 字段名、类型、来自源码解析的字段说明 |
| `env` / `has_env` | 对应环境变量名；无入口时为 `""` / `false` |
| `sensitive` | 是否密钥类（决定是否掩码） |
| `default` / `declared` / `effective` | 三个值，统一形如 `{present, display, length}` |
| `source` | `env` / `dotenv-uninjected` / `default` |
| `restart_required` | 是否为环境变量驱动（改动需重建容器） |

### 5.3 告警规则

见 AC-US-03.1 的五类。判定要点：

- 差异比对前做归一化（`"true"`/`True`、`"1"`/`1` 视为同值），避免误报。
- `dotenv-uninjected` 只对 `ENV_CONFIG_MAP` 中的键判定——`.env` 里仅供 nginx / 任务脚本使用的键（如 `EMILY_DOMAIN` / `EMILY_SSL_CERT`）不属本容器配置，不得报警。
- 不被读取的文件：仅在文件实际存在时才报警（文件已删则无意义）。

### 5.4 配置文件清单与加载状态

`core_config.json` 与 `scheduler_config.json` 标记为「不被读取」；其余 5 个标注加载位置：

| 文件 | 加载位置 |
|---|---|
| `hook_config.json` | `emily_core/__init__.py:920 _load_hook_config()` |
| `mcp_servers.json` | `emily_core/mcp/config.py:79-92` |
| `retrieval_channels.json` | `emily_core/retrieval/channel_registry.py:20-22` |
| `retrieval_strategy.json` | `emily_core/retrieval/strategy.py:18-20` |
| `scripts_registry.yaml` | `emily_core/scripts/registry.py:121-126` |

### 5.5 补环境变量入口清单

13 项，见附录 A。全部为「新增入口、不改默认值」，未设变量时行为与改造前一致（AC-US-02.4）。

### 5.6 接口契约

统一返回 `{code, message, data}`（与 emy-console 一致）。

**`GET /api/v1/config/inventory`** —— 配置总清单。返回：

```
runtime          配置来源、.env 路径与可读性、字段/变量计数、Python 版本
sections         分组清单（key / title / count）
fields           全部字段三方对照（见 5.2）
files            容器内配置文件状态（存在性 / 大小 / 修改时间 / 是否被读取 / 加载位置）
host_files       宿主机侧声明文件（.env、docker-compose-*.yml）
unmapped_env     容器内未映射到 Config 的 EMILY_*
dotenv_declared  .env 声明总览（键名 / 是否注入容器 / 是否映射到 Config）
findings         差异告警列表（level / kind / title / detail）
restart_hint     三类改动的生效方式说明
```

**`GET /api/v1/config/file?name=`** —— 查看单个配置文件内容（只读）。文件名白名单限定；超长截断；宿主机 `.env` 不在白名单内。

### 5.7 前端交互

- 字段表列：配置项 · 环境变量 · 代码默认值 · `.env` 声明 · 容器生效值 · 来源
- 点击（或键盘 Enter / Space）字段行展开详情：字段说明、目标值输入框、当前生效值、`ENV=value` 预览
- 底部草稿条：显示待导出项数，支持「生成 `.env` 片段」与「清空草稿」
- 弹窗展示导出片段，附重建容器命令与 C-01 提示；提供复制按钮（含非安全上下文的降级路径）
- 顶部筛选框：按字段名 / 环境变量名 / 字段说明过滤当前视图
- 顶部状态徽标：`.env 可读` / `告警 N`
- 可访问性：导航项与字段行均为可聚焦元素（`button` / `role=button` + `tabindex`），键盘可达

## 六、非功能需求

| 项 | 要求 |
|---|---|
| 性能 | 清单接口为纯内存 + 少量文件读取，P95 < 200ms；单文件内容上限 200k 字符 |
| 容错 | `config.py` 解析失败、`.env` 解析失败、配置文件 JSON 损坏均不得阻断页面，降级展示并提示 |
| 兼容 | `.env` 未挂载时页面仍可用，仅「声明值」列与相关告警不可用，并在总览给出补齐挂载的指引 |
| 安全 | 见 C-07 / C-08 / C-09；接口不返回明文密钥 |
| 观测 | 清单返回 `generated_at`，页面展示生成时间，避免误信过期数据 |

## 七、非目标

1. 不做配置的写盘、校验后落盘、版本回滚。
2. 不做保存后自动重启容器。
3. 不做配置热重载（需内核侧配套改造，另立需求）。
4. 不接管 AstrBot 插件侧配置（`_conf_schema.json`）。
5. 不替代 `.env` / compose 作为配置的权威来源，只做汇总与比对。
6. 不解决 P1 的根因（`core_config.json` 不被读取、`scheduler_config.json` 不被读取）——本需求只**暴露**它们并给出处置指引，是否清理/接线由后续独立需求决定。

## 八、验收与度量

**验收方式**

1. 页面可访问：`/config` 返回 200，浏览器控制台无报错。
2. 清单完整性：`fields` 条数 == `Config` 字段总数（当前 79），无遗漏。
3. 三方对照正确性：抽查 `llm_model`，`declare` = `.env` 值、`effective` = 容器值、`source` = `env`。
4. 幽灵字段已补齐：`llm_agent_loop_model` 的 `env` 为 `EMILY_LLM_AGENT_LOOP_MODEL` 且 `has_env` 为真。
5. 回归 AC-US-02.3：设 `EMILY_LLM_AGENT_LOOP_MODEL` 后，`llm_trace.jsonl` 中 agent loop 调用记录的 `model` 为新值。
6. 告警准确性：附录 C 的 5 类告警在目标环境可复现。
7. 密钥不泄密：全量响应体 grep 已知密钥值，零命中。
8. 只读性：全站无写配置的接口；操作前后 `git status` 对配置文件无变化。

**度量**

- 从"发现问题"到"知道该改哪个文件"的步骤数：目标从 ≥3（翻代码 / 翻文档 / 试错）降到 1。
- P1 类问题（改了不生效）的排查耗时显著下降。

## 九、风险与开放问题

| # | 风险 / 问题 | 影响 | 处置建议 |
|---|---|---|---|
| R1 | 挂载宿主 `.env` 到 emily-core 容器，容器内出现新的明文密钥副本 | 安全面扩大 | 只读挂载；接口层严格掩码；容器已有 `docker.sock`（等价宿主 root），不构成新增权限级别。若仍不接受，可退化为"仅展示容器生效值 + 代码默认值"两方对照 |
| R2 | 新增 13 个环境变量入口属于行为面改动 | 若 `ENV_BOOL/INT/FLOAT` 类型集登记漏项，会出现"设了但类型不对" | 每个入口必须在类型集与清单页双处可验证；用 US-02 回归用例覆盖 |
| R3 | `config.py` 源码解析依赖格式约定（缩进 4 空格 + 紧随其后的 docstring） | 格式变动会导致字段说明丢失 | 解析失败即降级（说明列留空），不阻断；在 `config.py` 顶部注明该约定 |
| R4 | 「是否被运行时代码读取」是静态结论，硬编码在文件清单里 | 与实现漂移后页面会误报 | 每条附 `文件:行号` 证据；后续可选：改为启动期探针/单测守护 |
| R5 | 三份 compose 需同步改挂载 | 漏改导致某些部署模式下"声明值"列不可用 | 页面在 `.env` 不可读时给出明确指引（AC 兼容性项） |
| Q1 | 是否需要把「配置健康度」暴露为健康检查的一部分（如 `/health` 带 warnings 计数）？ | 影响可观测集成方式 | 开放式，待评审 |
| Q2 | `core_config.json` / `scheduler_config.json` 这两个不生效文件：清理还是接线？ | 关系到 P1 根因 | 独立需求评估，本需求只暴露 |
| Q3 | 是否需要"导出当前生效配置快照"（便于排障与对比）？ | 影响接口范围 | 开放式，待评审 |

## 附录 A　环境变量入口新增清单（13 项）

| 环境变量 | Config 字段 | 类型 | 默认值 |
|---|---|---|---|
| `EMILY_LLM_ROUTER_MODEL` | `llm_router_model` | str | `deepseek-v4-flash` |
| `EMILY_LLM_GUARDIAN_MODEL` | `llm_guardian_model` | str | `deepseek-v4-flash` |
| `EMILY_LLM_AGENT_LOOP_MODEL` | `llm_agent_loop_model` | str | `deepseek-v4-pro` |
| `EMILY_EXPERT_MODEL` | `expert_model` | str | `deepseek-chat` |
| `EMILY_LLM_TEMPERATURE` | `llm_temperature` | float | `0.1` |
| `EMILY_LLM_MAX_TOKENS` | `llm_max_tokens` | int | `1024` |
| `EMILY_LLM_AGENT_LOOP_MAX_TOKENS` | `llm_agent_loop_max_tokens` | int | `8192` |
| `EMILY_LLM_CONTEXT_WINDOW_OVERRIDE` | `llm_context_window_override` | int | `0` |
| `EMILY_TAKEOVER_MODE` | `takeover_mode` | str | `monitor` |
| `EMILY_BOT_NAME` | `bot_name` | str | `Emy` |
| `EMILY_LOG_LEVEL` | `log_level` | str | `INFO` |
| `EMILY_SCHEDULER_ENABLED` | `scheduler_enabled` | bool | `True` |
| `EMILY_SESSION_ARCHIVE_ENABLED` | `session_archive_enabled` | bool | `True` |

> 补齐后入口总数 22 → 35；仍有 44 个字段无环境变量入口（结构性/低频项），页面归入「其他（仅代码默认值）」显式标注。

## 附录 B　配置文件加载状态（实测）

| 文件 | 被运行时读取 | 加载位置 / 说明 |
|---|---|---|
| `hook_config.json` | ✅ | `emily_core/__init__.py:920 _load_hook_config()` |
| `mcp_servers.json` | ✅ | `emily_core/mcp/config.py:79-92` |
| `retrieval_channels.json` | ✅ | `emily_core/retrieval/channel_registry.py:20-22` |
| `retrieval_strategy.json` | ✅ | `emily_core/retrieval/strategy.py:18-20` |
| `scripts_registry.yaml` | ✅ | `emily_core/scripts/registry.py:121-126` |
| `core_config.json` | ❌ | `bootstrap.init()` 只走「环境变量 → Config」，不读取本文件（`bootstrap.py:124`） |
| `scheduler_config.json` | ❌ | 全仓无代码读取；真实作业行来自数据库表 `scheduler_jobs` |

宿主机侧声明（容器内不可读）：`.env`、`docker-compose-*.yml`。

## 附录 C　实测告警样例（本机真实数据，2026-09-16）

```
[warn] dotenv-uninjected  EMILY_CAPABILITY_CALL_TIMEOUT_SECONDS 已声明但未注入容器
[warn] file-conflict      core_config.json 有 5 项与生效值冲突
[warn] file-not-loaded    core_config.json 不被任何运行时代码读取
[warn] file-not-loaded    scheduler_config.json 不被任何运行时代码读取
[info] file-invalid-key   core_config.json 有 3 个非 Config 字段
[info] env-without-dotenv 10 个变量由 compose / 镜像提供
[info] env-unmapped       4 个环境变量未映射到 Config 字段
```

## 附录 D　交接给计划阶段的问题

1. 改动文件清单与顺序（`bootstrap.py` → 新路由 → `server.py` / `auth.py` → 静态资源 → 三份 compose）。
2. 三份 compose 的 `.env` 挂载是否需要加开关（例如通过 profile 控制），还是默认全部挂载。
3. 静态资源目录命名（`static/config/`）与 `StaticFiles` 挂载顺序是否需与 `/console` 保持一致的写法。
4. 字段分组前缀规则的表是否需要单测守护（新增 `Config` 字段时能被正确归类）。
5. 是否需要把清单接口的响应固化为测试夹具，以便回归比对（防字段增删后清单悄悄漏项）。
