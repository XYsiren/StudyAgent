# 验证记录

验证于2026年10月15日至16日在用户电脑完成。Python解释器来自conda `cook-rag-1`；前后端源码位于 `E:\Leon\rag_search`。

## 自动测试

后端20项测试通过，ruff静态检查通过；Vue TypeScript类型检查及Vite生产构建通过。

测试覆盖账户权限与过期token、50MiB上传边界、文件路径、重复上传、任务失败重试与过期租约恢复、文档删除向量清理、Qdrant查询前权限过滤、单文档BM25、PDF空白页、DOCX表格、一次CrossEncoder重排、上下文预算、历史复评所有权和答案缓存失效。

```powershell
python -m ruff check backend scripts
python -m pytest backend/tests -q --basetemp=./.test-tmp/validation
cd frontend
npm run build
```

## 真实模型与完整服务

完整记录：[smoke-2026-10-15.json](artifacts/smoke-2026-10-15.json)。语料仅为仓库内两份虚构演示文档，检索对照与Ragas仅使用一条标注案例。

| 项目 | 结果 |
| --- | --- |
| PostgreSQL / Redis / Qdrant / Ollama | 真实连接通过 |
| 生成模型 | `qwen3.5:9b` |
| 嵌入模型 | `qwen3-embedding:8b` |
| Qdrant向量 | 实际4096维、非零，片段ID与SQL一致 |
| 文档入库 | 两份文档完成异步解析与索引 |
| 标准/Agent回答 | 正确引用住宿500元；返回原文证据 |
| 多轮追问 | 同一会话能回答报销30天提交期限 |
| 答案缓存 | 复用相同答案，不新增生成调用 |
| 历史复评 | 复用已保存的答案与证据，结果一致 |
| Ragas评分缓存 | 命中1条，新增评审案例0条，四指标一致 |
| Dense / Hybrid Hit@5 | 均为1.0，相对变化0% |

Ragas 0.3.9、本机Qwen裁判的单案例真实评分：

| 指标 | 得分 |
| --- | ---: |
| faithfulness | 0.5000 |
| answer_relevancy | 0.8742 |
| context_precision | 约1.0000 |
| context_recall | 1.0000 |

忠实度0.5原样保留；它说明本地模型裁判并未给出满分，不能解释为本系统整体准确率。四指标是自动裁判的小样本结果，仍需更多人工标注、错误分析和多次重复实验。没有证据支持普遍20%命中提升或30%成本下降。

首次Ragas测试发现本机Qwen将schema当作输出；修复为独立JSON实例提示、Ollama JSON模式与顺序评审后，四指标与缓存回放均真实完成。没有以默认值替代失败评分。

## 浏览器

真实浏览器验证了注册、创建知识库、演示文档批量上传与状态显示。工作台可显示知识库、文档、引用证据、检索轨迹与评测信息。页面不预填虚构运行指标，空账户显示真实空状态。

## 部署与范围

本机完整应用使用PostgreSQL/Redis/Qdrant容器和宿主机Python/Vue/Ollama。完整Compose定义另提供干净部署路径。模型权重、实际API key、原文上传、账号token与本机 `.env` 均未提交到Git。

这不是企业负载、容灾、渗透或大规模OCR评测。CrossEncoder、视觉适配器的独立验证结果如存在则见 `artifacts/model-adapters-2026-10-16.json`，不将配置开关等同于能力质量达标。初始schema使用create_all；大型稀疏索引、团队RBAC、增量数据库迁移仍是后续范围。
