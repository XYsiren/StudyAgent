# 个人学习与考试复习助手开发文档

## 1. 文档信息

- 产品模式：个人学习与考试复习助手
- 适用版本：当前 `FastAPI + Vue 3 + PostgreSQL/SQLite + Redis + Qdrant` 项目
- 文档状态：开发设计稿
- 目标读者：产品经理、前端、后端、算法/模型和测试人员

## 2. 产品定位

本模式不是“上传 PDF 后聊天”，而是面向有明确考试范围和截止日期的学生，将个人资料转化为可追溯的学习任务、练习和复习反馈。

核心闭环：

```text
导入课程资料
  -> 按章节和知识点组织
  -> 基于原文进行引用式问答
  -> 生成练习题并记录作答
  -> 识别薄弱知识点和错题原因
  -> 按间隔复习安排下一次任务
  -> 用学习数据调整计划
```

首个目标用户建议限定为“大学生专业课期末复习”或“有固定大纲的职业证书备考者”，不在第一版同时覆盖所有学生、所有考试类型。

### 2.1 目标用户

| 用户 | 主要任务 | 核心痛点 |
| --- | --- | --- |
| 备考学生 | 整理讲义、理解概念、刷题、复习错题 | 资料分散，无法判断是否真正掌握 |
| 学习小组负责人 | 共享资料、跟踪章节进度 | 成员进度和薄弱点不可见 |
| 助教/教师（后续） | 维护课程资料、检查题目质量 | 题目生成缺少依据，难以回溯来源 |

### 2.2 非目标

- 不承诺替代教师、教材或专业考试辅导。
- 不在没有资料依据时生成确定性结论。
- 不把模型评分当作正式成绩或能力认证。
- 第一阶段不实现开放网络搜索、自动提交考试答案和高风险学科决策。

## 3. 现有能力复用与新增范围

### 3.1 直接复用

| 现有能力 | 在学习产品中的用途 |
| --- | --- |
| KnowledgeBase | 作为课程工作区的底层知识容器 |
| Document / Chunk / DocumentRevision | 存储讲义、教材、试卷、笔记、PPT/PPTX 及页码/幻灯片来源 |
| PostgreSQL/SQLite | 保存课程、题目、作答和进度 |
| Redis + Worker | 执行文档解析、批量出题、复习任务通知 |
| Qdrant + BM25 + RRF | 进行语义和关键词混合检索 |
| 引用问答与证据校验 | 保证解释和答案能够回到原文 |
| 账户、权限、会话和审计 | 保护个人资料和共享课程 |
| EvaluationRun | 扩展为题目质量和学习效果评测任务 |

### 3.2 新增能力

1. 课程工作区和考试目标配置。
2. 章节、知识点和资料范围映射。
3. 复习计划、学习任务和间隔复习。
4. 基于资料的题目生成、练习和解析。
5. 错题本、错因标签和知识点掌握度。
6. 学习过程指标、质量指标和数据导出。

## 4. 总体架构

```text
Vue 学习工作台
  ├── 课程概览 / 资料 / 知识点
  ├── 学习计划 / 今日任务
  ├── 引用问答 / 练习模式
  ├── 错题本 / 间隔复习
  └── 学习数据与质量反馈
          |
          v
FastAPI Learning API
  ├── Course Service：课程和考试目标
  ├── Knowledge Service：章节、知识点、资料映射
  ├── Tutor Service：检索、引用问答、解释
  ├── Quiz Service：出题、组卷、批改、解析
  ├── Review Service：错题和复习调度
  └── Analytics Service：事件、指标和报表
          |
          +--> PostgreSQL：业务状态和学习记录
          +--> Redis：队列通知、缓存和限流
          +--> Qdrant/BM25：课程资料检索
          +--> Ollama/OpenAI-compatible：生成、Embedding、可选视觉解析
```

## 5. 产品功能需求

### 5.1 课程工作区

用户创建课程时填写以下信息：

必填字段：

- 课程名称
- 考试日期

可选字段：

- 考试名称（为空时默认使用课程名称）
- 目标分数
- 考试范围或大纲
- 每周可投入时间（为空时使用系统默认值，例如每周 300 分钟）

创建课程不要求用户一次性填写完整考试范围。系统应允许用户先创建课程、上传资料，再根据文档内容建议章节和知识点。考试日期是计划计算的最小必要条件，因此创建课程接口必须要求该字段。

计划生成规则：

- 只有课程名称和考试日期时，也必须能够生成基础计划。
- 基础计划根据资料解析出的章节、默认每周学习时长和距离考试的剩余天数分配任务。
- 用户补充目标分数、考试范围或每周时长后，允许手动重建计划。
- 考试日期变更后，未开始任务按照新的剩余天数重新排期；已完成任务和历史作答不被重写。
- 考试日期早于当前日期时，停止自动排期并提示用户修改日期或进入“考后复盘”模式。

课程首页展示：资料处理状态、章节覆盖率、知识点掌握度、今日任务、近期错题和预计完成度。

验收标准：

- 新建课程后可直接上传资料。
- 修改考试日期后，未完成任务的截止时间重新计算。
- 删除课程时，课程下的计划、题目、作答和事件按策略级联删除或匿名化。

### 5.2 资料与知识点

资料继续使用现有上传、去重、异步解析和版本能力。新增字段：资料类型（教材、讲义、试卷、笔记、参考答案、演示文稿）、所属章节和可信度备注。

#### PPT/PPTX 解析要求

第一阶段必须支持 `.pptx`；`.ppt`（旧版二进制格式）作为兼容格式评估，若运行环境无法稳定解析，应在上传时明确提示用户转换为 `.pptx`。

解析内容包括：

- 每页幻灯片的标题、正文、项目符号和备注区文本。
- 表格中的单元格文本，并保留行列顺序。
- 超链接显示文本，不抓取链接指向的外部网页。
- 图片、流程图和图表的替代文本；配置视觉模型时可生成图片/图表描述。
- 幻灯片编号、页标题和原始文件名，作为引用定位信息。

解析限制与降级策略：

- 默认单个演示文稿不超过 300 页、200 MiB 解压后大小；超过限制时拒绝任务并说明原因。
- 动画、切换效果、音视频和嵌入式宏不进入知识检索；保留“未解析对象”计数。
- 无文本但包含图片的页面进入 OCR/视觉解析路径；未配置能力时保留页面缩略图并标记为“需要视觉解析”。
- 同一页的标题、正文、表格和备注合并为一个逻辑来源单元，必要时按内容长度继续切块。
- 文本来源必须带 `slide_number`；前端引用显示“第 N 页幻灯片”，不能误显示成 PDF 页码。

后端实现要求：

- 扩展上传格式白名单、MIME 校验和错误提示，支持 `application/vnd.openxmlformats-officedocument.presentationml.presentation`。
- 在 `backend/app/services/parsing.py` 增加 PPTX 解析器，建议使用 `python-pptx`；解析器输出统一的 `ParsedPage`/来源结构，供现有切块、Embedding 和 BM25 流程复用。
- 对每页记录 `page_type`（text/table/image/mixed）和 `unparsed_objects`，便于任务详情和质量指标展示。
- 解析失败时不生成部分可检索结果；任务进入 `failed`，允许用户修复文件或启用视觉模型后重试。
- 在文档版本切换、删除和重新索引时，PPTX 与现有 PDF/DOCX 使用相同的来源版本和清理策略。

知识点支持：

- 从考试大纲手动创建。
- 从章节标题和文档分块建议生成。
- 合并、拆分、重命名和标记重点。
- 关联一个或多个文档片段。
- 标记掌握状态：`unknown`、`learning`、`mastered`。

模型自动建议的知识点必须经过用户确认后才进入正式大纲，避免把模型推断当成考试范围。

### 5.3 引用式学习问答

学习问答复用现有混合检索和 Agent 检索流程，新增学习上下文：课程、章节、知识点和最近错题。

回答规则：

- 解释必须带有来源编号和页码（资料存在页码时）。
- 资料没有依据时返回“当前资料未覆盖”，不能补写为确定事实。
- 同一知识点存在冲突资料时，展示冲突来源并要求用户确认版本。
- 支持“讲解”“举例”“对比”“生成练习”四种模式。
- 默认限制在当前课程资料范围内，用户明确选择后才能跨课程检索。

### 5.4 练习题生成与作答

支持题型：单选、多选、判断、填空、简答。第一阶段优先实现单选、判断和简答。

生成参数：

- 课程和章节
- 知识点
- 题型和数量
- 难度：基础、应用、综合
- 是否包含解析
- 是否允许引用多个来源

每道题必须保存：题干、选项、标准答案、解析、知识点、来源片段、生成模型和提示词版本。

生成约束：

- 标准答案必须能由引用片段支持。
- 选择题干扰项不能与资料明确冲突到无法区分。
- 无法生成可靠答案时跳过该题并记录原因。
- 题目生成结果进入“待检查”状态，不能直接宣称为正式试题。

### 5.5 错题本与错因诊断

作答后记录：用户答案、是否正确、耗时、置信度、用户自选错因和系统建议错因。

错因标签建议：

- 概念混淆
- 公式/步骤错误
- 关键词遗漏
- 审题错误
- 记忆不牢
- 资料理解错误
- 题目或解析可能有问题

用户可以修改错因。系统不得把模型推测的错因直接作为事实写入长期画像。

### 5.6 间隔复习

第一版采用可解释的简化 Leitner 算法，不引入不可解释的黑盒推荐：

- 新卡片进入第 1 级。
- 答对升级一级，答错退回第 1 级。
- 每级对应复习间隔：1、2、4、7、14、30 天。
- 用户可选择“太简单”“记得”“模糊”“不会”，覆盖默认升级规则。

每日任务由到期卡片、近期错题和低掌握度知识点组成，并受用户每日时间预算限制。

### 5.7 学习数据面板

展示以下内容：

- 资料覆盖的章节和知识点数量
- 已学习/掌握知识点比例
- 练习题数量、作答数和正确率
- 错题重复出现率
- 复习计划完成率
- 连续学习天数
- 来源引用覆盖率
- 无依据回答拦截数

所有指标都显示统计范围和样本量，避免用小样本结果制造“成绩提升”结论。

## 6. 后端设计

### 6.1 目录建议

```text
backend/app/
  api/learning.py              # 课程、计划、知识点、题目和复习接口
  schemas_learning.py         # Pydantic 请求/响应模型
  services/learning.py        # 课程和知识点服务
  services/quiz.py            # 题目生成、批改和解析
  services/review.py          # 错题、复习卡和计划调度
  services/learning_metrics.py# 事件采集和指标计算
  services/parsing.py         # 扩展 PPTX 页面、表格和图片解析
  worker.py                   # 新增 quiz/review 任务类型
```

### 6.2 数据表

推荐使用新增表，不直接把所有学习字段塞进 `KnowledgeBase`。

#### `courses`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID | 主键 |
| `user_id` | UUID | 所有者 |
| `knowledge_base_id` | UUID | 复用现有知识库 |
| `name` | varchar | 课程名称 |
| `exam_date` | datetime | 考试时间，创建课程时必填 |
| `target_score` | numeric nullable | 目标分数 |
| `weekly_minutes` | integer nullable | 每周学习预算，空值使用系统默认值 |
| `status` | varchar | active/archived |
| `created_at` | datetime | 创建时间 |

#### `course_units`

章节或模块树。字段包括 `id`、`course_id`、`parent_id`、`title`、`ordinal`、`weight`、`status`。

#### `knowledge_points`

字段包括 `id`、`course_id`、`unit_id`、`title`、`description`、`importance`、`mastery_state`、`mastery_score`、`source`、`confirmed_at`。

#### `knowledge_point_chunks`

知识点与现有 `chunks` 的多对多关系，主键为 `knowledge_point_id + chunk_id`。

#### `quiz_items`

字段包括 `id`、`course_id`、`unit_id`、`knowledge_point_id`、`type`、`difficulty`、`stem`、`options`、`answer`、`explanation`、`citations`、`status`、`model_metadata`、`created_at`。

`status`：`draft`、`approved`、`rejected`、`retired`。

#### `quiz_attempts`

字段包括 `id`、`user_id`、`quiz_item_id`、`answer`、`is_correct`、`duration_ms`、`confidence`、`error_tags`、`feedback`、`created_at`。

#### `review_cards`

字段包括 `id`、`user_id`、`course_id`、`knowledge_point_id`、`quiz_item_id nullable`、`level`、`due_at`、`last_reviewed_at`、`review_count`、`failure_count`。

#### `study_plans` / `study_tasks`

计划保存考试目标和时间预算；任务保存 `task_type`、`target_id`、`scheduled_for`、`completed_at`、`status`。

#### `learning_events`

字段包括 `id`、`user_id`、`course_id`、`event_name`、`entity_type`、`entity_id`、`properties`、`created_at`。禁止写入密码、访问令牌和完整文档正文。

### 6.3 API 设计

所有接口继承现有认证、课程/知识库权限和请求 ID 机制。

#### 课程资料上传与解析

```text
POST   /api/courses/{course_id}/materials/upload
GET    /api/courses/{course_id}/materials
GET    /api/materials/{document_id}/parse-status
POST   /api/materials/{document_id}/retry
```

上传接口应接受 `pptx`，并在响应中返回 `file_type`、`page_count`、`slide_count`、`parse_capabilities` 和 `unparsed_objects`。为兼容现有接口，也可以继续使用 `/api/documents/upload`，但课程模式必须在服务端校验文档属于当前课程的 KnowledgeBase。

PPTX 解析状态至少包含：`uploaded`、`extracting`、`vision_pending`、`chunking`、`embedding`、`ready`、`failed`。`vision_pending` 不能被误报为“资料已完成”，问答和出题默认排除尚未完成视觉解析的页面。

引用响应增加以下字段：

```json
{
  "document_id": "...",
  "page": null,
  "slide_number": 12,
  "page_label": "第 12 页幻灯片",
  "source_type": "pptx",
  "chunk_id": "..."
}
```

`slide_number` 与 PDF 的 `page` 不可混用；前端根据 `page_label` 展示来源位置。

#### 课程

```text
POST   /api/courses
GET    /api/courses
GET    /api/courses/{course_id}
PATCH  /api/courses/{course_id}
DELETE /api/courses/{course_id}
GET    /api/courses/{course_id}/overview
```

创建课程请求约束：

```json
{
  "name": "数据结构",
  "exam_date": "2026-12-20",
  "exam_name": null,
  "target_score": null,
  "syllabus": null,
  "weekly_minutes": null
}
```

`name` 和 `exam_date` 缺失或格式非法时返回 `422`。`exam_name` 为空时服务端写入课程名称；`weekly_minutes` 为空时写入系统默认值并在响应中返回实际采用的分钟数。课程创建成功后立即生成基础计划，资料和知识点尚未准备好时先生成占位任务，资料索引完成后再补充具体章节任务。

#### 章节与知识点

```text
GET    /api/courses/{course_id}/units
POST   /api/courses/{course_id}/units
PATCH  /api/course-units/{unit_id}
POST   /api/courses/{course_id}/knowledge-points/suggest
POST   /api/courses/{course_id}/knowledge-points
PATCH  /api/knowledge-points/{point_id}
POST   /api/knowledge-points/{point_id}/confirm
GET    /api/knowledge-points/{point_id}/sources
```

`suggest` 只生成待确认建议，不能直接改变正式考试大纲。

#### 学习问答

```text
POST   /api/courses/{course_id}/tutor/ask
POST   /api/courses/{course_id}/tutor/explain
POST   /api/courses/{course_id}/tutor/generate-practice
```

请求至少包含 `question`、`unit_id nullable`、`knowledge_point_id nullable`、`mode` 和幂等键。响应沿用现有 SSE/引用格式，并增加 `course_id`、`scope` 和 `evidence_status`。

#### 题目与作答

```text
POST   /api/courses/{course_id}/quiz/generate
GET    /api/courses/{course_id}/quiz
GET    /api/quiz/{quiz_id}
POST   /api/quiz/{quiz_id}/attempt
POST   /api/quiz/{quiz_id}/feedback
PATCH  /api/quiz/{quiz_id}/review-status
```

批量出题返回 `202` 和任务 ID；题目生成由 Worker 执行，前端通过现有任务状态接口轮询或接收通知。

#### 错题与复习

```text
GET    /api/courses/{course_id}/mistakes
PATCH  /api/attempts/{attempt_id}/error-tags
POST   /api/attempts/{attempt_id}/retry
GET    /api/courses/{course_id}/review/today
POST   /api/review-cards/{card_id}/grade
GET    /api/courses/{course_id}/study-plan
POST   /api/courses/{course_id}/study-plan/rebuild
POST   /api/study-tasks/{task_id}/complete
```

#### 指标

```text
GET /api/courses/{course_id}/metrics/overview
GET /api/courses/{course_id}/metrics/mastery
GET /api/courses/{course_id}/metrics/quality
GET /api/courses/{course_id}/metrics/export
```

指标接口必须返回 `value`、`sample_size`、`period`、`definition_version`，前端不能只展示一个没有统计口径的百分比。

### 6.4 生成与安全规则

题目和解释生成流程：

```text
选择课程范围
  -> 混合检索候选分块
  -> 检查知识点覆盖和来源版本
  -> 模型生成草稿
  -> 引用编号、答案和数字校验
  -> 无依据或冲突时标记 draft/rejected
  -> 保存题目和生成元数据
```

必须防范：

- 文档内容中的提示词注入。
- 把模型建议知识点当成考试大纲。
- 题目答案没有来源依据。
- 已过期或无权限文档进入题目和答案。
- 删除课程后缓存仍返回旧题目。

缓存键至少包含课程代次、资料版本、知识点范围、题型、题目数量、模型配置和提示词版本。

## 7. 前端设计

### 7.1 页面与路由

```text
/courses                         课程列表
/courses/:id                     课程概览
/courses/:id/materials           资料与解析状态
/courses/:id/outline             章节与知识点
/courses/:id/study-plan          学习计划和今日任务
/courses/:id/tutor               引用式问答
/courses/:id/practice             练习模式
/courses/:id/mistakes             错题本
/courses/:id/review               间隔复习
/courses/:id/analytics            学习数据和质量指标
```

### 7.2 组件建议

```text
components/learning/
  CourseOverview.vue
  CourseSetupDialog.vue
  MaterialUploadPanel.vue
  ParseStatusBadge.vue
  SlideSourceViewer.vue
  OutlineTree.vue
  KnowledgePointCard.vue
  StudyPlanPanel.vue
  TodayTaskList.vue
  TutorPanel.vue
  QuizRunner.vue
  QuizReview.vue
  MistakeBook.vue
  ReviewSession.vue
  MasteryChart.vue
  LearningMetricsPanel.vue
```

### 7.3 关键交互

- 上传资料后显示解析、切块和索引阶段，不让用户误以为资料已可用。
- 上传 PPT/PPTX 时显示文件类型、总页数、已解析页数和未解析对象数；图片页显示“需要视觉解析”状态。
- PPTX 引用打开幻灯片来源视图，展示页码、标题、正文、表格和图片描述；不能把幻灯片页码显示成普通 PDF 页码。
- PPTX 解析失败时保留失败原因和重试入口，不显示部分结果为完整资料；用户可以删除后重新上传转换后的 `.pptx`。
- 知识点建议必须有“确认/编辑/忽略”操作。
- 问答答案中的引用可展开原文、页码和所属知识点。
- 练习提交后先显示判定，再显示解析和来源，不直接用颜色暗示模型绝对正确。
- 题目存在待审核状态时，展示“基于资料生成，建议核对”提示。
- 今日任务允许跳过、调整时长和标记无法完成，避免计划只增不减。
- 指标图表显示样本量，数据不足时显示“样本不足”，不显示误导性趋势。
- 移动端优先保证刷题、错题复习和引用展开可用。

### 7.4 前端状态管理

建议按课程缓存以下状态：

- `courseOverview`
- `outline`
- `todayTasks`
- `activeQuiz`
- `mistakes`
- `metrics`

- `materialParseStatus`：按文档保存 `file_type`、`slide_count`、`processed_slides`、`unparsed_objects` 和 `capabilities`。

资料、题目生成和计划重建使用任务状态轮询；问答使用现有 SSE。课程代次变化后清理问答和题目缓存。

## 8. 指标体系

指标分为学习效果、产品使用、模型质量和系统运行四类。第一阶段只把“过程指标”和“质量指标”作为正式结果，不能把小样本正确率包装为成绩提升。

### 8.1 学习效果指标

| 指标 | 定义 | 口径 |
| --- | --- | --- |
| 知识点掌握率 | `mastered_points / confirmed_points` | 仅统计已确认知识点 |
| 练习正确率 | `correct_attempts / graded_attempts` | 按课程、章节和题型拆分 |
| 错题复现率 | `再次答错的原错题数 / 进入复习的错题数` | 观察 7/14/30 天窗口 |
| 复习计划完成率 | `completed_tasks / scheduled_tasks` | 排除用户主动跳过的任务或单独展示 |
| 知识点前后测变化 | `post_test - pre_test` | 必须使用同一知识点的独立题目 |

### 8.2 产品使用指标

| 指标 | 定义 |
| --- | --- |
| 首次资料可用时间 | 上传完成到文档可检索的时间 |
| 首次有效问答时间 | 注册到第一次产生有效引用答案的时间 |
| 周活跃学习用户 | 7 天内完成一次问答、练习或复习的用户数 |
| 课程激活率 | 创建课程后完成资料上传和一次学习任务的比例 |
| 复习任务回访率 | 首次完成任务后 7 天内再次完成复习的比例 |
| 问答转练习率 | 问答后生成或完成练习的会话比例 |

### 8.3 模型与内容质量指标

| 指标 | 定义 | 目标/说明 |
| --- | --- | --- |
| 引用覆盖率 | 有效答案中可定位到来源的结论比例 | 目标 100%，不足时拒答或摘录 |
| 无依据回答拦截率 | 被证据检查拦截的无依据草稿数 / 无依据草稿总数 | 通过人工抽样校验 |
| 题目答案支持率 | 经人工或规则核验、可被来源支持的题目比例 | 正式题库前必须达标 |
| 题目可作答率 | 用户认为题干和选项信息完整的题目比例 | 记录“题目有问题”反馈 |
| 解释相关性 | 用户对解析的有帮助评价 | 采用 1-5 分反馈，不与事实正确率混用 |
| 检索 Hit@5 / Recall@5 / MRR@5 | 标注题集上的来源检索指标 | 按课程和版本统计 |
| PPT 幻灯片引用准确率 | 引用中的 `slide_number` 与原始幻灯片位置一致的比例 | 通过自动页号校验和人工抽样确认 |

### 8.4 系统指标

- 资料解析成功率，按 PDF、DOCX、PPTX 和图片分别统计。
- PPTX 幻灯片解析覆盖率：`已完成解析的幻灯片数 / 总幻灯片数`。
- PPTX 未解析对象率：`未解析对象数 / 检测到的对象总数`，图片视觉解析未启用时单独标记。
- 资料到可检索的 P50/P95 时延。
- 问答首 token 和完整回答时延。
- 题目生成任务成功率、重试率和平均耗时。
- Redis 缓存命中率。
- Qdrant、PostgreSQL 和 Worker 健康状态。

### 8.5 事件埋点

建议事件：

```text
course_created
material_uploaded
material_parse_started
material_parse_completed
material_parse_failed
slide_vision_pending
material_ready
knowledge_point_confirmed
tutor_question_submitted
tutor_answer_cited
quiz_generated
quiz_attempt_submitted
quiz_feedback_reported
review_started
review_card_graded
study_task_completed
plan_rebuilt
```

事件属性只保存枚举、ID、耗时、计数和版本信息，不保存完整问题、答案或文档正文，除非用户明确开启调试记录。

## 9. 测试与验收

### 9.1 后端测试

- 课程归属和跨课程访问拒绝。
- 知识点建议未确认前不进入正式计划。
- 已过期文档不会进入问答、题目和复习卡。
- 题目生成任务可重试、取消和恢复。
- 作答幂等，重复提交不重复计分。
- 错题升级和降级符合 Leitner 规则。
- 课程删除后相关缓存失效。
- 指标在零样本、单样本和权限撤销后返回正确状态。
- 题目引用和答案校验失败时进入 draft/rejected，不向用户伪装成确定答案。
- `.pptx` 上传成功并进入解析任务，`.ppt` 按兼容策略接受或返回明确的转换提示。
- PPTX 标题、正文、项目符号、备注、表格和页码被正确提取；幻灯片来源可以回溯。
- 图片页在未启用视觉模型时进入 `vision_pending`，启用后可重试并更新覆盖率。
- PPTX 超过页数或解压大小限制时被拒绝，损坏文件不会留下可检索的部分分块。
- PPTX 文本中的恶意提示词不会改变题目生成和问答的系统指令。

### 9.2 前端验收

- 新用户可在 5 分钟内创建课程、上传资料并完成第一次引用问答。
- 用户能从答案引用跳转到原文页码。
- 用户能上传 PPTX，并在资料详情中看到幻灯片解析进度和未解析对象提示。
- 用户能从问答或题目引用打开指定幻灯片，而不是只看到文件名。
- 用户能完成一题、看到解析、修改错因并加入错题本。
- 用户能完成一次今日复习，并看到下一次到期时间。
- 指标卡片展示数值、样本量和统计周期。
- 移动宽度下题目选项、引用和按钮不溢出。

### 9.3 最小验收数据集

首轮准备：

- 1 门课程。
- 4 个章节。
- 20 个已确认知识点。
- 3 份讲义或教材。
- 至少 1 份包含标题、表格、备注和图片页的 PPTX 测试资料。
- 1 份损坏或超限 PPTX，用于验证失败和重试状态。
- 30 条标注问题。
- 50 道人工检查过的题目。
- 20 条人工标注的错题记录。

## 10. 分阶段开发计划

### Phase 1：学习版 MVP

- 课程工作区和考试目标。
- 资料上传（含 PPTX）、章节和知识点手动维护。
- 带引用的课程问答。
- 今日学习任务和基础指标。

### Phase 2：练习闭环

- 题目生成与人工审核状态。
- 单选/判断/简答作答。
- 错题本、错因标签和知识点掌握度。
- 题目质量反馈。

### Phase 3：个性化复习

- Leitner 间隔复习。
- 根据错题和考试日期重建计划。
- 前测/后测对比。
- 学习数据导出和课程版本管理。

### Phase 4：增强能力

- 图片和手写笔记解析。
- 小组共享和助教审核。
- 资料冲突检测。
- 题目质量评测集和 A/B 测试。

## 11. 风险与应对

| 风险 | 应对 |
| --- | --- |
| 模型生成错误题目 | 来源约束、草稿审核、题目反馈、独立题集评测 |
| 资料版权问题 | 仅处理用户有权使用的资料，提供删除和导出能力 |
| 用户不持续学习 | 先聚焦明确考试日期和每日任务，减少复杂设置 |
| 指标被小样本误导 | 所有指标携带样本量和周期，低于阈值显示样本不足 |
| 计划过于复杂 | 第一版使用可解释规则，允许手动调整和跳过 |
| 学习数据泄露 | 最小化埋点、权限校验、敏感字段不入日志、支持课程删除 |
| 与普通聊天产品同质化 | 强化引用、错题闭环、知识点掌握度和复习计划，而非只做问答 |

## 12. 产品结果的对外表达

在没有真实大规模用户实验前，简历或项目介绍使用以下表述：

> 面向有明确考试范围的学生，设计基于个人资料的学习与复习闭环，将文档检索、引用问答、自动出题、错题归因和间隔复习整合到课程工作区；通过引用覆盖率、题目答案支持率、知识点掌握度、错题复现率和计划完成率评估产品质量。

不要使用未经实测的表述，例如“提升考试成绩 30%”“准确率达到 95%”或“显著提升学习效率”。这些数字必须来自预先定义的题集、用户实验和明确统计周期。
