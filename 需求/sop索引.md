# sop应该有更深入系统化的管控
## 具体描述
1. 应有一个sop manager负责管理sop，，对sop进行分类。以轻量状态进入session。并告知session-agent有sop manager的存在。
2. sop manager应该同时满足两个需求，其一是AI agent友好的工具，session-agent通过这个工具逐层获取到匹配的sop。其二是方便开发者命令行手动调用观察运转以作为调试参考。
3. sop manager应具备的功能：
- a.扫描感知本文件夹下所有可用的sop，对sop进行分类，维护sop树状目录；
- b.根据自然语言描述与sop规则手册，创建新的sop（通过LLM实现）
- c.根据需求（联动系统升级需求、自进化要求、开发者要求等）更新具体sop。（通过LLM实现）
- d.面向Ai工具，根据目的检索对应的工具，供调用选择（通过rag与llm实现）。备注：根据匹配度提供，需要匹配度评判打分机制）
## 目的
1. 使SOP的管控更系统
2. 进入session的prompt更小。（session-agent仅需知道emily的业务是通过sop流实现的，且提供了sop manager用来检索并提供对应的sop。）