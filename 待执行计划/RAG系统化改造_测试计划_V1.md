# RAG 系统化改造 — 测试计划 V1

> **基于实施计划**：[RAG系统化改造_计划_V1.md](./RAG系统化改造_计划_V1.md)
> **基于需求**：[RAG系统化改造PRD.md](./RAG系统化改造PRD.md)
> **级别**：验收测试计划
> **目标**：验证实施计划 8 个模块（M1~M8）与功能需求 F1~F5 是否完全实现，重点验证「可见范围正确性（越权封堵）」这一硬验收。

---

## 一、测试目标与范围

| 验证维度 | 对应模块 | 硬验收（失败即停止） |
|---------|---------|---------------------|
| doc_id 归一（无断链） | M1 | ✓ |
| 可见范围公式 ①∪②∪③∩密级∪④ | M2、F1~F5 | ✓ |
| 检索前过滤（越权封堵） | M3、M4、F4 | ✓ |
| 多格式解析 / 结构分块 / 去重 | M5 | — |
| 混合检索 / rerank 降级 | M6 | — |
| 入库状态机 | M7 | — |
| 引用溯源 | M8 | — |

---

## 二、测试环境与前置条件

1. 独立测试库（建议 `emily` 库的干净 schema 或专用测试库），**不污染生产 `users`/`files`/`knowledge_chunks` 表**。
2. 已完成 M1~M8 代码改造并重启 `emily-core`。
3. TEI / embedding 服务可用（否则向量化全部失败，无法继续）。
4. 测试文件已按本计划第四节生成并入库，`knowledge_chunks.doc_id == files.id`。

---

## 三、测试主体数据（企业/用户/节点）

| 对象 | 关键字段 | 说明 |
|------|---------|------|
| 公司A | `company_info`，`is_admin=true`（管理单位） | 节点 N1/N2 参与企业 |
| 公司B | `company_info`，`is_admin=false` | 节点 N3 参与企业 |
| 用户 U1 | `users.company=A`，`users.level=5` → `info_level=confidential` | 可见密级 0/1/2，不见 3 |
| 用户 U2 | `users.company=B`，`users.level=1` → `info_level=public` | 只见密级 0 |
| 用户 U3（访客） | `users.company=null`，`users.level=1` → `info_level=public` | 无企业/节点 |
| 节点 N1 | `project_nodes.visibility_mode=specific`，参与公司 A | 经 `node_accessible_files` 绑文件 |
| 节点 N2 | `project_nodes.visibility_mode=all_project_files`，参与公司 A | 全项目文件可见 |
| 节点 N3 | `project_nodes.visibility_mode=specific`，参与公司 B | 企业隔离用 |

密级映射：`public=0 / internal=1 / confidential=2 / secret=3`。

### 关系表种子数据

| 表 | 需构造记录 |
|----|-----------|
| `node_participant_companies` | N1→公司A、N2→公司A、N3→公司B |
| `node_accessible_files` | N1→文件#5/#6/#7/#8、N3→文件#11/#12 |
| `session_accessible_files` | U2→文件#13（`access_type=explicit`） |
| `files` | 文件#1~#18（正确填写 `uploaded_by`/`confidentiality`/`project_id`） |

---

## 四、测试文件包（18 个文件，含完整内容）

### 4.1 文件清单总览

| # | 文件名 | 密级 | 格式 | 上传者 | 节点归属 | 验证点 |
|---|---|---|---|---|---|---|
| 1 | 公开制度A.md | 0 | md | U1 | 无 | ② 公开 |
| 2 | 公开流程B.txt | 0 | txt | U2 | 无 | ② 公开 |
| 3 | 绝密图纸C.pdf | 3 | pdf | U1 | 无 | ① 自传永可见（高密级） |
| 4 | 机密方案D.docx | 2 | docx | U2 | 无 | ① 自传（U2 可看自己密级2） |
| 5 | 节点A公开E.md | 0 | md | U1 | N1 | ③ 密级0 |
| 6 | 节点A内部F.md | 1 | md | U1 | N1 | ③ 密级1 |
| 7 | 节点A机密G.md | 2 | md | U1 | N1 | ③ U1 可见 / U2 不可见 |
| 8 | 节点A绝密H.md | 3 | md | U1 | N1 | ③ 密级上界（U1 也不可见） |
| 9 | 全项目文件I.md | 1 | md | U1 | N2 | ③ all_project_files 模式 |
| 10 | 全项目文件J.md | 2 | md | U1 | N2 | ③ all_project + 密级 |
| 11 | 节点B公开K.md | 0 | md | U2 | N3 | ③ 企业隔离（U1 不可见） |
| 12 | 节点B机密L.md | 2 | md | U2 | N3 | ③ 企业隔离 + 密级 |
| 13 | 显式授权M.md | 3 | md | U1 | 无（explicit→U2） | ④ 显式授权（U2 可见密级3） |
| 14 | 格式测试N.pdf | 0 | pdf | U1 | 无 | M5 解析 |
| 15 | 格式测试O.docx | 0 | docx | U1 | 无 | M5 解析 |
| 16 | 重复内容P.md | 0 | md | U1 | 无 | M5 去重 |
| 17 | 重复内容P_copy.md | 0 | md | U1 | 无 | M5 去重（与#16 内容 100% 相同） |
| 18 | 长文档Q.md | 0 | md | U1 | 无 | M5 结构分块 |

### 4.2 文件生成说明

- **md / txt 文件**：按下方代码块内容直接创建。
- **pdf / docx 文件**：将「目标文本」渲染为对应格式（用 `pymupdf`/`python-docx`/`reportlab` 脚本生成，或手工新建文档录入该文本）。**必须是可提取文本的文档，不得是扫描图片**。
- **锚点约定**：每个文件正文含唯一锚点 `[[RAGTEST-<序号>-<密级代码>-<归属代码>]]`，用于验证检索命中范围归属。
  - 密级代码：`PUB`=0公开 / `INT`=1内部 / `CON`=2机密 / `SEC`=3绝密
  - 归属代码：`NONE`=无节点 / `U1OWN`=U1自传 / `U2OWN`=U2自传 / `N1`/`N2`/`N3`=节点 / `EXP`=显式 / `PDF`/`DOCX`/`DUP`/`LONG`=格式/去重/长文档标记

### 4.3 各文件完整内容

#### 文件 #1 `公开制度A.md`（密级 0，U1 上传，无节点）

```markdown
# 公司考勤管理制度（公开）

[[RAGTEST-01-PUB-NONE]]

本文档为公司全员公开的考勤管理制度，适用于所有在职员工。

1. 工作日上下班时间为 9:00 - 18:00。
2. 迟到、早退按次记录，每月累计超过 3 次将影响绩效。
3. 请假需提前 1 个工作日通过 OA 系统提交审批。
```

#### 文件 #2 `公开流程B.txt`（密级 0，U2 上传，无节点）

```text
公司差旅报销流程（公开）
[[RAGTEST-02-PUB-NONE]]

1. 出差前填写出差申请单并经部门负责人审批。
2. 保留票据原件，出差结束后 5 个工作日内提交报销。
3. 住宿标准按职级执行，超支部分自理。
```

#### 文件 #3 `绝密图纸C.pdf`（密级 3，U1 上传，无节点）

目标文本：

```text
主楼结构设计图纸说明（绝密）
[[RAGTEST-03-SEC-U1OWN]]

本图纸为在建项目核心结构设计，密级为绝密。
基础采用桩筏基础，主楼抗震设防烈度为 8 度。
该文件仅上传者本人可见，未经授权严禁外传。
```

#### 文件 #4 `机密方案D.docx`（密级 2，U2 上传，无节点）

目标文本：

```text
专项施工方案（机密）
[[RAGTEST-04-CON-U2OWN]]

本方案为基坑支护专项施工方案，密级为机密。
采用地下连续墙 + 内支撑体系，开挖深度 12 米。
```

#### 文件 #5 `节点A公开E.md`（密级 0，U1 上传，N1 节点）

```markdown
# 节点A 施工进度说明（公开）

[[RAGTEST-05-PUB-N1]]

样板段主体结构已封顶，进入二次结构施工。
当前进度正常，无重大质量安全隐患。
```

#### 文件 #6 `节点A内部F.md`（密级 1，U1 上传，N1 节点）

```markdown
# 节点A 技术交底（内部）

[[RAGTEST-06-INT-N1]]

本次交底内容：防水工程施工工艺与验收标准。
防水层采用两道 SBS 卷材，搭接宽度不小于 100mm。
```

#### 文件 #7 `节点A机密G.md`（密级 2，U1 上传，N1 节点）

```markdown
# 节点A 成本测算数据（机密）

[[RAGTEST-07-CON-N1]]

样板段综合成本测算：人工费 120 万，材料费 340 万。
目标成本偏差控制在 3% 以内。
```

#### 文件 #8 `节点A绝密H.md`（密级 3，U1 上传，N1 节点）

```markdown
# 节点A 投标报价（绝密）

[[RAGTEST-08-SEC-N1]]

本节点最终投标报价为 8,860 万元，密级绝密。
报价构成与下浮空间属最高机密，仅限密级达标人员查看。
```

#### 文件 #9 `全项目文件I.md`（密级 1，U1 上传，N2 节点）

```markdown
# 项目周报（内部）

[[RAGTEST-09-INT-N2]]

本周完成：样板段砌体完成 80%，机电管线预埋 60%。
下周计划：完成样板段全部砌体并开始抹灰。
```

#### 文件 #10 `全项目文件J.md`（密级 2，U1 上传，N2 节点）

```markdown
# 项目合同摘要（机密）

[[RAGTEST-10-CON-N2]]

总承包合同金额 2.4 亿元，工期 720 日历天。
付款节点与质保金比例属机密信息。
```

#### 文件 #11 `节点B公开K.md`（密级 0，U2 上传，N3 节点）

```markdown
# 节点B 安全须知（公开）

[[RAGTEST-11-PUB-N3]]

进入施工现场必须佩戴安全帽，禁止酒后作业。
高处作业需系挂安全带。
```

#### 文件 #12 `节点B机密L.md`（密级 2，U2 上传，N3 节点）

```markdown
# 节点B 供应商名单（机密）

[[RAGTEST-12-CON-N3]]

本项目主要供应商：钢筋供应商 3 家、混凝土供应商 2 家。
供应商报价与结算价格属机密。
```

#### 文件 #13 `显式授权M.md`（密级 3，U1 上传，显式授权给 U2）

```markdown
# 专家评审会议纪要（绝密，显式授权）

[[RAGTEST-13-SEC-EXP]]

评审结论：方案总体可行，建议优化地下室防水节点。
本纪要密级绝密，已单独授权特定人员查看。
```

#### 文件 #14 `格式测试N.pdf`（密级 0，U1 上传，无节点）

目标文本：

```text
PDF 格式解析测试文档（公开）
[[RAGTEST-14-PUB-PDF]]

本文件用于验证 PDF 文档解析与向量化入库能力。
包含一个可检索的锚点词以确认检索命中。
```

#### 文件 #15 `格式测试O.docx`（密级 0，U1 上传，无节点）

目标文本：

```text
DOCX 格式解析测试文档（公开）
[[RAGTEST-15-PUB-DOCX]]

本文件用于验证 DOCX 文档解析与向量化入库能力。
包含一个可检索的锚点词以确认检索命中。
```

#### 文件 #16 `重复内容P.md`（密级 0，U1 上传，无节点）

```markdown
# 去重测试文档（公开）

[[RAGTEST-16-PUB-DUP]]

本文件内容与另一份文件完全相同，用于验证入库去重能力。
重复内容应只保留一份入库。
```

#### 文件 #17 `重复内容P_copy.md`（密级 0，U1 上传，无节点，内容与 #16 相同）

```markdown
# 去重测试文档（公开）

[[RAGTEST-16-PUB-DUP]]

本文件内容与另一份文件完全相同，用于验证入库去重能力。
重复内容应只保留一份入库。
```

#### 文件 #18 `长文档Q.md`（密级 0，U1 上传，无节点）

```markdown
# 施工现场管理手册（公开）

[[RAGTEST-18-PUB-LONG]]

## 第一章 总则
本手册用于规范施工现场各项管理工作。

## 第二章 安全管理
### 2.1 安全教育
新进场人员必须接受三级安全教育。

### 2.2 安全检查
项目部每周组织一次安全大检查。

## 第三章 质量管理
### 3.1 材料验收
进场材料需提供合格证并抽样送检。

### 3.2 隐蔽工程验收
隐蔽工程须经监理验收合格后方可覆盖。

## 第四章 进度管理
编制施工总进度计划并按月滚动更新。
```

---

## 五、测试用例矩阵

| 用例 | 验证特性 | 操作 | 预期结果 |
|------|---------|------|---------|
| TC-01 | M1 doc_id 归一 | 查 `knowledge_chunks` 与 `files` 关联 | 无孤儿（`doc_id` 均对应 `files.id`） |
| TC-02 | F3/① 自传 | U1 检索锚点 `RAGTEST-03-SEC-U1OWN` | U1 命中 #3（自传不受密级约束） |
| TC-03 | F3/② 公开 | U3（访客）检索 | 仅命中密级0 文件（#1/#2 等） |
| TC-04 | F5/③ 密级约束 | U1 vs U2 检索 `RAGTEST-07-CON-N1` | U1 命中 #7，U2 不命中 |
| TC-05 | F5/③ 密级上界 | U1 检索 `RAGTEST-08-SEC-N1` | U1 不命中 #8（secret 超限） |
| TC-06 | ③ 企业隔离 | U1 检索 `RAGTEST-11-PUB-N3` | U1 不命中 #11（公司A不参与N3） |
| TC-07 | ③ all_project_files | U1 检索 `RAGTEST-09-INT-N2` | U1 命中 #9 |
| TC-08 | ④ 显式授权 | U2 检索 `RAGTEST-13-SEC-EXP` | U2 命中 #13（explicit 覆盖密级） |
| TC-09 | F4 越权封堵 | U1 与 U2 检索同一 query | 各自命中落在各自可见集内，互不越权 |
| TC-10 | 访客落点 | U3 检索 | 可见=①∪②，③为空 |
| TC-11 | M5 多格式解析 | 入库 #14 pdf、#15 docx | 均产生 chunk，无失败 |
| TC-12 | M5 结构分块 | 入库 #18 长文档 | 按标题断点分块，offset 连续 |
| TC-13 | M5 去重 | 入库 #16/#17 | 只入 1 份，重复被 skip |
| TC-14 | M6 混合检索 | 混合检索 query | 命中不越界，doc_id ∈ 可见集 |
| TC-15 | M6 rerank 降级 | 关闭/失败 rerank | 仍返回向量结果，不报错 |
| TC-16 | M7 状态机 | 入库失败后重跑 | `failed→pending→indexed` 恢复 |
| TC-17 | M8 引用溯源 | 检索结果 | 每条 chunk 带 `cite_id`(=files.id) 与 `title`(=file_no) |

---

## 六、执行顺序与判定标准

### 执行顺序

1. **里程碑1（M1~M4）**：跑 TC-01~TC-10，此为「越权封堵」硬验收，任何一项失败即停止，不进入后续里程碑。
2. **里程碑2（M5~M6）**：跑 TC-11~TC-15。
3. **里程碑3（M7~M8）**：跑 TC-16~TC-17。

### 判定标准

- **通过**：所有用例「预期结果」逐条匹配。
- **失败**：出现以下任一情况即判定为失败——越权命中、断链孤儿、解析失败、去重失效、状态机无法恢复、结果缺 citation。

### 核心验收命令

```bash
# TC-01 无断链（应返回 0 行）
docker exec emily-postgres psql -U emily -d emily -c \
"SELECT k.doc_id FROM knowledge_chunks k LEFT JOIN files f ON k.doc_id=f.id WHERE f.id IS NULL;"

# TC-03 访客可见集（应仅密级0）
uv run python scripts/rag_visible_check.py --user-id <U3_uuid>

# TC-04 密级约束对比
uv run python scripts/rag_visible_check.py --user-id <U1_uuid> --query "成本测算"
uv run python scripts/rag_visible_check.py --user-id <U2_uuid> --query "成本测算"

# TC-09 越权封堵（U2 不应命中 U1 的机密文件）
uv run python scripts/rag_visible_check.py --user-id <U2_uuid> --query "RAGTEST-07-CON-N1"

# TC-13 去重（content_hash 相同应仅 1 条）
docker exec emily-postgres psql -U emily -d emily -c \
"SELECT content_hash, count(*) FROM knowledge_chunks WHERE content_hash != '' GROUP BY content_hash HAVING count(*)>1;"
```

---

## 七、测试交付物

| 交付物 | 说明 |
|--------|------|
| 18 个测试文件 | 内容见第四节，md/txt 直接创建，pdf/docx 渲染生成 |
| 种子数据 | 3 用户 / 2 企业 / 3 节点 / 关系表记录（见第三节） |
| 测试报告 | 逐用例记录「预期/实际/是否通过」，形成 `RAG系统化改造_测试报告_V1.md` |

---

*本测试计划用于验收 [RAG系统化改造_计划_V1.md](./RAG系统化改造_计划_V1.md) 的实现成果。*
