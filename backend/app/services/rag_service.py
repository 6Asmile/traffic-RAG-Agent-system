# app/services/rag_service.py
import json
import logging
import os
import re
import shutil
import tempfile
import time
from typing import List

import httpx
import jieba
from docling.document_converter import DocumentConverter
# 引入更强的 PDF 解析器
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.messages import HumanMessage, ToolMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from pypdf import PdfReader, PdfWriter
from rank_bm25 import BM25Okapi
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.prompts import RAG_SYSTEM_PROMPT, QUERY_REWRITE_PROMPT
from app.db.session import SessionLocal
from app.models import User
from app.models.knowledge import KnowledgeDoc
from app.services.cache_service import CacheManager
from app.services.config_service import ConfigService
from app.services.tool_service import agent_get_route, agent_search_nearby, agent_get_weather

os.environ["ANONYMIZED_TELEMETRY"] = "False"
logger = logging.getLogger("RAGService")


class AliyunEmbeddingWrapper(Embeddings):
    def __init__(self, model, api_key, base_url):
        self.model, self.api_key = model, api_key
        self.url = base_url.replace("/compatible-mode/v1", "/compatible-mode/v1/embeddings")

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        results = []
        # 加大超时时间，并配置连接池限制
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        with httpx.Client(timeout=120.0, limits=limits) as client:
            for i in range(0, len(texts), 10):
                batch = [str(t) for t in texts[i: i + 10]]
                payload = {"model": self.model, "input": batch}
                headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

                # ==========================================
                # ✨ 核心修复：加入 3 次重试机制与网络退避策略
                # ==========================================
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        resp = client.post(self.url, json=payload, headers=headers)
                        if resp.status_code != 200:
                            raise Exception(f"Embedding API Error: {resp.status_code} - {resp.text}")
                        data = resp.json()
                        results.extend([item["embedding"] for item in data["data"]])
                        break  # 如果成功，跳出重试循环
                    except Exception as e:
                        logger.warning(f"⚠️ 阿里云 Embedding 请求断开 (尝试 {attempt + 1}/{max_retries}): {e}")
                        if attempt == max_retries - 1:
                            raise Exception(f"Embedding 最终失败: {e}")
                        time.sleep(2)  # 失败后休息 2 秒再重试

                # 每发完一批请求，强制让程序休息 0.5 秒，绝不触发阿里云的并发限制！
                time.sleep(0.5)

        return results

    def embed_query(self, text: str) -> List[float]:
        # 查询时因为只有1条，也复用上面的重试逻辑
        return self.embed_documents([text])[0]


# app/services/rag_service.py

class AliyunReranker:
    def __init__(self, api_key, base_url):
        self.api_key = api_key
        # 注意：Rerank 模型通常不需要 /compatible-mode/v1 这种路径，直接对接阿里云 Endpoint
        # 如果你之前的 base_url 是兼容 OpenAI 的，这里可能需要硬编码阿里云的真实地址
        # 或者保留你之前的逻辑，只要能通就行。通常阿里云 Rerank 地址如下：
        self.url = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
        # 如果你的 base_url 已经是 dashscope.aliyuncs.com，保持原样即可

    def rerank(self, query: str, documents: List[str], top_n: int = 10) -> List[dict]:
        """
        返回格式升级：不再只返回索引，而是返回 [{'index': 0, 'score': 0.85}, ...]
        """
        payload = {
            "model": "gte-rerank",  # 或者 "text-rerank-v1" 视你的订阅而定
            "input": {
                "query": query,
                "documents": documents
            },
            "parameters": {
                "return_documents": False,
                "top_n": top_n
            }
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        try:
            # 使用同步客户端，或者在该方法内创建 client
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(self.url, json=payload, headers=headers)

            if resp.status_code == 200:
                data = resp.json()
                # 阿里云返回结构通常在 output.results 里
                if "output" in data and "results" in data["output"]:
                    return data["output"]["results"]  # [{'index': 0, 'relevance_score': 2.5}, ...]

                # 兼容旧版或其他模型返回结构
                return []
            else:
                print(f"Rerank API Error: {resp.text}")
                return []
        except Exception as e:
            print(f"Rerank Exception: {e}")
            return []


class RAGService:
    def __init__(self, db: Session, current_user: User = None):
        self.db = db
        emb_cfg = ConfigService.get_active_config(db, "embedding")
        llm_cfg = ConfigService.get_active_config(db, "llm")
        if not emb_cfg or not llm_cfg:
            raise Exception("AI 配置缺失")

        # 初始化 Redis
        import redis
        try:
            self.redis_client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=0,
                decode_responses=True
            )
        except:
            self.redis_client = None

        self.cache = CacheManager(self.redis_client)

        user_prefs = current_user.ai_preferences if (current_user and current_user.ai_preferences) else {}
        final_llm_model = user_prefs.get("llm_model") or llm_cfg.model_name
        final_llm_key = user_prefs.get("llm_key") or llm_cfg.api_key
        final_emb_model = user_prefs.get("embed_model") or emb_cfg.model_name
        final_emb_key = user_prefs.get("embed_key") or emb_cfg.api_key
        llm_base_url = "https://api.deepseek.com" if "deepseek" in final_llm_model else llm_cfg.base_url

        self.custom_embeddings = AliyunEmbeddingWrapper(final_emb_model, final_emb_key, emb_cfg.base_url)
        self.reranker = AliyunReranker(final_emb_key, emb_cfg.base_url)

        self.llm = ChatOpenAI(
            model=final_llm_model,
            openai_api_key=final_llm_key,
            openai_api_base=llm_base_url,
            temperature=0,
            streaming=True
        )

        self.rewriter_llm = ChatOpenAI(
            model=final_llm_model,
            openai_api_key=final_llm_key,
            openai_api_base=llm_base_url,
            temperature=0.5
        )

        self.index_path = os.path.abspath(os.path.join(settings.BASE_DIR, "data", "faiss_index"))
        self.vector_db = None
        if os.path.exists(os.path.join(self.index_path, "index.faiss")):
            self.vector_db = FAISS.load_local(self.index_path, self.custom_embeddings,
                                              allow_dangerous_deserialization=True)

        self.bm25_instance = None
        self.bm25_corpus = []
        self._init_bm25()

    def strict_clean(self, text: str) -> str:
        """强化清洗：专门针对国标文档的格式问题"""
        if not text: return ""
        # 1. 移除不可见字符
        text = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', text)
        # 2. 合并多余空格
        text = re.sub(r'\s+', ' ', text)
        # 3. 【关键】修复被拆散的单位 (例如 "6 0 k m / h" -> "60km/h")
        text = re.sub(r'(?<=\d)\s+(?=[a-zA-Z])', '', text)  # 修复数字和单位间的空格
        text = re.sub(r'(?<=[a-zA-Z])\s+(?=/)', '', text)  # 修复单位斜杠
        text = re.sub(r'(?<=/)\s+(?=[a-zA-Z])', '', text)
        return text.strip()

    def _init_bm25(self):
        """
        极速初始化：不再读取物理文件，直接从 MySQL 的 parsed_content 字段读取。
        这让系统重启时间从几十分钟缩短到 0.1 秒！
        """
        try:
            docs = self.db.query(KnowledgeDoc).all()
            all_texts = []

            for doc in docs:
                # 检查数据库里有没有缓存好的解析文本
                if doc.parsed_content and isinstance(doc.parsed_content, list):
                    all_texts.extend(doc.parsed_content)
                else:
                    logger.warning(f"文档 {doc.filename} 没有 parsed_content 缓存，已跳过。")

            if all_texts:
                self.bm25_corpus = all_texts
                tokenized_corpus = [list(jieba.cut(text)) for text in all_texts]
                self.bm25_instance = BM25Okapi(tokenized_corpus)
                logger.info(f"⚡ BM25 极速构建完成，共挂载 {len(all_texts)} 条高质量语义片段")
            else:
                logger.warning("BM25 初始化为空，知识库没有可用文本")
        except Exception as e:
            logger.error(f"BM25 初始化失败: {e}")

    def _process_document_advanced(self, file_path: str, ext: str) -> list:
        """
        防 OOM 版文档解析：针对长 PDF 进行【物理分页剥离】处理
        针对 TXT 进行【原生极速读取】绕过视觉模型
        """
        print(f"👁️ [解析路由] 启动文档解析: {file_path} (格式: {ext})")
        md_text = ""

        if ext == 'pdf':
            # === PDF 防爆内存处理：每次只处理 5 页 ===
            from docling.document_converter import DocumentConverter
            converter = DocumentConverter()
            from pypdf import PdfReader, PdfWriter
            import tempfile

            reader = PdfReader(file_path)
            total_pages = len(reader.pages)
            batch_size = 10  # 每次处理5页，完美控制内存

            print(f"📄 PDF 共 {total_pages} 页，将分 {(total_pages // batch_size) + 1} 批进行解析...")

            for i in range(0, total_pages, batch_size):
                writer = PdfWriter()
                # 截取 5 页
                for j in range(i, min(i + batch_size, total_pages)):
                    writer.add_page(reader.pages[j])

                # 写入临时文件
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    writer.write(tmp.name)
                    tmp_path = tmp.name

                try:
                    # 依次将小切片喂给 Docling
                    result = converter.convert(tmp_path)
                    md_text += result.document.export_to_markdown() + "\n\n"
                    print(f"   ⏳ 进度：已解析 {min(i + batch_size, total_pages)} / {total_pages} 页")
                finally:
                    # 阅后即焚，释放硬盘
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)

        elif ext == 'txt':
            # === TXT 文件：直接原生极速读取，不走 Docling 视觉模型 ===
            print("📄 检测到 TXT 文件，采用极速纯文本读取...")
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    md_text = f.read()
            except UnicodeDecodeError:
                # 兼容 Windows 常见的 GBK 编码格式
                with open(file_path, 'r', encoding='gbk') as f:
                    md_text = f.read()

        else:
            # === DOCX / MD 通常不会内存溢出，且格式在允许列表内，交由 Docling 直接解析 ===
            from docling.document_converter import DocumentConverter
            converter = DocumentConverter()
            result = converter.convert(file_path)
            md_text = result.document.export_to_markdown()

        # ----------------------------------------------------
        # 下面是原有的 Markdown 切分与上下文富化逻辑（不变）
        # ----------------------------------------------------
        from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
        headers_to_split_on = [("#", "章"), ("##", "节"), ("###", "条"), ("####", "款")]
        md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on, strip_headers=False)

        # 无论是 PDF 还是 TXT，此时都已经变成了文本字符串，直接切分
        md_splits = md_splitter.split_text(md_text)

        text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=150)
        final_splits = text_splitter.split_documents(md_splits)

        valid_texts = []
        for split in final_splits:
            hierarchy = [split.metadata[h] for h in ["章", "节", "条", "款"] if h in split.metadata]
            path_str = " > ".join(hierarchy) if hierarchy else "通用正文"

            import re
            content = split.page_content.strip()

            # 过滤目录页等污染数据
            if "目 次" in path_str or "........" in content:
                continue

            content = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', content)

            if len(content) < 15: continue

            # 提取条款号（匹配“第一条”“第二条”等）
            clause_match = re.search(r'第[\u4e00-\u9fa5\d]+条', content)
            clause_num = clause_match.group() if clause_match else "明细条款"

            enriched_text = f"【章节】: {path_str} | 【{clause_num}】\n{content}"
            valid_texts.append(enriched_text)

        print(f"✅[解析完成] 共提取 {len(valid_texts)} 个语义富化块。")
        return valid_texts

    def async_process_and_store(self, file_path: str, ext: str, doc_id: int):
        """
        专供后台 Task 调用的异步处理函数
        """
        # 因为在后台运行，必须重新开启数据库连接
        db = SessionLocal()
        try:
            # 1. 深度解析
            valid_texts = self._process_document_advanced(file_path, ext)
            if not valid_texts:
                logger.warning(f"文档 {doc_id} 未提取到有效文本")
                return

            # 2. 存入 FAISS 向量库
            if self.vector_db is None:
                self.vector_db = FAISS.from_texts(valid_texts, self.custom_embeddings)
            else:
                self.vector_db.add_texts(valid_texts)
            self.vector_db.save_local(self.index_path)

            # 3. 热更新 BM25
            self.bm25_corpus.extend(valid_texts)
            import jieba
            from rank_bm25 import BM25Okapi
            tokenized_corpus = [list(jieba.cut(text)) for text in self.bm25_corpus]
            self.bm25_instance = BM25Okapi(tokenized_corpus)

            # 4. 更新数据库记录
            from app.models.knowledge import KnowledgeDoc
            doc = db.query(KnowledgeDoc).filter_by(id=doc_id).first()
            if doc:
                doc.chunk_count = len(valid_texts)
                doc.parsed_content = valid_texts
                db.commit()
                logger.info(f"🎉 异步任务完成！文档ID:{doc_id} 已成功存入 {len(valid_texts)} 个切片。")

        except Exception as e:
            logger.error(f"❌ 异步解析任务崩溃: {e}")
        finally:
            db.close()
    def ingest_knowledge(self, file_upload_object, filename: str) -> list:
        print(f"\n🚀 [知识入库] 接收文件: {filename}")

        upload_path = os.path.join(settings.BASE_DIR, "data", "uploads")
        os.makedirs(upload_path, exist_ok=True)
        file_save_path = os.path.join(upload_path, filename)

        # 保存物理文件
        with open(file_save_path, "wb") as buffer:
            shutil.copyfileobj(file_upload_object.file, buffer)

        try:
            # 调用高级视觉解析管线
            valid_texts = self._process_document_advanced(file_save_path)
        except Exception as e:
            logger.error(f"文档视觉解析失败: {e}")
            raise Exception(f"文档解析失败: {str(e)}")

        if not valid_texts:
            raise Exception("文档解析后没有提取到有效语义文本")

        # 存入 FAISS 向量库
        if self.vector_db is None:
            self.vector_db = FAISS.from_texts(valid_texts, self.custom_embeddings)
        else:
            self.vector_db.add_texts(valid_texts)

        self.vector_db.save_local(self.index_path)

        # 重新热加载 BM25 (直接使用刚才解析出的 texts，不用再查数据库)
        self.bm25_corpus.extend(valid_texts)
        tokenized_corpus = [list(jieba.cut(text)) for text in self.bm25_corpus]
        self.bm25_instance = BM25Okapi(tokenized_corpus)

        # 返回文本列表，供 chat.py 存入 MySQL
        return valid_texts

    async def chat_stream(self, query: str, session_id: str = "default"):
        print(f"\n{'=' * 10} [提问] {query} {'=' * 10}")
        query = self.strict_clean(query)
        q_vec = self.custom_embeddings.embed_query(query)

        # 1. 语义缓存检查   //开发状态可以先注释掉 正常部署使用时弄回来
        # cached_ans, cached_src = self.cache.get_semantic_cache(q_vec)
        # if cached_ans:
        #     print("⚡ [缓存命中] 直接返回历史答案")
        #     yield json.dumps({"type": "sources", "data": cached_src})
        #     yield json.dumps({"type": "content", "data": cached_ans})
        #     yield json.dumps({"type": "done", "full_answer": cached_ans})
        #     return

        if not self.vector_db:
            yield json.dumps({"type": "error", "data": "知识库为空"})
            return

        # 2. 获取历史记录并构建 Context (关键！)
        history_key = f"chat_history:{session_id}"
        chat_history_objs = [] # 用于给 Agent 的上下文对象列表
        history_str_for_rewrite = ""

        if self.redis_client:
            raw = self.redis_client.get(history_key)
            if raw:
                raw_list = json.loads(raw)[-6:] # 取最近6条
                history_str_for_rewrite = "\n".join([f"{'用户' if i % 2 == 0 else '助手'}: {msg}" for i, msg in enumerate(raw_list)])

                # 【关键】将历史记录转化为 LangChain Message 对象
                for i, msg in enumerate(raw_list):
                    if i % 2 == 0:
                        chat_history_objs.append(HumanMessage(content=msg))
                    else:
                        chat_history_objs.append(AIMessage(content=msg))

        # 2. 查询改写 (Query Rewriting)
        history_key = f"chat_history:{session_id}"
        history_list =[]
        if self.redis_client:
            raw = self.redis_client.get(history_key)
            if raw: history_list = json.loads(raw)[-6:]  # 取最近6条

        search_query = query
        if history_list:
            history_str = "\n".join([f"{'用户' if i % 2 == 0 else '助手'}: {msg}" for i, msg in enumerate(history_list)])
            try:
                # 快速改写，不流式输出
                rewrite_res = self.rewriter_llm.invoke(QUERY_REWRITE_PROMPT.format(history=history_str, query=query))
                search_query = rewrite_res.content.strip().replace('"', '')
                print(f"🔍 [改写后] {search_query}")
            except:
                pass

        # 3. 意图识别与工具调用 (Tool Calling)
        try:
            print("🤖 [Agent] 正在思考...")

            # 定义 System Prompt，强行压制模型输出 XML 标签
            agent_system_prompt = SystemMessage(content="""
            你是一个集成了高德地图能力的智能交通助手。
            
            【核心指令】
            1. 当用户询问路线、地点、天气时，**必须**调用工具。
            2. 工具调用完成后，请根据工具返回的数据，用**自然语言**生成一段温馨、有用的建议。
            3. **严禁**输出任何 XML标签、JSON代码块或 `< | DSML | ... >` 这样的调试信息。只输出给用户看的最终文字。
            4. 如果工具返回了图片链接，请不要在回答中重复生成该链接，以免图片显示两次。
            """)

            tools = [agent_get_route, agent_search_nearby, agent_get_weather]
            llm_with_tools = self.rewriter_llm.bind_tools(tools)

            messages_for_agent = [agent_system_prompt] + chat_history_objs + [HumanMessage(content=search_query)]

            agent_msg = llm_with_tools.invoke(messages_for_agent)

            if agent_msg.tool_calls:
                print(f"🛠️ [Agent] 命中工具: {[t['name'] for t in agent_msg.tool_calls]}")
                messages_for_agent.append(agent_msg)

                for tool_call in agent_msg.tool_calls:
                    tool_name = tool_call["name"]
                    tool_args = tool_call["args"]

                    # 给前端发送状态
                    status_text = ""
                    if "route" in tool_name: status_text = "🔄 **正在规划出行方案...**\n\n"
                    elif "nearby" in tool_name: status_text = "🔄 **正在搜索周边设施...**\n\n"
                    elif "weather" in tool_name: status_text = "🔄 **正在查询实时天气...**\n\n"

                    yield json.dumps({"type": "content", "data": status_text})

                    # 执行工具
                    selected_tool = next(t for t in tools if t.name == tool_name)
                    try:
                        tool_result = await selected_tool.ainvoke(tool_args)
                    except Exception as tool_err:
                        tool_result = f"工具调用失败: {str(tool_err)}"

                    # 将工具结果加入历史
                    messages_for_agent.append(ToolMessage(content=str(tool_result), tool_call_id=tool_call["id"]))

                    # 💡 关键优化：如果工具返回了 Markdown 图片，我们已经通过上面的 yield 发给前端渲染了
                    # 为了防止大模型在总结时又把图片链接复述一遍（导致裂图或重复），我们在喂给大模型前，把 URL 稍微“打码”一下
                    # 或者依靠 System Prompt 指令（第4条）

                # 6. 生成最终回答 (带清洗过滤器)
                print("🤖 [Agent] 生成综合建议...")
                full_answer = ""

                # 重新流式调用 LLM 生成总结
                async for chunk in self.llm.astream(messages_for_agent):
                    content = chunk.content
                    if content:
                        # 🔥🔥🔥 【核心修复】 过滤阿里模型的 DSML 脏数据 🔥🔥🔥
                        # 只要包含这些特征字符，直接跳过不发送给前端
                        if "< | DSML" in content or "function_calls" in content or "| >" in content:
                            continue

                        # 简单的清洗，防止多余的换行
                        full_answer += content
                        yield json.dumps({"type": "content", "data": content}, ensure_ascii=False)

                yield json.dumps({"type": "done", "full_answer": full_answer})
                return
            else:
                print("🧠 [Agent] 未命中工具，转入法规库检索...")

        except Exception as e:
            print(f"⚠️ Agent 调度异常: {e}")

        # 4. 混合检索 (暴力扩大范围，确保查得全)
        print("📡 [混合检索] 执行中...")

        # 4.1 向量检索：扩大到 Top 40
        faiss_docs = self.vector_db.similarity_search(search_query, k=40)

        # 4.2 关键词检索：扩大到 Top 20
        bm25_docs =[]
        if self.bm25_instance:
            tokenized_query = list(jieba.cut(search_query))
            scores = self.bm25_instance.get_scores(tokenized_query)
            top_n = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:20]
            bm25_docs = [Document(page_content=self.bm25_corpus[i]) for i in top_n if scores[i] > 0]

        # 4.3 合并去重
        candidates = {}
        for d in faiss_docs + bm25_docs:
            if d.page_content not in candidates:
                candidates[d.page_content] = d.page_content

        candidate_list = list(candidates.values())
        print(f"🎯[Rerank] 正在对 {len(candidate_list)} 个片段进行精排...")

        # 5. 重排序与防幻觉截断 (Anti-Hallucination)
        # 这里设置为 0.05 是一个平衡值，既能拦住完全不相关的噪音，又能放过字面上有一定关联的法条
        SCORE_THRESHOLD = 0.05

        final_docs =[]

        try:
            # 获取带分数的排序结果
            rerank_results = self.reranker.rerank(search_query, candidate_list, top_n=10)

            for res in rerank_results:
                score = res.get('relevance_score', -100)
                idx = res.get('index')

                # 如果片段与问题相关度太低，直接丢弃
                if score < SCORE_THRESHOLD:
                    continue

                final_docs.append(candidate_list[idx])

        except Exception as e:
            print(f"Rerank 异常 (降级为普通截取): {e}")
            final_docs = candidate_list[:5]

        # 6. 最终判定：如果没有合格的文档，直接拒答
        if not final_docs:
            print("❌ [防幻觉] 所有片段得分均低于阈值，判定为知识库无相关内容。")
            fallback_msg = "抱歉，根据目前的知识库，未找到与您描述完全匹配的交通法规条款。建议您提供更详细的关键词，或咨询当地交管部门。"
            yield json.dumps({"type": "content", "data": fallback_msg})
            yield json.dumps({"type": "done", "full_answer": fallback_msg})
            return

        yield json.dumps({"type": "sources", "data": final_docs})

        # 7. 知识图谱增强 (Knowledge Graph Integration)
        print("🕸️ [知识图谱] 正在提取逻辑链...")
        try:
            from app.services.graph_service import GraphService
            graph_data = GraphService(self.llm).get_full_graph(self.db)

            # 使用 jieba 分词对问题进行关键词提取，去图谱里撞库
            relevant_triples = [
                f"{link['source']} -{link['value']}-> {link['target']}"
                for link in graph_data['links']
                if any(k in link['source'] or k in link['target'] for k in jieba.cut(search_query))
            ]
            graph_context = "\n".join(relevant_triples) if relevant_triples else "暂无图谱逻辑关联"
        except Exception as e:
            print(f"知识图谱提取异常: {e}")
            graph_context = "暂无图谱逻辑关联"

        # 8. 拼装 Prompt 并生成最终流式回答
        context = "\n".join([f"[资料{i + 1}]: {d}" for i, d in enumerate(final_docs)])

        # 组装最终提示词
        final_prompt = RAG_SYSTEM_PROMPT.format(
            context=context,
            graph_context=graph_context,
            history="\n".join(history_list),
            query=query
        )

        full_answer = ""
        try:
            print("🤖 [AI生成] 开始流式输出...")
            async for chunk in self.llm.astream(final_prompt):
                if chunk.content:
                    full_answer += chunk.content
                    yield json.dumps({"type": "content", "data": chunk.content}, ensure_ascii=False)

            # 存入语义缓存
            self.cache.set_semantic_cache(q_vec, full_answer, final_docs)
            # 存入历史会话 (保留最近16条用于上下文改写)
            if self.redis_client:
                history_list.extend([f"用户: {query}", f"助手: {full_answer}"])
                self.redis_client.setex(history_key, 3600, json.dumps(history_list[-16:]))

            yield json.dumps({"type": "done", "full_answer": full_answer})

        except Exception as e:
            print(f"❌ 生成异常: {e}")
            yield json.dumps({"type": "error", "data": str(e)})