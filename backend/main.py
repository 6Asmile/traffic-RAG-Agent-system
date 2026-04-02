import os
# 强行关闭 HuggingFace 的符号链接功能，解决 WinError 1314 报错
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS"] = "1"
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

from app.api.endpoints import test, chat, auth, quiz, admin, agentic
from app.core.config import settings

app = FastAPI(title=settings.PROJECT_NAME)

UPLOAD_DIR = "data/uploads/avatars"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory="data/uploads"), name="static")

# --- 1. 配置跨域 ---
origins = [
    "http://localhost:5173",    # Vue默认开发端口
    "http://127.0.0.1:5173",
    "http://localhost:3000",    # 其他常见前端端口
    "*"                         # 开发环境可以先设为 * 允许所有，生产环境需改回具体域名
]

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=origins,      # 允许这些来源访问
#     allow_credentials=True,
#     allow_methods=["*"],        # 允许所有方法 (GET, POST等)
#     allow_headers=["*"],        # 允许所有请求头
# )
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # 开发阶段最稳妥的方法
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 2. 注册路由 ---
# 注意：chat 路由的 prefix 是 /api/v1/chat
app.include_router(auth.router, prefix="/api/v1/auth", tags=["认证"])
app.include_router(chat.router, prefix="/api/v1/chat", tags=["智能问答"])
app.include_router(test.router, prefix="/api/v1/test", tags=["测试"]) # 建议改为 /v1/test
app.include_router(quiz.router, prefix="/api/v1/quiz", tags=["模拟考试"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["后台管理"])
app.include_router(agentic.router,prefix="/api/v1/agentic",tags=["Agentic专家模式"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)