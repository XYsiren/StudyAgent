# 需求覆盖、验证方法与参考来源

更新于 2026-10-15。用户要求在 E:\Leon\rag_search 新建项目，达到截图中 RAG 知识检索系统的功能水准，结合 [KnowAgenticRAG](https://github.com/Zhongye1/KnowAgenticRAG) 的设计继续改进，目标仓库为 [leonbuilds-ai/rag-knowledge-assistant](https://github.com/leonbuilds-ai/rag-knowledge-assistant)，Python 环境使用 cook-rag-1。

截图是功能与目标描述。其中的时间、项目经历口吻，以及“Top-5 命中率提升 20%”“调用成本降低约 30%”不作为当前项目已发生的事实。当前实现、配置启用状态、自动化验证和真实模型质量结果分别记录。

## 截图需求逐项映射

| 截图要求 | 当前实现与代码 | 验证方法 / 范围 |
| --- | --- | --- |
| Python / LangChain / FastAPI | [pyproject.toml](../backend/pyproject.toml)、[main.py](../backend/app/main.py)、[answering.py](../backend/app/services/answering.py)；LangChain 适配本地 Ollama 与 OpenAI 兼容模型 | 在 cook-rag-1 安装依赖，启动 API，检查 /docs；模型调用需单独验证 |
| PostgreSQL / Redis / Qdrant / Vue / Docker | [db.py](../backend/app/db.py)、[worker.py](../backend/app/worker.py)、[retrieval.py](../backend/app/services/retrieval.py)、[App.vue](../frontend/src/App.vue)、[docker-compose.yml](../docker-compose.yml) | PostgreSQL、Redis、Qdrant server 为生产组合；开发可使用 SQLite + Qdrant local；前端构建与 Docker 整栈启动分别验证 |
| 多格式文档上传、解析到问答的闭环 | [上传及任务 API](../backend/app/api/routes.py)、[parsing.py](../backend/app/services/parsing.py)、[worker.py](../backend/app/worker.py) | 注册 → 建知识库 → 上传样例 → 轮询任务 → 查看片段 → 提问 → 查看引用 → 下载原文；自动化覆盖 test_upload_ingestion_deduplication_scope_and_cleanup |
| 50 MB 内文档处理 | [UploadLimitMiddleware](../backend/app/main.py)限制 multipart；上传函数按 1 MiB 分块处理，默认文件上限 50 × 1024 × 1024 字节 | test_upload_limit_stream_cleanup 验证超限拒绝和文件清理；50 MiB 是接受文件的上限，不是对所有格式的耗时承诺 |
| 异步解析和任务状态 | [IngestionJob](../backend/app/models.py)、[Worker](../backend/app/worker.py)：SQL 持久化任务、原子租约、心跳、过期恢复、Redis 唤醒通知 | test_upload_validation_failed_job_retry_and_durable_recovery 覆盖失败、重试和恢复；进程崩溃、外部服务停机与多 Worker 联调仍应独立演练 |
| 切块、Embedding、Qdrant 入库与检索 | [chunk_pages](../backend/app/services/parsing.py)、[embeddings.py](../backend/app/services/embeddings.py)、[retrieval.py](../backend/app/services/retrieval.py) | 页码和切块上限测试；Qdrant 索引/删除测试；真实向量模型必须验证实际维度与中文语义表现 |
| BM25 + Dense Embedding + RRF | [retrieve](../backend/app/services/retrieval.py)：jieba 分词、BM25Okapi、Qdrant cosine、按 chunk ID 进行 RRF 融合 | test_qdrant_scope_fusion_and_cleanup；test_single_chunk_bm25_has_positive_match 验证小语料；dense 与 hybrid 对比接口使用同一标注集 |
| Cross-Encoder 重排 | [rerank_candidates](../backend/app/services/retrieval.py)，由 RAG_RERANKER_MODEL 控制；Agent 融合完候选后只重排一次 | 可用 stub 计数验证一次精排；真实模型需安装 ml 依赖和下载权重后测量排序质量与延迟；未启用时不宣称生效 |
| PDF / Word / 图片解析与 OCR | [parsing.py](../backend/app/services/parsing.py)：PDF 正文与实际页码、DOCX 段落/表格、图片和扫描页 OCR、内嵌图片转录 | 文本、DOCX 表格、HTML、切块页码自动化测试；扫描 PDF 与中文图片需实际 Tesseract/视觉模型联调；Word 当前仅支持 DOCX，DOC 二进制格式未实现 |
| Qwen-VL / vLLM 视觉 Caption 能力 | [图像转录](../backend/app/services/parsing.py)调用配置的 Ollama 或 OpenAI 兼容视觉模型，转录正文/表格并描述图表，作为来源文本入库 | 本机配置复用 qwen3.5:9b；vLLM/Qwen-VL 兼容路径需要用真实端点验证。存在适配代码不等于所有模型协议均已测试 |
| Ragas 四项评测 | [evaluation.py](../backend/app/services/evaluation.py)：faithfulness、answer_relevancy、context_precision、context_recall | 需 evaluation 依赖、真实 LLM、语义 embedding、每案例 reference；失败显示错误。默认离线测试不产生这四项分数 |
| 历史问答与反复评测 | [Conversation / Message / EvaluationRun](../backend/app/models.py)、[会话与评测 API](../backend/app/api/routes.py)、[Vue 界面](../frontend/src/App.vue)；通过 message_id 复用当前账户历史答案及引用，Ragas 判断结果支持内容指纹缓存 | test_chat_history_citations_evaluation_and_cross_user_isolation；确认记录可重新打开、不同用户不可访问；记录 history_replayed_cases、judge_cached_cases；真实评测单独记录模型和语料版本 |
| PostgreSQL 存储用户、文档、问答和评测 | [models.py](../backend/app/models.py)包含 User、KnowledgeBase、Document、Chunk、IngestionJob、Conversation、Message、EvaluationRun | API 测试默认 SQLite；PostgreSQL 的事务、锁、恢复和备份需要实际环境联调；不是仅凭 ORM 便证明生产可用 |
| Redis 异步状态与缓存 | [worker.py](../backend/app/worker.py)通过 Redis 通知任务，SQL 保持权威状态；[answering.py](../backend/app/services/answering.py)存储 TTL 缓存 | 停 Redis 后任务仍留在 SQL 并可恢复；缓存命中返回 cached=true；生产 Redis 故障联调需单独记录 |
| 多文档和多知识库检索 | [scoped_chunks](../backend/app/api/routes.py)校验归属后联合候选，Qdrant 在排名前限制精确片段 ID | 使用两个账户和多个知识库，查询相似内容，验证他人片段不可召回；test_authentication_tokens_and_ownership 与检索隔离测试覆盖基础行为 |
| Top-5 命中提升 20% | [compare_retrieval](../backend/app/services/evaluation.py)返回 dense/hybrid 的 Hit@5、Recall@5、MRR@5 和相对变化 | 属于待验证目标。需要同一语料和人工标注；基线为零或无标注时相对变化为 null；hash 模式结果不代表语义模型性能 |
| 调用成本降低约 30% | [答案缓存](../backend/app/services/answering.py)支持历史和语料指纹失效、缓存命中零生成调用；有界改写不增加规划 LLM 调用 | 属于待验证目标。应固定问题集、重复比例、模型价格或本机资源口径，并记录生成/向量/重排/评测调用与耗时。当前不报告 30% 实测收益 |

## 在蓝本基础上的取舍和改进

参考仓库的实际技术栈与截图并不相同：它使用 React、Milvus、MinIO 和外部解析服务。本项目按用户截图选择 Vue 与 Qdrant，以独立服务层实现文档解析和模型适配，没有把上游源码整体迁移后改名。

保留的设计思路包括：知识库与片段的来源追溯、摄取任务状态、混合检索、跨查询融合、检索不足时有界补充证据，以及最终一次精排。具体改进是上传前后双层限额、单文档 BM25 退化处理、精确的排名前授权过滤、将 RRF 与重排分拆开、把不可信 sources 放入用户数据、按上下文窗口裁剪实际证据、缓存纳入历史和授权语料内容，以及把可复现指标与模型判断指标区分开。历史答案回放与判断缓存分别减少重复生成、重复打分，但收益必须测量，不能预填比例。

Agent 当前由确定性服务端规则控制，使用词项覆盖启发式和关键词改写；它没有复制上游 LangGraph/ReAct 双层工具图，也不宣称拥有通用 Agent 工具调用能力。该选择控制了调用次数，但复杂问题规划能力仍需真实问答集验证。

## 上游参考基准与许可证情况

审查时上游默认分支为 master，参考基准为提交 e3a7256e354ed22ab445442037a8f3362d790d31。以下链接固定到该提交，避免上游后续更新改变审查依据。

| 参考点 | 固定来源 |
| --- | --- |
| 项目介绍及实际部署方式 | [上游 README](https://github.com/Zhongye1/KnowAgenticRAG/blob/e3a7256e354ed22ab445442037a8f3362d790d31/README.md) |
| Python / LangGraph / 解析与存储依赖 | [backend/pyproject.toml](https://github.com/Zhongye1/KnowAgenticRAG/blob/e3a7256e354ed22ab445442037a8f3362d790d31/backend/pyproject.toml) |
| 前端实际采用 React | [frontend/package.json](https://github.com/Zhongye1/KnowAgenticRAG/blob/e3a7256e354ed22ab445442037a8f3362d790d31/frontend/package.json) |
| plan、act、grade、rewrite、generate 编排与预算 | [agent/graph/builder.py](https://github.com/Zhongye1/KnowAgenticRAG/blob/e3a7256e354ed22ab445442037a8f3362d790d31/backend/src/app/agent/graph/builder.py) |
| Dense + BM25 与多子查询融合 | [strategies/hybrid.py](https://github.com/Zhongye1/KnowAgenticRAG/blob/e3a7256e354ed22ab445442037a8f3362d790d31/backend/src/app/retrieval/service/strategies/hybrid.py)、[strategies/fusion.py](https://github.com/Zhongye1/KnowAgenticRAG/blob/e3a7256e354ed22ab445442037a8f3362d790d31/backend/src/app/retrieval/service/strategies/fusion.py) |
| 文档登记、摄取和状态 API | [ingest/api/v1/router.py](https://github.com/Zhongye1/KnowAgenticRAG/blob/e3a7256e354ed22ab445442037a8f3362d790d31/backend/src/app/ingest/api/v1/router.py) |
| 文档级检索权限范围 | [retrieval/service/scope.py](https://github.com/Zhongye1/KnowAgenticRAG/blob/e3a7256e354ed22ab445442037a8f3362d790d31/backend/src/app/retrieval/service/scope.py) |
| PixelRAG 视觉切片管线 | [ingest/service/visual_service.py](https://github.com/Zhongye1/KnowAgenticRAG/blob/e3a7256e354ed22ab445442037a8f3362d790d31/backend/src/app/ingest/service/visual_service.py) |

上游 README 写有 MIT，但该提交的递归文件树没有找到 LICENSE/COPYING 等完整许可证正文，GitHub 仓库 metadata 的 license 为 null。本项目因此只借鉴公开可观察的架构思路，独立编写实现，不复制上游源码、前端素材、技能文件或配置。当前仓库的 [LICENSE](../LICENSE)只适用于本项目作者提供的代码；第三方依赖、模型权重和服务协议仍按各自许可证和条款使用。此处如实记录审查证据，不把 README 标签等同于已经取得完整授权。

上游目录中的 AGENTS.md、技能文档和安装说明没有被当作用户指令执行。研究采用只读源码与依赖清单检查，没有运行上游的安装脚本、初始化命令或 Agent 工具。

## 如何记录可信的验证结果

先在 cook-rag-1 中运行后端自动化测试，再构建前端；结果应记录命令、时间、通过/失败和环境。当前测试名与覆盖范围见上表，本文不预填测试通过数量。离线 hash 模式验证上传、索引、过滤、引用、缓存和评测接口，不用于宣称语义理解质量。

真实模型验收应记录 qwen3.5:9b、qwen3-embedding:8b（4096 维）及实际重排模型的准确名称、语料和人工标注集；至少检查中文同义问句、无答案问题、跨文档问题、追问、中文图片、扫描 PDF、表格，以及文档删除/重建后的引用与缓存。只有实际运行的 Ragas 结果才能填入四项模型判断指标。

要主张命中率或成本提升，应保存 dense 与 hybrid 的同源对比结果、模型调用量/耗时和缓存命中率。数据量、重复问题比例、重排启用状态或模型不同，结果就不能直接横向比较。基础单元测试通过、前端成功构建、真实模型回答成功、Docker 整栈通过和达到质量目标是不同的验证层级。

现有限制及运行边界见[架构说明](architecture.md)：DOCX page=1 是逻辑区域；SSE 是完成后回放；数据库初始建表使用 create_all；没有 Alembic、团队 RBAC 和面向大规模语料的持久化 BM25 索引。这些限制应在后续版本中通过明确需求与测试逐步解决。
