# app/core/constants.py

class AmapAPI:
    """高德地图 API 接口地址常量"""
    # 基础路径
    REST_API_BASE = "https://restapi.amap.com/v3"
    URI_API_BASE = "https://uri.amap.com"

    # 1. 基础数据查询接口
    GEOCODE = f"{REST_API_BASE}/geocode/geo"           # 地理编码（地址转坐标）
    PLACE_TEXT = f"{REST_API_BASE}/place/text"         # POI 文本搜索
    WEATHER_INFO = f"{REST_API_BASE}/weather/weatherInfo" # 天气查询

    # 2. 路线规划接口
    DIR_DRIVING = f"{REST_API_BASE}/direction/driving"               # 驾车
    DIR_TRANSIT = f"{REST_API_BASE}/direction/transit/integrated"    # 公交/地铁
    DIR_WALKING = f"{REST_API_BASE}/direction/walking"               # 步行

    # 3. 供前端渲染的 H5 交互式地图 URI
    URI_NAVIGATION = f"{URI_API_BASE}/navigation" # H5 路线导航
    URI_SEARCH = f"{URI_API_BASE}/search"         # H5 周边搜索


class UIConstants:
    """后端生成的 HTML Widget 样式常量"""
    WIDGET_STYLE = (
        "border: 1px solid #e0e0e0; "
        "border-radius: 16px; "
        "overflow: hidden; "
        "margin: 12px 0; "
        "background: #ffffff; "
        "box-shadow: 0 8px 24px rgba(0,0,0,0.12); "
        "display: flex; "
        "flex-direction: column;"
    )

    HEADER_STYLE = (
        "padding: 14px 18px; "
        "background: rgba(255, 255, 255, 0.9); "
        "backdrop-filter: blur(10px); "
        "display: flex; "
        "justify-content: space-between; "
        "align-items: center; "
        "border-bottom: 1px solid #f0f0f0; "
        "z-index: 10;"
    )


class SystemConfig:
    """系统文件路径等全局配置常量"""
    UPLOAD_DIR = "data/uploads"
    FAISS_INDEX_DIR = "data/faiss_index"


class AIModelConstants:
    """AI 模型与接口相关常量"""
    DEEPSEEK_BASE_URL = "https://api.deepseek.com"
    ALIYUN_RERANK_URL = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    ALIYUN_EMBEDDING_PATH = "/compatible-mode/v1/embeddings"
    COMPATIBLE_MODE_PATH = "/compatible-mode/v1"

    DEFAULT_RERANK_MODEL = "gte-rerank"
    DEFAULT_SCORE_THRESHOLD = 0.15

    # 普通极速模式检索参数
    FAST_FAISS_TOP_K = 20
    FAST_BM25_TOP_N = 20
    FAST_FUSION_TOP_N = 30
    FAST_RERANK_TOP_N = 8
    FAST_RRF_K = 60
    FAST_RRF_WEIGHT_FAISS = 0.6
    FAST_RRF_WEIGHT_BM25 = 0.4
    FAST_DYNAMIC_MARGIN = 0.18
    FAST_MIN_KEEP = 3

    # 文本切片配置
    CHUNK_SIZE = 1200
    CHUNK_OVERLAP = 200
    # 阿里云 embedding 批大小上限（DashScope 当前限制 <=10）
    EMBEDDING_BATCH_SIZE = 10
    # 文档解析最低质量分（低于该分值将标记失败，不进入检索索引）
    PARSE_MIN_QUALITY_SCORE = 55
    # PDF 解析最低质量分（PDF 常见字形映射乱码，阈值更严格）
    PDF_PARSE_MIN_QUALITY_SCORE = 65
    # 原始文本候选最低分（路由选优用）
    RAW_TEXT_MIN_ACCEPT_SCORE = 45
    # 原始文本候选中可接受的最大乱码占比（超过则拒绝该批次）
    RAW_TEXT_MAX_GARBLED_RATIO = 0.15
    # PDF 解析批大小（控制内存，避免 OOM）
    PDF_PARSE_BATCH_SIZE = 5
    # 超大 PDF 跳过 Docling（仅文本层+OCR），减少峰值内存
    PDF_DOCLING_MAX_PAGES = 80
    # 当批次文本候选分低于该值时，触发 OCR 兜底
    PDF_OCR_TRIGGER_SCORE = 65


class RedisKeys:
    """Redis 缓存键名常量"""
    CHAT_HISTORY = "chat_history:{user_id}:{session_id}"
    CHAT_SUMMARY = "chat_summary:{user_id}:{session_id}"
    METRICS_TOTAL_QUERIES = "metrics:total_queries"
    METRICS_CACHE_HITS = "metrics:cache_hits"
    HISTORY_EXPIRE_SECONDS = 3600
    MAX_HISTORY_LENGTH = 8  # 历史记录保留的最大条数
    SUMMARY_TRIGGER_TURNS = 6  # 超过该轮数后触发“滚动摘要压缩”
    SUMMARY_KEEP_TURNS = 3  # 压缩后保留的最近轮数

class QuizConstants:
    """题库生成配置常量"""
    DEFAULT_GENERATE_COUNT = 10       # 默认生成的题目数量
    DOC_SAMPLE_LIMIT = 3             # 每次抽取文档的上限，防止上下文过大
    MIN_CHUNK_LENGTH = 100           # 参与出题的有效切片最小长度
    MIN_DIFFICULTY = 3               # 题目最小难度星级
    MAX_DIFFICULTY = 5               # 题目最大难度星级
    OPTIONS_LETTERS =["A", "B", "C", "D"] # 选项标号

class AnalyticsConstants:
    """舆情聚类分析常量"""
    MAX_CLUSTERS = 8                 # KMeans 默认最大聚类簇数
    MIN_TEXT_LENGTH = 5              # 纳入聚类分析的最小文本长度
    MIN_SAMPLES = 10                 # 触发聚类分析的最小问题样本数
    RANDOM_STATE = 42                # 算法随机数种子，保证结果可复现
    N_INIT = 10                      # K-Means 初始化次数

class GraphConstants:
    """知识图谱常量"""
    MIN_TEXT_LENGTH = 20             # 触发图谱提取的最小文本长度
    EXTRACT_CHUNK_LIMIT = 15  #  每次触发图谱提取时，从知识库随机抽取的切片数量上限
