# 部署与模型配置

## 本机三进程

后端在 `backend` 目录运行，自动读取项目根 `.env`；不要把含凭据的文件提交 Git。

```powershell
conda activate cook-rag-1
cd E:\Leon\rag_search
.\scripts\dev.ps1 backend   # 8010
.\scripts\dev.ps1 worker    # 配了 Redis 时必需
.\scripts\dev.ps1 frontend  # 5173
```

不配置 Redis 时，后端进程自动启动本地 SQL worker。它会扫描 queued 和过期 processing 租约，进程重启后恢复任务。配了 Redis 时运行独立 worker，Redis 提供唤醒，SQL 保存任务事实；Redis 短暂不可用仍扫描 SQL 恢复。

复用 cook 本地服务的项目数据库为 `rag_knowledge_assistant`、Redis DB6、Qdrant 前缀 `rag_assistant`。本机5432位于 Windows 排除范围，`rag-assistant-postgres-proxy` 只监听 `127.0.0.1:15432`。停止后重新启动该代理：`docker start rag-assistant-postgres-proxy`。具体配置只在被忽略的 `.env` 中。

## 模型

| 用途 | 配置 |
| --- | --- |
| 本机生成 | `RAG_LLM_PROVIDER=ollama`、`RAG_LLM_MODEL=qwen3.5:9b`、`RAG_LLM_BASE_URL=http://127.0.0.1:11434` |
| 本机嵌入 | `RAG_EMBEDDING_PROVIDER=ollama`、`RAG_EMBEDDING_MODEL=qwen3-embedding:8b`、`RAG_EMBEDDING_DIMENSION=4096`、`RAG_EMBEDDING_BASE_URL=http://127.0.0.1:11434` |
| vLLM / 兼容 API | `RAG_LLM_PROVIDER=openai`、`RAG_LLM_BASE_URL=http://127.0.0.1:8001/v1`，填写服务实际模型名及 API key；无鉴权的本地服务可用非空占位 key |
| 兼容嵌入 API | `RAG_EMBEDDING_PROVIDER=openai`，独立 `RAG_EMBEDDING_BASE_URL` / `RAG_EMBEDDING_API_KEY`；模型需支持请求的 `dimensions` 参数 |
| CPU 语义模型 | `RAG_EMBEDDING_PROVIDER=sentence-transformers`，`RAG_EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5`；自动读取实际模型维度 |
| 精排 | `RAG_RERANKER_MODEL=cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`；需安装 `.[ml]`，支持本地模型路径 |
| 视觉 | `RAG_VISION_MODEL=qwen3.5:9b` 或 Qwen-VL；复用生成模型接口，需该端点支持图像输入 |
| OCR | `RAG_OCR_ENABLED=true`；安装 Tesseract 和 `chi_sim` / `eng` 语言包；Docker 镜像已包含 |

Ollama 不需要 API key。`RAG_OLLAMA_NUM_CTX=16384`、`RAG_EMBEDDING_NUM_CTX=4096`、`RAG_EMBEDDING_BATCH_SIZE=8` 与已有 cook 配置一致。`RAG_MAX_OUTPUT_TOKENS` 控制输出，`RAG_MODEL_TIMEOUT` 控制请求时限。证据与历史按 UTF-8 字节上界保守裁剪，超长问题返回明确错误。

更换嵌入模型、维度或端点后会使用新的 Qdrant 集合；对文档点“重新索引”重建。旧模型索引不会混入新模型查询。删除文档会清理同一项目此前模型集合中的对应向量。

Ragas 要求真实生成模型和语义 Embedding。`pip install -e '.[evaluation]'` 后可在 UI 开启；本地验证使用 Ragas 0.3.9。评测模型与问答模型共用当前配置，顺序执行以控制本机显存占用。评分缓存键包括问答、上下文、reference、模型与端点；同样的历史答案复评可复用评分。

## Docker Compose

```powershell
Copy-Item .env.production.example .env.docker
# 编辑 .env.docker 中所有 replace-* 密码和模型设置
docker compose --env-file .env.docker config --quiet
docker compose --env-file .env.docker up --build -d
```

访问 `http://localhost:8080`。API 只映射本机8010；PostgreSQL、Redis、Qdrant在Compose内部网络。正式公开部署时应在入口配置 HTTPS、访问控制、备份与容量限制。

使用宿主机 Ollama 时，在 `.env.docker` 中写：

```dotenv
RAG_LLM_PROVIDER=ollama
RAG_LLM_BASE_URL=http://host.docker.internal:11434
RAG_LLM_MODEL=qwen3.5:9b
RAG_EMBEDDING_PROVIDER=ollama
RAG_EMBEDDING_BASE_URL=http://host.docker.internal:11434
RAG_EMBEDDING_MODEL=qwen3-embedding:8b
RAG_EMBEDDING_DIMENSION=4096
RAG_VISION_MODEL=qwen3.5:9b
```

Docker Desktop 的 Ollama 服务需允许容器网络访问。Linux 需要给 api/worker 设置 `extra_hosts: ["host.docker.internal:host-gateway"]` 或使用同网络模型服务。不要直接在公网暴露无鉴权的 Ollama 端口。

镜像默认安装 CPU PyTorch、`ml,evaluation` 依赖和 Tesseract。模型首次下载与镜像构建可能较大；部署环境不含模型权重。可以预先缓存模型于 `app-data/models`。Compose 数据卷保存 SQL、原文、模型与向量索引；日常重启不要删除数据卷。

## 备份与升级

同时备份 PostgreSQL、`app-data/uploads` 和 Qdrant 快照，保存相关嵌入配置。Redis 数据可恢复队列通知与缓存，SQL 持久任务仍是恢复依据。初始版本仅创建初始schema，没有 Alembic；结构升级前制定迁移并备份。Qdrant local 仅用于单进程开发，禁止由 API 与独立 worker 同时打开同一目录。

## 排查

- 任务一直 queued：配了 Redis 后是否启动 `python -m app.worker`；检查 worker 日志和 SQL 租约。
- 未配置模型仍有答案：查看“系统与模型”，extractive 是明确标注的原文摘录模式。
- 扫描 PDF / 图片失败：检查视觉模型是否支持图片，或 OCR 可执行文件和语言包。
- Qdrant local 被占用：停止多余进程，或改用 Qdrant server。
- 更换模型后查不到：重新索引文档，并确认实际 Embedding 维度。
- 评测没有 Hit@5：每个案例需标注真实 `expected_document_ids`；`null` 表示缺少标注。
- 本地网页无法访问：前端需5173、API需8010；只在受限sandbox启动的进程可能无法从宿主浏览器访问。
