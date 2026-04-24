from app.db.base import Base  # 确保 Base 也在
from app.models.user import User
from app.models.config import AIConfig # <--- 必须在这里导入！！
from app.models.chat import ChatMessage,ChatSession
from app.models.knowledge import KnowledgeBase,KnowledgeDoc
from app.models.analysis import HotTopic
from .graph import GraphNode, GraphEdge
from  .quiz import UserQuizRecord,Question
from .agent_run import AgentRun, AgentRunCheckpoint, AgentMemoryRecord
# 这样以后你再增加新的模型文件（比如 ChatLog），也记得在这里加一行
