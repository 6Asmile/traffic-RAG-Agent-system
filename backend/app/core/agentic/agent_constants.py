# app/core/agentic/agent_constants.py

class AgentLimits:
    """Agent 运行限制常量"""
    MAX_RETRIES = 3  # 最大重试次数，硬性防止无限死循环

class NodeNames:
    """LangGraph 节点名称常量"""
    AGENT = "agent_decision_node"
    ACTION = "tool_execution_node"
    GRADE_DOCS = "document_grading_node"
    REWRITE = "query_rewriting_node"
    RETRIEVE_RETRY = "retrieve_retry_node"  # 专用于重试的检索节点
    GENERATE = "generation_node"
    GRADE_HALLUCINATION = "hallucination_grading_node"

class GraderThresholds:
    """裁判模型打分常量"""
    SCORE_YES = "yes"
    SCORE_NO = "no"

class UIEventTypes:
    """流式输出给前端的事件类型常量"""
    THINKING = "正在规划思考路径..."
    ROUTING_TOOL = "正在调用外部生态工具..."
    RETRIEVING = "正在检索交通法规知识库..."
    GRADING_DOCS = "正在对检索结果进行相关性评估..."
    REWRITING = "检索结果相关性不足，正在反思并重写搜索词..."
    GENERATING = "正在生成最终法律建议..."
    CHECKING_HALLUCINATION = "正在进行防幻觉审查..."
    REJECTED = "触发安全防线，已拦截不合规输出。"

class AgentToolNames:
    """Agent 工具名称常量"""
    LAW_SEARCH = "search_traffic_law_database"
    MAP_ROUTE = "expert_get_route"
    MAP_NEARBY = "expert_search_nearby"
    MAP_WEATHER = "expert_get_weather"



class AgentToolDesc:
    """Agent 工具系统提示词与参数描述常量"""
    LAW_SEARCH = "【必须调用】当需要解答交通法规、罚款、扣分、事故责任划分、道路标志标线等专业法律问题时调用。"
    LAW_SEARCH_QUERY = "需要在交通法规数据库中检索的具体法律关键词或自然语言短句。"

    MAP_ROUTE = "【必须调用】当用户需要：路线规划、导航、从A地到B地怎么走、查询距离或预计耗时、打车费用。"
    MAP_ROUTE_ORIGIN = "出发地名称"
    MAP_ROUTE_DEST = "目的地名称"
    MAP_ROUTE_MODE = "出行方式，可选值: driving (驾车/打车), transit (公交/地铁/火车), walking (步行)"

    MAP_NEARBY = "【必须调用】当用户寻找附近、周边的具体设施（如停车场、充电桩、加油站等）时调用。"
    MAP_NEARBY_KEYWORD = "搜索的地点关键词，如'充电桩'"
    MAP_NEARBY_CITY = "城市名称，默认为'全国'"

    MAP_WEATHER = "【必须调用】当用户询问天气预报、路况天气影响或询问穿衣/出行建议时调用。"
    MAP_WEATHER_CITY = "城市名称"

class RAGToolConfig:
    """RAG 底层参数常量"""
    FAISS_TOP_K = 40
    BM25_TOP_N = 20
    RERANK_TOP_N = 10
    SCORE_THRESHOLD = 0.05
    FALLBACK_MESSAGE = "抱歉，法律知识库中未检索到相关条款，请尝试更换关键词重新检索。"