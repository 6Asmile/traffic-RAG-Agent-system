# ITQA — 智能交通法规问答与出行规划 Agent 系统

![License](https://img.shields.io/badge/License-MIT-blue.svg)
![Python](https://img.shields.io/badge/Python-3.12-green.svg)
![Vue](https://img.shields.io/badge/Vue-3.x-4FC08D.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.128-009688.svg)
![TypeScript](https://img.shields.io/badge/TypeScript-5.x-3178C6.svg)

**ITQA（Intelligent Traffic Question-Answering）** 是一款专为 **交通法规咨询** 与 **智能出行规划** 打造的全栈 AI Agent 平台。它既是能精准回答国标法条的法律顾问，也是能结合实时地图数据为你规划行程的生活助理。

---

## 🎯 项目简介

### 解决的问题

在日常交通场景中，普通人面对海量的 GB 国标、道路交通安全法、地方性法规时常常感到无所适从。与此同时，出行前的路线规划、实时路况查询、天气预警等需求也分散在多个应用中，信息割裂，体验碎片化。

ITQA 将这两大场景合二为一，借助大语言模型（LLM）的语义理解能力与外部工具的实时数据调取能力，提供了一个 **一站式、零门槛** 的智能服务平台。

### 差异化亮点

| 特性 | 传统方案 | ITQA |
|------|---------|------|
| 法条查询 | 搜索引擎 + 人工筛选 | Hybrid RAG 精准检索 + 来源追溯 |
| 回答可靠性 | 可能产生幻觉 | 三重防幻觉机制（召回漏斗 + Rerank 熔断 + CoT 校验） |
| 出行规划 | 多个 App 切换 | Agent 自动调用高德 API，结果汇总在对话中 |
| 学习刷题 | 驾考 App 固定题库 | LLM 动态出题 + Python 洗牌防作弊 |
| 数据洞察 | 需专业数据分析 | 一键 K-Means 聚类 + LLM 自动总结热点 |

---

## ✨ 核心功能

### 1. 智能交通法律顾问（Chatbot）

基于 **Hybrid RAG（混合检索增强生成）** 架构，对用户的法律问题进行精准解答。

- **双路混合召回**：FAISS 稠密检索（语义，Top-40）+ BM25 稀疏检索（关键词，Top-20），互补检索盲区
- **Rerank 防幻觉熔断**：通过阿里云 `gte-rerank` 对候选片段精排，得分低于 0.05 阈值时物理截断生成链路，从根源杜绝幻觉
- **CoT 适用范围校验**：思维链提示词教导模型进行"主体匹配"，若用户询问"外星人酒驾"等非法定主体，优雅拒答
- **引用回溯机制**：每次回答强制标注 `[资料X]` 标签，前端支持点击展开法条原文
- **多模态交互**：支持语音输入（Web Speech API）与 TTS 朗读回复

### 2. 出行规划管家（Travel Agent）

采用 LangChain 原生 **Native Tool Calling** 架构，AI 自动判断是否需要调用外部工具。

- **路线规划**：输入"从玄武招商花园到新街口怎么走"，Agent 自动规划驾车/公交/步行路线，包含距离、耗时、红绿灯数、打车费预估、高速过路费等细粒度信息
- **静态地图预览**：路线结果中嵌入高德静态地图图片，直观展示起终点位置
- **周边搜索**：查询附近加油站、充电桩、交警大队、修车厂等设施
- **实时天气**：查询指定城市的实时天气与未来多日预报
- **模糊地名解析**：内置 POI 搜索 + 地理编码两级回退机制，输入"桂电"也能正确解析

### 3. 每日一练刷题中心（Smart Quiz）

智能题库系统，千人千面，防作弊。

- **动态出题**：从知识库文档中随机抽取上下文片段，LLM 自动生成高难度情景选择题
- **Python 洗牌算法**：LLM 存在"Lazy LLM"倾向（总把正确答案放在 A 选项），后端截获 JSON 后以 Python 随机打乱选项顺序，重新锁定正确答案
- **千人千面**：根据用户 `UserQuizRecord` 自动过滤已做题目，题库不足时后台异步补题
- **错题复习**：新题不足时自动混合已答旧题作为复习，优雅降级

### 4. 知识图谱可视化（Knowledge Graph）

- **三元组抽取**：LLM 从法条中提取 `(实体, 关系, 实体)` 三元组并持久化
- **力导向图漫游**：前端使用 ECharts 力导向图展示交通法律实体之间的因果逻辑链（如"酒驾 → 处罚措施 → 吊销驾照"）
- **RAG 增强**：在生成答案时，将命中的图谱因果链作为独立上下文喂给 LLM，增强责任判定推理的准确性

### 5. 舆情分析大屏（Analytics）

- **K-Means 无监督聚类**：将海量用户咨询自动分组
- **LLM 智能总结**：对每个聚类簇自动提炼核心主题和高频关键词
- **热点词云**：热门话题按热度排序，一目了然

### 6. BYOK 多租户支持

- **用户自定义 Key**：在「AI 设置」页面，用户可配置自己的 LLM / Embedding / Vision 模型的 API Key 和模型名
- **隔离生效**：个人配置即时生效，不影响其他用户，满足多租户场景

---

## 🏗️ 技术架构

### 请求处理流程

```text
用户提问（文本 / 语音）
    │
    ▼
┌──────────────────────┐
│  1. 语义缓存检查      │  Redis + Cosine Similarity
│     → 命中则毫秒返回   │
└──────┬───────────────┘
       │ 未命中
       ▼
┌──────────────────────┐
│  2. Query 改写        │  LLM 结合历史对话进行指代消解
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│  3. Agent 工具判定     │  LangChain bind_tools
│  ┌──────────────────┐ │
│  │ 路线规划 API      │ │
│  │ 周边搜索 API      │ │
│  │ 天气查询 API      │ │
│  └──────────────────┘ │
└──────┬───────────────┘
       │ 未命中工具
       ▼
┌──────────────────────┐
│  4. 混合 RAG 检索     │
│  ┌──────────────────┐ │
│  │ FAISS 稠密 Top-40 │ │
│  │ BM25  稀疏 Top-20 │ │
│  └──────┬───────────┘ │
│         ▼              │
│  ┌──────────────────┐ │
│  │ Rerank 精排+熔断  │ │  低于阈值 → 物理阻断生成
│  └──────┬───────────┘ │
│         ▼              │
│  ┌──────────────────┐ │
│  │ 知识图谱关联查询  │ │  三元组命中
│  └──────┬───────────┘ │
│         ▼              │
│  ┌──────────────────┐ │
│  │ LLM CoT 生成回答  │ │  Prompt 模板 + 防幻觉保障
│  └──────────────────┘ │
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│  5. SSE 流式输出       │  打字机效果 + Markdown 渲染
│  + 写入语义缓存 + 历史  │
└──────────────────────┘
```

### RAG 管道详解

```text
文档入库                              检索与生成
─────────                            ─────────
PDF/DOCX/TXT                         User Query
    │                                     │
    ▼                                     ▼
Docling 解析 → Markdown              Query 改写（LLM）
    │                                     │
    ▼                                     ▼
表格感知切片                          FAISS + BM25 混合检索
(表格=结构化, 正文=递归拆分)                │
    │                                     ▼
    ▼                                 Rerank 精排
存入 FAISS + BM25 索引               (阿里云 gte-rerank)
                                        │
                                        ▼
                                   KG 三元组补充
                                        │
                                        ▼
                                   LLM 生成 + 引用标注
                                        │
                                        ▼
                                   SSE 流式返回前端
```

---

## 🛠️ 技术栈

### 后端（Backend）

| 类别 | 技术 | 说明 |
|------|------|------|
| 核心框架 | FastAPI 0.128+ | 异步 Web 框架，支持 SSE 流式响应 |
| 编程语言 | Python 3.12 | 类型注解 + async/await |
| AI 框架 | LangChain | LLM 调用、Tool Calling、文档加载 |
| 向量数据库 | FAISS | 稠密向量相似度检索 |
| 稀疏检索 | Rank-BM25 | 关键词级别 BM25 检索 |
| 重排序 | 阿里云 gte-rerank | 候选片段精排 + 阈值截断 |
| Embedding | 阿里云 text-embedding | 文本向量化 |
| LLM | DeepSeek / OpenAI | 对话生成、问答、出题 |
| 缓存 | Redis | 语义缓存 + 会话历史 |
| 数据库 | MySQL 8.0 + SQLAlchemy | 持久化存储 |
| 文档解析 | Docling + PDFPlumber | PDF/Office 转 Markdown |
| 分词 | Jieba | 中文分词（BM25 索引构建） |
| 聚类 | scikit-learn K-Means | 用户提问无监督聚类 |

### 前端（Frontend）

| 类别 | 技术 | 说明 |
|------|------|------|
| 核心框架 | Vue 3（Composition API） | 响应式 UI |
| 类型系统 | TypeScript 5.x | 静态类型检查 |
| 构建工具 | Vite 7.x | 极速 HMR 开发服务器 |
| UI 组件库 | Element Plus | 企业级组件 |
| 可视化 | ECharts 6.x | 力导向图、饼图 |
| 路由 | Vue Router 5.x | SPA 路由管理 |
| HTTP 客户端 | Axios | API 请求 + 拦截器 |
| Markdown 渲染 | markdown-it | 对话内容实时渲染 |
| 语音交互 | Web Speech API + Capacitor 插件 | 语音识别 + TTS |
| 移动端 | Capacitor 8.x | Android APK 打包 |
| 样式 | SCSS | 响应式布局（支持移动端） |

### 外部服务

| 服务 | 用途 |
|------|------|
| DeepSeek / OpenAI API | 大语言模型推理 |
| 阿里云 DashScope | Embedding 向量化 + Rerank 重排序 |
| 高德地图 API | 路线规划、POI 搜索、天气查询、静态地图 |
| 阿里云通义千问 | Vision 模型（可选） |

---

## 📂 项目目录结构

```text
traffic_qa_system-1/
│
├── README.md                           # 项目说明文档（本文件）
│
├── backend/                            # 后端服务（Python / FastAPI）
│   ├── main.py                         # ★ 应用入口：FastAPI 实例、CORS、路由注册
│   ├── requirements.txt                # Python 依赖清单
│   ├── init_db.py                      # 数据库初始化脚本（建表）
│   ├── .env                            # 环境变量（需自行创建）
│   ├── .env.example                    # 环境变量模板
│   │
│   ├── app/                            # 应用主目录
│   │   ├── __init__.py
│   │   │
│   │   ├── api/endpoints/              # API 路由层
│   │   │   ├── auth.py                 # 认证接口（注册/登录/验证码）
│   │   │   ├── chat.py                 # ★ 核心对话接口（流式问答/文件上传/分析/图谱/反馈）
│   │   │   ├── quiz.py                 # 每日一练接口（获取题目/提交答案/生成题库）
│   │   │   ├── evaluation.py           # RAG 评估接口（运行评估/查询结果/管理数据集）
│   │   │   └── test.py                 # 测试接口
│   │   │
│   │   ├── core/                       # 核心配置层
│   │   │   ├── config.py               # pydantic-settings 配置类（读取 .env）
│   │   │   ├── prompts.py              # 所有 LLM Prompt 模板（RAG/出题/分析/Agent）
│   │   │   └── security.py             # JWT Token 生成与验证、密码哈希
│   │   │
│   │   ├── db/                         # 数据库层
│   │   │   ├── base.py                 # SQLAlchemy Base 声明
│   │   │   └── session.py              # Session 工厂（SessionLocal）
│   │   │
│   │   ├── models/                     # ORM 数据模型
│   │   │   ├── __init__.py             # 模型统一导出
│   │   │   ├── user.py                 # User（含角色、头像、AI偏好）
│   │   │   ├── chat.py                 # ChatSession + ChatMessage（含反馈评分）
│   │   │   ├── knowledge.py            # KnowledgeDoc（文档元信息+解析缓存）
│   │   │   ├── config.py               # AIConfig（多租户 AI 模型配置）
│   │   │   ├── quiz.py                 # Question + UserQuizRecord
│   │   │   ├── graph.py                # GraphNode + GraphEdge（知识图谱）
│   │   │   ├── analysis.py             # HotTopic（聚类热点话题）
│   │   │   └── evaluation.py           # EvalDataset + EvalResult + EvalRun
│   │   │
│   │   ├── schemas/                    # Pydantic 请求/响应模型
│   │   │   └── user.py                 # UserAuth, Token, UserOut
│   │   │
│   │   └── services/                   # ★ 业务逻辑层（核心大脑）
│   │       ├── rag_service.py          # RAG 核心引擎（检索→Rerank→生成→流式）
│   │       ├── tool_service.py         # Agent 工具集（高德 API: 路线/POI/天气）
│   │       ├── cache_service.py        # Redis 语义缓存管理
│   │       ├── config_service.py       # AI 配置读取服务
│   │       ├── quiz_service.py         # 出题服务（LLM 生成 + 洗牌防作弊）
│   │       ├── evaluation_service.py   # RAG 评估引擎（LLM-as-Judge）
│   │       ├── graph_service.py        # 知识图谱构建与查询
│   │       ├── analytics_service.py    # K-Means 聚类 + LLM 舆情总结
│   │       ├── hybrid_search.py        # 混合检索工具
│   │       ├── eval_metrics.py         # 评估指标定义（忠实度/准确度等）
│   │       └── eval_dataset_default.py # 默认评估数据集
│   │
│   └── data/                           # 数据目录
│       ├── uploads/                    # 上传的知识文档（PDF/TXT/DOCX）
│       │   └── avatars/                # 用户头像
│       ├── faiss_index/                # FAISS 向量索引文件（index.faiss + index.pkl）
│       └── .ragas_cache/               # 评估缓存
│
├── frontend/                           # 前端应用（Vue 3 / TypeScript / Vite）
│   ├── index.html                      # HTML 入口
│   ├── package.json                    # 前端依赖
│   ├── vite.config.ts                  # Vite 构建配置
│   ├── tsconfig.json                   # TypeScript 配置
│   ├── capacitor.config.ts             # Capacitor 移动端配置
│   │
│   ├── src/
│   │   ├── main.ts                     # Vue 应用入口
│   │   ├── App.vue                     # 根组件
│   │   │
│   │   ├── router/index.ts             # 路由定义 + 登录守卫
│   │   ├── api/                        # Axios 请求封装
│   │   │   ├── request.ts              # Axios 实例 + 拦截器
│   │   │   └── config.ts               # API 地址配置
│   │   ├── config/constants.ts         # 常量定义
│   │   │
│   │   └── views/                      # 页面组件
│   │       ├── Landing.vue             # 功能大厅（卡片导航）
│   │       ├── Login.vue               # 登录 / 注册
│   │       ├── Chat.vue                # ★ 核心对话页（流式打字机/Markdown/语音/反馈）
│   │       ├── Quiz.vue                # 每日一练刷题
│   │       ├── Admin.vue               # 管理后台（知识库/分析/评估）
│   │       ├── GraphView.vue           # 知识图谱力导向图
│   │       ├── Profile.vue             # 个人中心
│   │       └── AiSettings.vue          # AI 配置（BYOK）
│   │
│   ├── public/                         # 静态资源
│   └── android/                        # Capacitor Android 原生壳
│
└── .trae/documents/                    # 开发文档
```

---

## 🚀 本地快速启动

### 前置依赖

请确保本地已安装以下软件：

- **Python** 3.12+
- **Node.js** 18+
- **MySQL** 8.0+
- **Redis** 7.0+

### 1. 克隆项目

```bash
git clone <repository-url>
cd traffic_qa_system-1
```

### 2. 后端部署

```bash
cd backend

# 2.1 创建虚拟环境（推荐）
python -m venv venv

# 2.2 激活虚拟环境
# Windows:
venv\Scripts\activate
# Linux / macOS:
source venv/bin/activate

# 2.3 安装依赖
pip install -r requirements.txt

# 2.4 配置环境变量
# 复制 .env.example 为 .env，编辑填入你的 MySQL、Redis 等信息
cp .env.example .env

# 2.5 初始化数据库表结构
python init_db.py

# 2.6 启动 FastAPI 服务
python main.py
```

### 3. 前端部署

```bash
cd frontend

# 3.1 安装依赖
npm install

# 3.2 启动 Vite 开发服务器
npm run dev
```

---

## 📡 API 接口文档

### 认证模块 `/api/v1/auth`

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| POST | `/register` | 用户注册 | 否 |
| POST | `/login` | 用户登录，返回 JWT Token | 否 |
| GET  | `/captcha` | 获取验证码 | 否 |

### 智能问答模块 `/api/v1/chat`

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| POST | `/ask_stream` | ★ 核心流式问答（SSE） | 是 |
| GET  | `/me` | 获取当前用户信息 | 是 |
| GET  | `/sessions` | 获取用户的所有对话会话 | 是 |
| GET  | `/history/{session_id}` | 获取指定会话的消息历史 | 是 |
| GET  | `/stats` | 获取用户统计数据 | 是 |
| PUT  | `/update_me` | 修改用户名 | 是 |
| POST | `/change_password` | 修改密码 | 是 |
| POST | `/upload_avatar` | 上传头像 | 是 |
| POST | `/feedback` | 提交回答质量反馈 | 是 |
| POST | `/upload` | 上传知识库文档（管理员） | 是 |
| GET  | `/knowledge_list` | 知识库文档列表 | 是 |
| DELETE | `/knowledge/{doc_id}` | 删除知识库文档 | 是 |
| DELETE | `/session/{session_id}` | 删除对话会话 | 是 |
| POST | `/perform_analysis` | 触发 AI 聚类分析（管理员） | 是 |
| GET  | `/analytics` | 获取分析结果（管理员） | 是 |
| GET  | `/knowledge_graph` | 获取知识图谱数据 | 是 |
| POST | `/build_graph` | 触发图谱构建（管理员） | 是 |
| GET  | `/ai_settings` | 获取 AI 偏好设置 | 是 |
| POST | `/ai_settings` | 保存 AI 偏好设置（BYOK） | 是 |
| POST | `/warmup_cache` | 预热语义缓存 | 是 |

### 每日一练模块 `/api/v1/quiz`

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| GET  | `/daily` | 获取每日题目（自动过滤已做） | 是 |
| POST | `/submit` | 提交答案 | 是 |
| GET  | `/my_stats` | 获取个人答题统计 | 是 |
| POST | `/admin_generate` | 管理员强制生题（异步） | 是 |

### RAG 评估模块 `/api/v1/evaluation`

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| POST | `/run` | 触发评估运行（异步后台任务） | 管理员 |
| GET  | `/results` | 查询评估结果详情 | 管理员 |
| GET  | `/latest` | 获取最新一次评估分数汇总 | 管理员 |
| GET  | `/history` | 获取评估历史记录 | 管理员 |
| GET  | `/datasets` | 查看评估数据集 | 管理员 |
| POST | `/datasets` | 添加单条评估数据 | 管理员 |
| POST | `/datasets/batch` | 批量添加评估数据 | 管理员 |
| POST | `/datasets/init_default` | 导入默认评估数据集 | 管理员 |
| DELETE | `/datasets/{id}` | 删除评估数据条目 | 管理员 |
| GET  | `/status/{run_id}` | 查询指定批次运行状态 | 管理员 |
| GET  | `/active_run` | 查询是否有正在运行的任务 | 管理员 |

---

## 🗄️ 数据库设计

### 核心表概览

| 表名 | 对应模型 | 说明 |
|------|---------|------|
| `users` | User | 用户账号、角色（admin/user）、头像、AI 偏好 JSON |
| `chat_sessions` | ChatSession | 对话会话（关联用户） |
| `chat_messages` | ChatMessage | 单条消息（user/ai）+ 反馈评分 |
| `knowledge_docs` | KnowledgeDoc | 知识库文档元信息、切片数、解析内容缓存 |
| `ai_configs` | AIConfig | AI 模型配置（多套，按 is_active 激活） |
| `questions` | Question | 题库（含洗牌后的选项和正解） |
| `user_quiz_records` | UserQuizRecord | 用户答题记录 |
| `graph_nodes` | GraphNode | 知识图谱节点 |
| `graph_edges` | GraphEdge | 知识图谱边（三元组关系） |
| `hot_topics` | HotTopic | 聚类分析热点话题 |
| `eval_datasets` | EvalDataset | 评估数据集（问题+参考答案） |
| `eval_results` | EvalResult | 单条评估结果（4 维度分数） |
| `eval_runs` | EvalRun | 评估运行批次记录 |

### ER 关系简述

```text
User ──1:N──> ChatSession ──1:N──> ChatMessage
User ──1:N──> UserQuizRecord ──N:1──> Question ──N:1──> KnowledgeDoc
KnowledgeDoc ──1:N──> GraphNode ──N:N──> GraphEdge
EvalRun ──1:N──> EvalResult ──N:1──> EvalDataset
AIConfig （独立配置表）
HotTopic （独立聚类结果表）
```

---

## 🤝 贡献指南

欢迎任何形式的贡献！请按以下流程参与：

### 提交流程

1. **Fork** 本仓库
2. 创建特性分支：`git checkout -b feature/your-feature-name`
3. 提交你的更改：`git commit -m 'feat: 添加xxx功能'`
4. 推送到远程分支：`git push origin feature/your-feature-name`
5. 发起 **Pull Request**

### 代码规范

- **Python**：遵循 PEP 8 规范，使用类型注解
- **TypeScript / Vue**：遵循项目已有的 ESLint 配置
- **Commit Message**：推荐使用 [约定式提交](https://www.conventionalcommits.org/zh-hans/) 格式
  - `feat:` 新功能
  - `fix:` 修复 Bug
  - `docs:` 文档更新
  - `refactor:` 重构
  - `perf:` 性能优化

### 开发注意事项

- 所有涉及 LLM 调用的 Prompt 请统一放在 [prompts.py](backend/app/core/prompts.py) 中管理
- 新增 API 端点请在对应的 endpoint 文件中注册，并在 `main.py` 中挂载路由
- 新增数据库模型请在 `models/__init__.py` 中导入以确保 SQLAlchemy 能识别

---

## 📝 许可证

本项目采用 [MIT License](LICENSE) 协议开源。

---

<p align="center">
  <b>ITQA</b> — 让每一条交通法规都可查、可问、可见，让每一次出行都心中有数。
</p>
