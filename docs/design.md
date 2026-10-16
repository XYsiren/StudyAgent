# 设计原则与技术参考

知衡围绕“文档进入知识库，答案回到证据”构建产品体验，将多格式摄取、混合检索、多模态转录和持续评测整合为可部署的知识助手。系统强调来源可追溯、检索范围一致、任务可恢复和模型可替换，支持知识内容的持续维护与迭代。

完整数据流与关键实现见[系统架构](architecture.md)，环境准备与运行方式见[部署指南](deployment.md)。

## 设计目标

- **以证据组织答案。** 文档、片段、页码和引用在解析、检索、生成与历史记录之间保持关联，便于回查原文。
- **保持知识范围一致。** 从 SQL 候选集到向量过滤、词项召回和答案引用，统一使用当前账户可访问的内容。
- **让摄取任务可恢复。** 将任务状态持久化到 SQL，以租约和心跳协调执行，以 Redis 通知减少等待。
- **让模型能力可组合。** 独立配置生成、向量、视觉与重排模型，支持本地部署和兼容接口。
- **用评测驱动调整。** 对比检索策略，回放历史回答，记录质量指标与调用情况，为参数和模型选择提供依据。

## 能力模块

| 模块 | 主要能力 | 实现入口 |
| --- | --- | --- |
| 知识库管理 | 账户认证、知识库组织、文档生命周期、原文件下载 | [API 路由](../backend/app/api/routes.py)、[数据模型](../backend/app/models.py) |
| 文档摄取 | 50 MiB 上传限额、分块写入、SHA256 去重、异步任务、失败重试与过期恢复 | [上传限额](../backend/app/main.py)、[Worker](../backend/app/worker.py) |
| 内容解析 | PDF 页码、DOCX 段落与表格、常见文本格式、图片 OCR 与视觉转录 | [解析服务](../backend/app/services/parsing.py) |
| 向量索引 | Ollama、SentenceTransformers、OpenAI 兼容向量模型，按配置组织 Qdrant 集合 | [向量适配](../backend/app/services/embeddings.py)、[索引服务](../backend/app/services/retrieval.py) |
| 混合检索 | 中文 BM25、Dense 召回、RRF 融合、可选 CrossEncoder 精排、多知识库查询 | [检索服务](../backend/app/services/retrieval.py) |
| 检索编排 | 词项覆盖检查、有界关键词改写、候选融合后统一一次精排 | [问答服务](../backend/app/services/answering.py) |
| 可追溯对话 | 历史上下文、证据预算、引用编号校验、语料感知缓存、答案与来源持久化 | [问答服务](../backend/app/services/answering.py)、[会话 API](../backend/app/api/routes.py) |
| 质量评测 | Hit@5、Recall@5、MRR@5、dense/hybrid 对比、Ragas 四项指标、历史答案回放与判断缓存 | [评测服务](../backend/app/services/evaluation.py) |
| Web 工作台 | 知识库与文档管理、问答、引用查看、任务状态与评测记录 | [Vue 页面](../frontend/src/App.vue) |
| 部署运行 | 开发模式与 PostgreSQL、Redis、Qdrant server 组合，独立 API/Worker/前端容器 | [Compose](../docker-compose.yml)、[运行配置](../backend/app/config.py) |

## 关键设计

### 将来源作为贯穿全链路的数据

片段保留文档 ID、顺序和页码，检索结果携带来源信息，生成仅使用选定证据。对话保存当时的答案与引用，历史回放可以评估同一份回答，实时评测则检查当前语料下的行为。两种评测路径各有用途，结果保留相应来源。

### 在检索之前确定权限范围

业务层先验证知识库归属，再把授权片段作为 Qdrant 的排名前过滤条件。BM25 与引用返回复用同一范围。这种设计让权限控制与召回策略解耦，也避免召回后才过滤导致候选不足。

### 用持久化状态协调异步执行

SQL 保存文档和任务状态，Redis 只负责唤醒。Worker 通过原子租约领取任务，使用心跳保持执行权，允许过期任务被后续消费者重新处理。失败清理和用户重试形成可观察的恢复路径。

### 在召回、融合和精排之间明确分工

Dense 捕获语义关联，BM25 保留专有名词、数值和关键词匹配，RRF 根据排名合并两路证据，CrossEncoder 对最终候选进行细粒度排序。各阶段分别保留分数，便于观察策略差异。

Agent 模式以有界规则扩充证据，控制改写次数，并将精排集中到候选融合之后。该实现聚焦知识检索，不依赖开放工具执行；需要更复杂的任务规划时，可以在已有检索服务之上扩展编排层。

### 把上下文和重复调用纳入资源管理

历史消息和证据在进入模型前按窗口预算裁剪，输出使用独立长度上限。答案缓存包含历史、模型配置和授权语料指纹，文档更新后自然生成新的缓存键。历史评测复用已保存的答案，Ragas 判断缓存复用内容与评审配置一致的有效分数。

## 技术选型

| 选择 | 设计考虑 |
| --- | --- |
| FastAPI + SQLAlchemy | 将 API、业务服务与数据模型分层，支持 SQLite 开发和 PostgreSQL 部署 |
| Vue 3 + TypeScript | 将文档、对话和评测组织为统一工作台，保持清晰的 API 边界 |
| Qdrant | 负责 Dense 索引、相似度搜索与排名前过滤 |
| Redis + SQL 持久化任务 | 同时提供低等待通知与可恢复的任务状态 |
| LangChain 模型适配 | 在应用流程稳定的前提下替换本地或兼容服务模型 |
| 中文 BM25 + RRF + CrossEncoder | 组合词项、语义与细粒度匹配，保留可解释的阶段结果 |
| Ragas + 人工标注检索集 | 将答案判断和检索效果分别衡量 |
| Docker Compose | 为 API、Worker、前端及基础服务提供统一部署入口 |

## 质量评估

检索评测使用相同语料、相同问题和人工标注的 expected_document_ids，计算 Hit@5、Recall@5、MRR@5，并比较 dense 与 hybrid。基线为零或缺少标注时，相对变化保持为空。Ragas 在配置真实模型与 reference 后计算 faithfulness、answer_relevancy、context_precision、context_recall；词项覆盖指标单独呈现。

模型调用量、耗时、缓存命中率与历史回放情况用于分析运行成本。性能与质量结果应同时记录模型版本、语料、标注集和部署环境，使不同配置之间的比较有据可查。

仓库保留[核心流程记录](artifacts/smoke-2026-10-15-core.json)、[模型适配记录](artifacts/model-adapters-2026-10-16.json)和 [Docker 验证记录](artifacts/docker-2026-10-16.json)。自动化测试覆盖上传、索引、权限、引用、缓存、任务恢复和评测接口，代码见 [API 测试](../backend/tests/test_api.py)与 [服务测试](../backend/tests/test_services.py)。具体能力以对应记录中的环境和检查范围为准。

## 版本边界

当前版本优先覆盖账户独立知识库与中小语料工作流。DOCX 使用逻辑页定位；SSE 采用完成后的事件回放；数据表通过 create_all 初始化；BM25 在请求内构建。团队共享权限、数据库版本迁移、大规模稀疏索引和独立评测调度列为后续扩展方向，详细取舍见[系统架构](architecture.md#设计取舍与扩展方向)。

hash embedding 与原文摘录提供轻量演示路径。语义检索、多模态转录和模型判断评测分别依赖所选模型及运行配置。

## 技术参考与致谢

知识库组织与有界检索编排的设计参考了 [KnowAgenticRAG](https://github.com/Zhongye1/KnowAgenticRAG)，并结合 Vue、Qdrant 和本项目的服务边界完成独立实现。可进一步阅读其[检索编排](https://github.com/Zhongye1/KnowAgenticRAG/blob/e3a7256e354ed22ab445442037a8f3362d790d31/backend/src/app/agent/graph/builder.py)与[多路融合设计](https://github.com/Zhongye1/KnowAgenticRAG/blob/e3a7256e354ed22ab445442037a8f3362d790d31/backend/src/app/retrieval/service/strategies/fusion.py)。

同时感谢 FastAPI、Vue、LangChain、Qdrant、SentenceTransformers、Ragas、Ollama 及相关开源社区。本项目采用 [MIT License](../LICENSE)，第三方依赖与模型遵循各自许可。
