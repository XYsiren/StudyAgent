# 评测与验证

本文记录自动化检查、模型链路和容器验证结果，便于复现、排查问题及比较后续版本。记录日期：2026年10月15日至16日。

## 自动化检查

| 检查项 | 结果 | 覆盖内容 |
| --- | --- | --- |
| 后端测试 | 20项通过 | 账户权限、上传、任务恢复、检索、引用与历史复评 |
| 静态检查 | 通过 | Ruff |
| 前端检查 | 通过 | TypeScript类型检查与Vite构建 |
| 持续集成 | 通过 | Linux环境的后端测试与前端构建 |

持续集成记录：[GitHub Actions](https://github.com/leonbuilds-ai/rag-knowledge-assistant/actions/runs/38844819562)。

测试覆盖50MiB上传边界、SHA256去重、路径校验、过期token、租约恢复、向量清理、权限范围过滤、单文档BM25、PDF空白页、DOCX表格、统一精排、上下文预算及缓存失效。

```bash
python -m ruff check backend scripts
cd backend
python -m pytest -q
cd ../frontend
npm run build
```

## 端到端模型链路

验证组合为 **PostgreSQL + Redis + Qdrant + Ollama**，生成模型使用 `qwen3.5:9b`，Embedding使用 `qwen3-embedding:8b`。

| 验证项 | 结果 |
| --- | --- |
| 异步文档摄取 | 两份示例文档完成解析与索引 |
| 向量写入 | 4096维非零向量，片段ID与SQL记录一致 |
| 标准问答 | 返回答案、来源编号与原文片段 |
| Agent问答 | 检索、证据检查与生成链路完成 |
| 多轮上下文 | 同一会话内完成连续追问 |
| 答案缓存 | 重复请求复用答案 |
| 历史复评 | 使用已保存的问题、答案与引用 |
| 评分缓存 | 命中已保存的评分，四项结果保持一致 |

完整结果：[模型链路记录](artifacts/smoke-2026-10-15.json)、[核心流程记录](artifacts/smoke-2026-10-15-core.json)。

## 检索与Ragas样例

以下结果来自仓库内两份示例文档、一条标注案例，Ragas版本为0.3.9。该用例用于检查评测流程，效果比较应结合具体业务评测集进行。

| 检索指标 | Dense | Hybrid |
| --- | ---: | ---: |
| Hit@5 | 1.0000 | 1.0000 |
| Recall@5 | 1.0000 | 1.0000 |
| MRR@5 | 1.0000 | 1.0000 |

| Ragas指标 | 得分 |
| --- | ---: |
| Faithfulness | 0.5000 |
| Answer relevancy | 0.8742 |
| Context precision | 约1.0000 |
| Context recall | 1.0000 |

复用同一历史答案与评审配置时，评分缓存命中1条，新增评审案例为0条。

## 多模态与重排适配

| 适配器 | 验证方式 | 结果 |
| --- | --- | --- |
| 多语言CrossEncoder | 对相关/无关语句分别打分 | 0.9565 / -4.6102 |
| Qwen视觉解析 | 通过正式解析入口处理英文测试图片 | 保留500与30两个关键数值 |

测试图片为黑白英文样例，视觉检查未启用OCR。文档摄取与问答用例、独立重排适配器用例分别记录。完整输出见 [模型适配记录](artifacts/model-adapters-2026-10-16.json)。

## 容器与界面

Docker Compose配置检查及前后端镜像构建通过。后端默认包含`ml,evaluation`依赖，在离线、非root容器中通过以下检查：

- `pip check`无依赖冲突。
- Ragas、SentenceTransformers与CPU PyTorch导入成功。
- `/api/health`返回200。
- Tesseract包含中文与英文语言包。

容器记录见 [Docker验证](artifacts/docker-2026-10-16.json)。验证范围包括镜像构建与独立容器健康检查；完整Compose编排见部署文档。

浏览器检查覆盖注册、知识库创建、批量上传与文档状态显示。后续版本可继续扩展检索评测集、中文扫描文档集和负载测试。
