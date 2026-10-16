# 知衡 · RAG Knowledge Assistant

**把企业文档变成带出处、可评测的答案。**

Python / LangChain / FastAPI / PostgreSQL / Redis / Qdrant / Ragas / Vue 3 / Docker。

这是在截图功能要求基础上，参考 [KnowAgenticRAG](https://github.com/Zhongye1/KnowAgenticRAG) 的检索与 Agent 设计独立实现的项目。包含完整前后端、异步摄取、账户隔离、来源引用、历史问答复评与可复现的检索对照。参考范围、需求映射和实现边界见 [需求与来源](docs/requirements-and-provenance.md)。

## 已实现

- **知识空间**：多知识库，50MiB 文件上传，逐块限流与 SHA256 去重，异步任务进度、失败重试、重新索引、原文下载与片段预览。
- **多格式解析**：PDF、DOCX（含表格）、TXT、Markdown、CSV、JSON、HTML、PNG/JPEG/WebP；扫描 PDF 与图片可走 Tesseract OCR 或 Qwen 视觉模型，保留 PDF 页码。
- **混合检索**：Qdrant dense + 中文分词 BM25 → RRF 融合 → 可选多语言 Cross-Encoder。向量查询之前限定授权片段，避免跨账户召回。
- **引用问答**：每条证据关联文档、页码、片段和编号；支持本地 Ollama、vLLM / OpenAI 兼容接口，以及明确标注的离线摘录模式。
- **有界 Agent**：词项覆盖检查、最多两次关键词改写、候选融合后仅一次精排、检索轨迹、服务端会话上下文和输入预算。
- **质量评测**：文档级 Hit@5 / Recall@5 / MRR@5，Dense / Hybrid 对照；可选真实 Ragas 四指标。历史答案可直接加入复评，复用存档答案和证据；答案缓存与评分缓存分别避免重复调用。
- **工程闭环**：SQL 持久化任务与租约恢复、Redis 队列和缓存、PostgreSQL 用户/文档/会话/评测持久化、Docker Compose、自动测试、响应式 Vue 工作台。

项目不会预设“命中率提升20% / 成本下降30%”。这些必须由自己的语料、标注集、模型与运行记录验证。hash 向量模式只用于验证软件流程，不能代表语义检索性能。

## 在这台电脑启动

用户指定环境为 **conda `cook-rag-1`**，模型配置与 `E:\Leon\code_c8` 相同：

| 服务 | 本地配置 |
| --- | --- |
| 生成 / 视觉模型 | Ollama `qwen3.5:9b` |
| Embedding | Ollama `qwen3-embedding:8b`，4096维 |
| 模型端点 | `http://127.0.0.1:11434` |
| Qdrant | `http://127.0.0.1:6335`，独立 `rag_assistant_*` 集合 |
| PostgreSQL | 复用 cook 服务，独立数据库 `rag_knowledge_assistant` |
| PostgreSQL 本机端口 | `15432`；Windows 保留了包含5432的端口范围，使用仅本机 TCP 代理 |
| Redis | 复用6379服务，独立 DB6 |
| 后端 / 前端 | `http://127.0.0.1:8010` / `http://localhost:5173` |

本地 `.env` 已单独配置并被 Git 忽略。服务若已启动，直接打开前端即可；新用户在页面自行注册。

```powershell
cd E:\Leon\rag_search
conda activate cook-rag-1

# 三个终端分别运行（已运行时不需要重复启动）
.\scripts\dev.ps1 backend
.\scripts\dev.ps1 worker
.\scripts\dev.ps1 frontend
```

API 文档：[http://127.0.0.1:8010/docs](http://127.0.0.1:8010/docs)。检查本地基础设施：

```powershell
python -X utf8 scripts/check-infrastructure.py
```

如需在新的本地 checkout 重新导入 cook 配置，在没有 `.env` 时运行：

```powershell
python -X utf8 scripts/configure-local.py --source E:\Leon\code_c8\.env --create-database --expose-postgres --postgres-port 15432
```

该脚本只为新项目建独立数据库，复用连接凭据，不修改 cook 的文档、索引或数据库记录。其他电脑无需使用此导入脚本。

## 通用安装

Python 3.12、Node.js 20.19+；完整服务需要 PostgreSQL、Redis、Qdrant 和 Ollama。首次安装：

```powershell
git clone https://github.com/leonbuilds-ai/rag-knowledge-assistant.git
cd rag-knowledge-assistant
conda activate cook-rag-1

cd backend
python -m pip install -e ".[dev,ml,evaluation]"
cd ../frontend
npm ci
cd ..
Copy-Item .env.ollama.example .env
```

准备本地模型并启动 Ollama：

```powershell
ollama pull qwen3.5:9b
ollama pull qwen3-embedding:8b
ollama serve
```

`.env.ollama.example` 默认用 SQLite / Qdrant local，可先验证模型闭环；需要队列和 PostgreSQL 时填写对应连接。**独立 Redis worker 必须搭配 Qdrant server**，Qdrant local 的同目录文件锁只支持单进程。

完全离线的软件演示可改用 `.env.example`：无模型下载、无 API Key，但回答会显示“本地摘录模式”。具体模型切换、OCR 与容器部署见 [部署指南](docs/deployment.md)。

## 试用流程

1. 注册账号，创建“企业制度”知识库。
2. 上传 [演示员工手册](examples/company-handbook.md) 和 [产品说明](examples/product-guide.md)，等待状态变为“已就绪”。
3. 进入智能问答，选择知识库，提问“出差住宿报销上限是多少？”。检查答案编号、原文证据与检索轨迹。
4. 点击答案旁“加入评测”，填写参考答案和期望文档，运行离线复评；需要模型裁判时开启 Ragas。
5. 用相同标注集运行 Dense / Hybrid 对照。没有期望文档标注的案例不会产生伪造的命中率。

示例评测集：[evaluation-cases.json](examples/evaluation-cases.json)。导入后将 `expected_document_ids` 替换为当前账户上传后的真实文档 ID。

## 测试与验证

```powershell
python -m ruff check backend scripts
cd backend
python -m pytest -q
cd ../frontend
npm run build
```

Windows 受限环境的默认临时目录可能不可写，可运行 `scripts/dev.ps1 test`，将测试文件写到项目 `backend/data`。API 集成测试使用真实 SQLite 和本地 Qdrant，不调用收费模型。

完整本地服务的真实模型验证脚本：

```powershell
python -X utf8 scripts/smoke.py
# 会额外调用本地模型裁判，耗时取决于硬件
python -X utf8 scripts/smoke.py --with-ragas
```

验证范围与结果见 [验证记录](docs/validation.md)。不要把小型演示集的结果外推为企业场景准确率。

## 结构与文档

```text
backend/app/
  api/routes.py       # 账户、知识库、文档、问答、评测 API
  models.py           # PostgreSQL / SQLite 数据模型
  worker.py           # 持久化任务、租约与恢复
  services/
    parsing.py        # 解析 / OCR / 视觉 / 页码切块
    embeddings.py     # Ollama / OpenAI / SentenceTransformers / hash
    retrieval.py      # Qdrant / BM25 / RRF / Cross-Encoder
    answering.py      # 有界 Agent、引用、上下文与缓存
    evaluation.py     # 检索对照、历史复评、Ragas
frontend/             # Vue 3 + TypeScript 工作台
scripts/              # 启动、本机配置、依赖检查、真实模型冒烟
examples/             # 虚构演示文档与评测案例
docs/                 # 架构、部署、来源和验证记录
```

- [架构与数据流](docs/architecture.md)
- [截图需求映射与蓝本来源](docs/requirements-and-provenance.md)
- [部署与模型配置](docs/deployment.md)
- [验证记录与已知限制](docs/validation.md)

当前账户之间完全隔离，尚未提供团队共享与细粒度角色管理；初始表由 `create_all` 创建，后续结构升级需补充数据库迁移。BM25 当前在授权语料内存构建，面向大规模语料应迁移独立稀疏索引。`/chat/stream` 是完整答案落库后的 SSE 分块回放。以上边界在文档中明确说明。

## 许可证与参考

本项目独立实现，采用 [MIT](LICENSE)。参考上游的架构与检索思想，未复制其源码：上游在审阅提交中只有 README 的 MIT 标注，未找到许可证正文。完整依据见 [来源说明](docs/requirements-and-provenance.md)。模型权重与依赖库遵循各自许可证。
