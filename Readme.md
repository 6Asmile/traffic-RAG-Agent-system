# 🚗 ITQA: 智能交通问答与数据分析系统 (V6.0)

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)[![Vue](https://img.shields.io/badge/Vue-3.4-green)](https://vuejs.org/)[![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688)](https://fastapi.tiangolo.com/)[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

ITQA 是一款基于 **RAG (检索增强生成)** 技术构建的现代化智能交通法律咨询平台。它通过深度解析《中华人民共和国道路交通安全法》等权威文档，结合大语言模型的自然语言理解能力，为用户提供精准、专业且有据可查的交通法规建议。

## ✨ 核心功能

*   **🔍 极致精准问答 (RAG)**：采用阿里 `text-embedding-v4` 向量模型与 FAISS 向量库，配合 DeepSeek 大模型，实现无幻觉、带法律原文依据的精准回答。
*   **🧠 上下文理解与改写**：支持多轮对话，系统会自动结合历史聊天记录改写当前问题，确保追问（如“那营运车辆呢？”）也能精准命中法条。
*   **📜 持久化会话管理**：集成 MySQL 存储，仿 ChatGPT 式的侧边栏历史记录，支持多用户隔离。
*   **💎 现代毛玻璃 UI**：基于 Vue3 + Element Plus 打造的高级感界面，支持 Glassmorphism 视觉特效、消息平滑动效及 Markdown 渲染。
*   **👤 用户系统与个性化**：完备的 JWT 登录注册流程、带验证码的安全验证，以及支持自定义上传 PNG/JPG 的个人头像系统。
*   **⚙️ 动态配置加载**：后端采用解耦设计，API Key、模型参数、数据库配置均通过环境变量及数据库动态读取，支持零代码切换大模型供应商。

## 🛠️ 技术架构

### 后端 (Backend)
- **框架**: FastAPI (高性能异步 Web 框架)
- **AI 编排**: LangChain + OpenAI SDK
- **向量数据库**: FAISS (高效向量检索)
- **关系数据库**: MySQL 8.0 (SQLAlchemy ORM)
- **缓存/历史**: Redis
- **安全**: JWT (JSON Web Token) + Bcrypt 密码哈希

### 前端 (Frontend)
- **框架**: Vue 3 (Composition API) + TypeScript
- **构建工具**: Vite
- **UI 组件库**: Element Plus
- **Markdown 渲染**: Markdown-it
- **网络请求**: Axios (带请求/响应拦截器)

## 📂 项目结构

```text
traffic-qa-system/
├── backend/                # FastAPI 后端
│   ├── app/
│   │   ├── api/            # 接口层 (Auth, Chat, User)
│   │   ├── core/           # 核心配置 (Security, Config)
│   │   ├── db/             # 数据库连接与 Session
│   │   ├── models/         # SQLAlchemy 数据库模型
│   │   ├── schemas/        # Pydantic 数据验证模型
│   │   └── services/       # 业务逻辑层 (RAG 核心逻辑)
│   ├── data/               # 上传文件与向量索引存储
│   └── main.py             # 入口文件
├── frontend/               # Vue 3 前端
│   ├── src/
│   │   ├── api/            # Axios 请求封装
│   │   ├── views/          # 页面 (Login, Chat, Profile)
│   │   └── router/         # 路由配置与守卫
│   └── vite.config.ts      # 代理配置
└── .gitignore              # Git 忽略规则
```

## 🚀 快速开始

### 1. 环境准备
确保已安装 Python 3.10+、Node.js 18+、MySQL 8.0 和 Redis。

### 2. 后端配置
```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows 使用 .\venv\Scripts\activate
pip install -r requirements.txt
```
在 `backend` 目录下创建 `.env` 文件：
```ini
# 数据库配置
MYSQL_USER=root
MYSQL_PASSWORD=你的密码
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DB=traffic_qa_db

# Redis
REDIS_HOST=127.0.0.1
REDIS_PORT=6379

# 安全密钥
SECRET_KEY=你的随机长字符串
```

运行初始化脚本：
```bash
python init_db.py       # 创建数据库表
python setup_configs.py # 初始化 AI API 配置
python main.py          # 启动后端
```

### 3. 前端配置
```bash
cd frontend
npm install
npm run dev
```
访问 `http://localhost:5173` 即可开始体验。

## ⚠️ 安全提醒
本仓库的 `.gitignore` 已配置忽略 `.env` 文件。**请务必不要将包含真实 API Key 的环境配置文件上传至任何公开仓库。**

---

**ITQA** - 让交通法律咨询更智能、更简单。