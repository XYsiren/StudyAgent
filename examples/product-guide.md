# 知衡知识助理产品说明（演示数据）

## 文档管理

支持不超过50MiB的 PDF、DOCX、TXT、Markdown、CSV、JSON、HTML 和图片。
文件上传后进入持久化异步队列，依次执行解析、按页切块、Embedding 生成与 Qdrant 索引。
同一知识库内相同文件按 SHA256 去重。解析失败的文档可以重试，已就绪文档可通过重试重新索引。

## 混合检索

系统结合 Qdrant dense 向量召回与中文分词 BM25，以 RRF 融合排名。配置 Cross-Encoder 后进行精排。
Agent 模式在词项证据不足时最多改写两次查询；完成融合后只精排一次。它不执行外部工具。
问答返回文档名、页码、片段与来源编号。无足够证据时明确说明。

## 评测

未开启 Ragas 时，系统报告文档级 Hit@5、Recall@5、MRR@5 和参考答案词项召回。
配置真实语义模型与评测模型后可以运行 Ragas 的 faithfulness、answer_relevancy、context_precision、context_recall。
20%召回提升、30%成本下降只是外部项目描述中的目标，本项目不预设这些结果。
