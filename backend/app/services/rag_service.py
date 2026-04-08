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

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.messages import HumanMessage, ToolMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from pypdf import PdfReader, PdfWriter
from rank_bm25 import BM25Okapi
from sqlalchemy.orm import Session
from docling.document_converter import DocumentConverter

from app.core.config import settings
from app.core.prompts import RAG_SYSTEM_PROMPT, QUERY_REWRITE_PROMPT, AGENT_SYSTEM_PROMPT
from app.core.constants import SystemConfig, AIModelConstants, RedisKeys  # 🌟 引入常量
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
        # 使用常量替换硬编码
        self.url = base_url.replace(AIModelConstants.COMPATIBLE_MODE_PATH, AIModelConstants.ALIYUN_EMBEDDING_PATH)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        results = []
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        with httpx.Client(timeout=120.0, limits=limits) as client:
            for i in range(0, len(texts), 15):
                batch = [str(t) for t in texts[i: i + 15]]
                payload = {"model": self.model, "input": batch}
                headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        resp = client.post(self.url, json=payload, headers=headers)
                        if resp.status_code != 200:
                            raise Exception(f"Embedding API Error: {resp.status_code} - {resp.text}")
                        data = resp.json()
                        results.extend([item["embedding"] for item in data["data"]])
                        break
                    except Exception as e:
                        logger.warning(f"⚠️ 阿里云 Embedding 请求断开 (尝试 {attempt + 1}/{max_retries}): {e}")
                        if attempt == max_retries - 1:
                            raise Exception(f"Embedding 最终失败: {e}")
                        time.sleep(2)

                time.sleep(0.5)

        return results

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]


class AliyunReranker:
    def __init__(self, api_key, base_url):
        self.api_key = api_key
        # 使用常量替换硬编码
        self.url = AIModelConstants.ALIYUN_RERANK_URL

    def rerank(self, query: str, documents: List[str], top_n: int = 10) -> List[dict]:
        payload = {
            "model": AIModelConstants.DEFAULT_RERANK_MODEL,
            "input": {"query": query, "documents": documents},
            "parameters": {"return_documents": False, "top_n": top_n}
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(self.url, json=payload, headers=headers)

            if resp.status_code == 200:
                data = resp.json()
                if "output" in data and "results" in data["output"]:
                    return data["output"]["results"]
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

        import redis
        try:
            self.redis_client = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0,
                                            decode_responses=True)
        except:
            self.redis_client = None

        self.cache = CacheManager(self.redis_client)

        user_prefs = current_user.ai_preferences if (current_user and current_user.ai_preferences) else {}
        final_llm_model = user_prefs.get("llm_model") or llm_cfg.model_name
        final_llm_key = user_prefs.get("llm_key") or llm_cfg.api_key
        final_emb_model = user_prefs.get("embed_model") or emb_cfg.model_name
        final_emb_key = user_prefs.get("embed_key") or emb_cfg.api_key

        # 使用常量替换硬编码 DeepSeek 网址
        llm_base_url = AIModelConstants.DEEPSEEK_BASE_URL if "deepseek" in final_llm_model else llm_cfg.base_url

        self.custom_embeddings = AliyunEmbeddingWrapper(final_emb_model, final_emb_key, emb_cfg.base_url)
        self.reranker = AliyunReranker(final_emb_key, emb_cfg.base_url)

        self.llm = ChatOpenAI(model=final_llm_model, openai_api_key=final_llm_key, openai_api_base=llm_base_url,
                              temperature=0, streaming=True)
        self.rewriter_llm = ChatOpenAI(model=final_llm_model, openai_api_key=final_llm_key,
                                       openai_api_base=llm_base_url, temperature=0.5)

        # 使用常量替换硬编码索引路径
        self.index_path = os.path.abspath(os.path.join(settings.BASE_DIR, SystemConfig.FAISS_INDEX_DIR))
        self.vector_db = None
        if os.path.exists(os.path.join(self.index_path, "index.faiss")):
            self.vector_db = FAISS.load_local(self.index_path, self.custom_embeddings,
                                              allow_dangerous_deserialization=True)

        self.bm25_instance = None
        self.bm25_corpus = []
        self._init_bm25()

    def strict_clean(self, text: str) -> str:
        if not text: return ""
        text = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', text)
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'(?<=\d)\s+(?=[a-zA-Z])', '', text)
        text = re.sub(r'(?<=[a-zA-Z])\s+(?=/)', '', text)
        text = re.sub(r'(?<=/)\s+(?=[a-zA-Z])', '', text)
        return text.strip()

    def _init_bm25(self):
        try:
            docs = self.db.query(KnowledgeDoc).all()
            all_texts = []
            for doc in docs:
                if doc.parsed_content and isinstance(doc.parsed_content, list):
                    all_texts.extend(doc.parsed_content)
                else:
                    logger.warning(f"文档 {doc.filename} 没有 parsed_content 缓存，已跳过。")

            if all_texts:
                self.bm25_corpus = all_texts
                tokenized_corpus = [list(jieba.cut(text)) for text in all_texts]
                self.bm25_instance = BM25Okapi(tokenized_corpus)
                logger.info(f"⚡ BM25 极速构建完成，共挂载 {len(all_texts)} 条片段")
            else:
                logger.warning("BM25 初始化为空，知识库没有可用文本")
        except Exception as e:
            logger.error(f"BM25 初始化失败: {e}")

    def _process_document_advanced(self, file_path: str, ext: str) -> list:
        print(f"👁️ [解析路由] 启动文档解析: {file_path} (格式: {ext})")
        md_text = ""

        if ext == 'pdf':
            from docling.document_converter import DocumentConverter
            converter = DocumentConverter()

            reader = PdfReader(file_path)
            total_pages = len(reader.pages)
            batch_size = 5

            print(f"📄 PDF 共 {total_pages} 页，将分 {(total_pages // batch_size) + 1} 批进行解析...")

            for i in range(0, total_pages, batch_size):
                writer = PdfWriter()
                for j in range(i, min(i + batch_size, total_pages)):
                    writer.add_page(reader.pages[j])

                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    writer.write(tmp.name)
                    tmp_path = tmp.name

                try:
                    result = converter.convert(tmp_path)
                    md_text += result.document.export_to_markdown() + "\n\n"
                    print(f"   ⏳ 进度：已解析 {min(i + batch_size, total_pages)} / {total_pages} 页")
                finally:
                    if os.path.exists(tmp_path): os.remove(tmp_path)
             # 去除因为物理分页导致的一句话被切断的现象 (结尾没有句号等标点，直接跟着换行)
            md_text = re.sub(r'([^\.。：:；;!！?？>])\n+([^\n#*-])', r'\1\2', md_text)
        elif ext == 'txt':
            print("📄 检测到 TXT 文件，采用极速纯文本读取...")
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    md_text = f.read()
            except UnicodeDecodeError:
                with open(file_path, 'r', encoding='gbk') as f:
                    md_text = f.read()
        else:
            from docling.document_converter import DocumentConverter
            converter = DocumentConverter()
            result = converter.convert(file_path)
            md_text = result.document.export_to_markdown()

        headers_to_split_on = [("#", "章"), ("##", "节"), ("###", "条"), ("####", "款")]
        md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on, strip_headers=False)
        md_splits = md_splitter.split_text(md_text)

        # 使用常量替换硬编码切片参数
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=AIModelConstants.CHUNK_SIZE,
                                                       chunk_overlap=AIModelConstants.CHUNK_OVERLAP)
        final_splits = text_splitter.split_documents(md_splits)

        valid_texts = []
        for split in final_splits:
            hierarchy = [split.metadata[h] for h in ["章", "节", "条", "款"] if h in split.metadata]
            path_str = " > ".join(hierarchy) if hierarchy else "通用正文"

            content = split.page_content.strip()
            if "目 次" in path_str or "........" in content: continue
            content = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', content)

            if len(content) < 15: continue

            clause_match = re.search(r'第[\u4e00-\u9fa5\d]+条', content)
            clause_num = clause_match.group() if clause_match else "明细条款"

            enriched_text = f"【章节】: {path_str} | 【{clause_num}】\n{content}"
            valid_texts.append(enriched_text)

        print(f"✅[解析完成] 共提取 {len(valid_texts)} 个语义富化块。")
        return valid_texts

    def async_process_and_store(self, file_path: str, ext: str, doc_id: int):
        db = SessionLocal()
        try:
            valid_texts = self._process_document_advanced(file_path, ext)
            if not valid_texts:
                logger.warning(f"文档 {doc_id} 未提取到有效文本")
                return

            if self.vector_db is None:
                self.vector_db = FAISS.from_texts(valid_texts, self.custom_embeddings)
            else:
                self.vector_db.add_texts(valid_texts)
            self.vector_db.save_local(self.index_path)

            self.bm25_corpus.extend(valid_texts)
            tokenized_corpus = [list(jieba.cut(text)) for text in self.bm25_corpus]
            self.bm25_instance = BM25Okapi(tokenized_corpus)

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
        # 使用常量替换硬编码路径
        upload_path = os.path.join(settings.BASE_DIR, SystemConfig.UPLOAD_DIR)
        os.makedirs(upload_path, exist_ok=True)
        file_save_path = os.path.join(upload_path, filename)

        with open(file_save_path, "wb") as buffer:
            shutil.copyfileobj(file_upload_object.file, buffer)

        try:
            ext = filename.split('.')[-1].lower()
            valid_texts = self._process_document_advanced(file_save_path, ext)
        except Exception as e:
            logger.error(f"文档解析失败: {e}")
            raise Exception(f"文档解析失败: {str(e)}")

        if not valid_texts:
            raise Exception("文档解析后没有提取到有效语义文本")

        if self.vector_db is None:
            self.vector_db = FAISS.from_texts(valid_texts, self.custom_embeddings)
        else:
            self.vector_db.add_texts(valid_texts)
        self.vector_db.save_local(self.index_path)

        self.bm25_corpus.extend(valid_texts)
        tokenized_corpus = [list(jieba.cut(text)) for text in self.bm25_corpus]
        self.bm25_instance = BM25Okapi(tokenized_corpus)

        return valid_texts

    async def chat_stream(self, query: str, session_id: str = "default"):
        print(f"\n{'=' * 10} [提问] {query} {'=' * 10}")

        if self.redis_client:
            try:
                self.redis_client.incr(RedisKeys.METRICS_TOTAL_QUERIES)
            except:
                pass

        query = self.strict_clean(query)
        q_vec = self.custom_embeddings.embed_query(query)

        # 1. 语义缓存检查 (开发调试期间可注释)
        cached_ans, cached_src = self.cache.get_semantic_cache(q_vec)
        if cached_ans:
            print("⚡ [缓存命中] 直接返回历史答案")
            if self.redis_client:
                try:
                    self.redis_client.incr(RedisKeys.METRICS_CACHE_HITS)
                except:
                    pass
            yield json.dumps({"type": "sources", "data": cached_src})
            yield json.dumps({"type": "content", "data": cached_ans})
            yield json.dumps({"type": "done", "full_answer": cached_ans})
            return

        if not self.vector_db:
            yield json.dumps({"type": "error", "data": "知识库为空"})
            return

        # 2. 获取历史记录并构建 Context
        # 使用常量替换历史记录键名
        history_key = RedisKeys.CHAT_HISTORY.format(session_id=session_id)
        chat_history_objs = []
        history_str_for_rewrite = ""
        history_list = []

        if self.redis_client:
            raw = self.redis_client.get(history_key)
            if raw:
                # 使用常量替换硬编码的数量限制
                history_list = json.loads(raw)[-RedisKeys.MAX_HISTORY_LENGTH:]
                history_str_for_rewrite = "\n".join(
                    [f"{'用户' if i % 2 == 0 else '助手'}: {msg}" for i, msg in enumerate(history_list)])

                for i, msg in enumerate(history_list):
                    if i % 2 == 0:
                        chat_history_objs.append(HumanMessage(content=msg))
                    else:
                        chat_history_objs.append(AIMessage(content=msg))

        search_query = query
        if history_list:
            try:
                rewrite_res = self.rewriter_llm.invoke(
                    QUERY_REWRITE_PROMPT.format(history=history_str_for_rewrite, query=query))
                search_query = rewrite_res.content.strip().replace('"', '')
                print(f"🔍 [改写后] {search_query}")
            except:
                pass

        # 3. 真・Agent 智能体
        try:
            print("🤖 [Agent] 正在思考...")
            agent_system_prompt = SystemMessage(content=AGENT_SYSTEM_PROMPT)
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

                    status_text = ""
                    if "route" in tool_name:
                        status_text = "🔄 **正在规划出行方案...**\n\n"
                    elif "nearby" in tool_name:
                        status_text = "🔄 **正在搜索周边设施...**\n\n"
                    elif "weather" in tool_name:
                        status_text = "🔄 **正在查询实时天气...**\n\n"

                    yield json.dumps({"type": "content", "data": status_text})

                    selected_tool = next(t for t in tools if t.name == tool_name)
                    try:
                        tool_result_raw = await selected_tool.ainvoke(tool_args)
                        tool_res_str = str(tool_result_raw)

                        try:
                            res_dict = json.loads(tool_res_str)
                            if "html_widget" in res_dict and "text_data" in res_dict:
                                yield json.dumps({"type": "content", "data": res_dict["html_widget"] + "\n\n"},
                                                 ensure_ascii=False)
                                messages_for_agent.append(
                                    ToolMessage(content=res_dict["text_data"], tool_call_id=tool_call["id"]))
                                continue
                        except json.JSONDecodeError:
                            pass

                        messages_for_agent.append(ToolMessage(content=tool_res_str, tool_call_id=tool_call["id"]))

                    except Exception as tool_err:
                        messages_for_agent.append(
                            ToolMessage(content=f"工具调用失败: {str(tool_err)}", tool_call_id=tool_call["id"]))

                print("🤖 [Agent] 生成综合建议...")
                full_answer = ""
                async for chunk in self.llm.astream(messages_for_agent):
                    content = chunk.content
                    if content:
                        clean_content = re.sub(r'<\|?DSML\|?.*?>', '', content, flags=re.IGNORECASE)
                        clean_content = re.sub(r'</\|?DSML\|?.*?>', '', clean_content, flags=re.IGNORECASE)
                        if not clean_content.strip(): continue
                        full_answer += clean_content
                        yield json.dumps({"type": "content", "data": clean_content}, ensure_ascii=False)

                if self.redis_client:
                    history_list.extend([f"用户: {query}", f"助手: {full_answer}"])
                    # 使用常量替换时间
                    self.redis_client.setex(history_key, RedisKeys.HISTORY_EXPIRE_SECONDS,
                                            json.dumps(history_list[-RedisKeys.MAX_HISTORY_LENGTH * 2:]))

                yield json.dumps({"type": "done", "full_answer": full_answer})
                return
            else:
                print("🧠 [Agent] 未命中工具，转入法规库检索...")
        except Exception as e:
            print(f"⚠️ Agent 调度异常: {e}")

        # 4. 混合检索
        print("📡 [混合检索] 执行中...")
        faiss_docs = self.vector_db.similarity_search(search_query, k=40)
        bm25_docs = []
        if self.bm25_instance:
            tokenized_query = list(jieba.cut(search_query))
            scores = self.bm25_instance.get_scores(tokenized_query)
            top_n = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:20]
            bm25_docs = [Document(page_content=self.bm25_corpus[i]) for i in top_n if scores[i] > 0]

        candidates = {}
        for d in faiss_docs + bm25_docs:
            if d.page_content not in candidates:
                candidates[d.page_content] = d.page_content
        candidate_list = list(candidates.values())

        print(f"🎯[Rerank] 正在对 {len(candidate_list)} 个片段进行精排...")

        # 5. 重排序与防幻觉截断
        # 使用常量替换硬编码阈值
        final_docs = []
        try:
            rerank_results = self.reranker.rerank(search_query, candidate_list, top_n=10)
            for res in rerank_results:
                score = res.get('relevance_score', -100)
                idx = res.get('index')
                if score < AIModelConstants.DEFAULT_SCORE_THRESHOLD: continue
                final_docs.append(candidate_list[idx])
        except Exception as e:
            print(f"Rerank 异常 (降级): {e}")
            final_docs = candidate_list[:5]

        if not final_docs:
            print("❌ [防幻觉] 所有片段得分均低于阈值，判定为知识库无相关内容。")
            fallback_msg = "抱歉，根据目前的知识库，未找到与您描述完全匹配的交通法规条款。建议您提供更详细的关键词，或咨询当地交管部门。"
            yield json.dumps({"type": "content", "data": fallback_msg})
            yield json.dumps({"type": "done", "full_answer": fallback_msg})
            return

        yield json.dumps({"type": "sources", "data": final_docs})

        # 7. 知识图谱增强
        print("🕸️ [知识图谱] 正在提取逻辑链...")
        try:
            from app.services.graph_service import GraphService
            graph_data = GraphService(self.llm).get_full_graph(self.db)
            relevant_triples = [
                f"{link['source']} -{link['value']}-> {link['target']}"
                for link in graph_data['links']
                if any(k in link['source'] or k in link['target'] for k in jieba.cut(search_query))
            ]
            graph_context = "\n".join(relevant_triples) if relevant_triples else "暂无图谱逻辑关联"
        except Exception as e:
            print(f"知识图谱提取异常: {e}")
            graph_context = "暂无图谱逻辑关联"

        # 8. RAG 回答生成
        context = "\n".join([f"[资料{i + 1}]: {d}" for i, d in enumerate(final_docs)])
        final_prompt = RAG_SYSTEM_PROMPT.format(
            context=context, graph_context=graph_context, history="\n".join(history_list), query=query
        )

        full_answer = ""
        try:
            print("🤖 [AI生成] 开始流式输出...")
            async for chunk in self.llm.astream(final_prompt):
                if chunk.content:
                    full_answer += chunk.content
                    yield json.dumps({"type": "content", "data": chunk.content}, ensure_ascii=False)

            self.cache.set_semantic_cache(q_vec, full_answer, final_docs)
            if self.redis_client:
                history_list.extend([f"用户: {query}", f"助手: {full_answer}"])
                # 使用常量替换
                self.redis_client.setex(history_key, RedisKeys.HISTORY_EXPIRE_SECONDS,
                                        json.dumps(history_list[-RedisKeys.MAX_HISTORY_LENGTH * 2:]))

            yield json.dumps({"type": "done", "full_answer": full_answer})
        except Exception as e:
            print(f"❌ 生成异常: {e}")
            yield json.dumps({"type": "error", "data": str(e)})

