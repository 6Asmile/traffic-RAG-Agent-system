# app/services/rag_service.py 完整替换

import os, shutil, time, logging, re, httpx, redis, json, hashlib, asyncio
from sqlalchemy.orm import Session
from langchain_core.embeddings import Embeddings
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import ChatOpenAI
from app.core.config import settings
from app.services.config_service import ConfigService
from app.services.tool_service import ToolService
from app.core.prompts import INTENT_DISPATCH_PROMPT, RAG_SYSTEM_PROMPT, QUERY_REWRITE_PROMPT

os.environ["ANONYMIZED_TELEMETRY"] = "False"
logger = logging.getLogger("RAGService")


class AliyunEmbeddingWrapper(Embeddings):
    def __init__(self, model, api_key, base_url):
        self.model, self.api_key = model, api_key
        self.url = base_url.replace("/compatible-mode/v1", "/compatible-mode/v1/embeddings")

    def embed_documents(self, texts):
        results = []
        with httpx.Client(timeout=60.0) as client:
            for i in range(0, len(texts), 10):
                batch = [str(t) for t in texts[i: i + 10]]
                resp = client.post(self.url, json={"model": self.model, "input": batch},
                                   headers={"Authorization": f"Bearer {self.api_key}"})
                results.extend([item["embedding"] for item in resp.json()["data"]])
        return results

    def embed_query(self, text): return self.embed_documents([text])[0]


class RAGService:
    def __init__(self, db: Session):
        emb_cfg = ConfigService.get_active_config(db, "embedding")
        llm_cfg = ConfigService.get_active_config(db, "llm")
        self.custom_embeddings = AliyunEmbeddingWrapper(emb_cfg.model_name, emb_cfg.api_key, emb_cfg.base_url)

        # 主对话模型 (Temp=0 保证严谨)
        self.llm = ChatOpenAI(model=llm_cfg.model_name, openai_api_key=llm_cfg.api_key,
                              openai_api_base=llm_cfg.base_url, temperature=0.1, streaming=True)

        # 专门用于改写逻辑的模型 (Temp=0.5 增加灵活性)
        self.rewriter_llm = ChatOpenAI(model=llm_cfg.model_name, openai_api_key=llm_cfg.api_key,
                                       openai_api_base=llm_cfg.base_url, temperature=0.5)

        self.index_path = os.path.abspath(os.path.join(settings.BASE_DIR, "data", "faiss_index"))
        self.vector_db = None
        if os.path.exists(os.path.join(self.index_path, "index.faiss")):
            self.vector_db = FAISS.load_local(self.index_path, self.custom_embeddings,
                                              allow_dangerous_deserialization=True)

        try:
            self.redis_client = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0,
                                            decode_responses=True)
        except:
            self.redis_client = None

    def strict_clean(self, text: str) -> str:
        return re.sub(r'\s+', ' ', "".join(c for c in text if c.isprintable())).strip() if text else ""

    async def chat_stream(self, query: str, session_id: str = "default"):
        print(f"\n{'=' * 20} 收到新请求 {'=' * 20}")
        print(f"用户原话: {query}")

        if not self.vector_db:
            yield json.dumps({"type": "error", "data": "知识库未初始化"})
            return

        query = self.strict_clean(query)

        # --- 1. 获取并限制历史记录 (最近 8 轮 = 16 条消息) ---
        history_key = f"chat_history:{session_id}"
        history_list = []
        if self.redis_client:
            raw_history = self.redis_client.get(history_key)
            if raw_history:
                history_list = json.loads(raw_history)[-16:]  # 取最近16条数据

        # --- 2. 上下文改写 (Context-Aware Query Rewriting) ---
        search_query = query
        if history_list:
            history_text = "\n".join(history_list)
            rewrite_input = QUERY_REWRITE_PROMPT.format(history=history_text, query=query)
            try:
                rewrite_res = self.rewriter_llm.invoke(rewrite_input)
                search_query = rewrite_res.content.strip().replace('"', '')
                print(f"🔍 检索词优化: [{query}] -> [{search_query}]")
            except Exception as e:
                print(f"⚠️ 改写失败: {e}")

        # --- 3. 意图识别 (使用改写后的搜索词) ---
        dispatch_msg = INTENT_DISPATCH_PROMPT.format(query=search_query)
        try:
            intent_res = self.rewriter_llm.invoke(dispatch_msg)
            json_match = re.search(r'\{.*\}', intent_res.content, re.DOTALL)
            intent_obj = json.loads(json_match.group()) if json_match else {"intent": "LEGAL_QUERY"}

            if intent_obj["intent"] == "NAVIGATION":
                p = intent_obj.get("params", {})
                yield json.dumps({"type": "content", "data": f"🔄 **正在为您规划 {p.get('mode', '驾车')} 路线...**\n\n"})
                route_info = await ToolService.get_route_plan(p.get('from'), p.get('to'), p.get('mode', 'driving'))
                yield json.dumps({"type": "content", "data": route_info})
                yield json.dumps({"type": "done"});
                return
            elif intent_obj["intent"] == "CHITCHAT":
                yield json.dumps({"type": "content", "data": "您好！我是您的智能交通助手，您可以问我法规或路线。"})
                yield json.dumps({"type": "done"});
                return
        except:
            pass

        # --- 4. 向量检索 ---
        print(f"📡 正在检索向量库...")
        docs_with_scores = self.vector_db.similarity_search_with_score(search_query, k=6)
        filtered_docs = []
        for doc, score in docs_with_scores:
            print(f"   - 匹配片段 (Score: {score:.4f}): {doc.page_content[:30]}...")
            if score < 0.85: filtered_docs.append(doc)

        if not filtered_docs:
            yield json.dumps({"type": "content", "data": "抱歉，在知识库中未找到相关法律依据。"})
            yield json.dumps({"type": "done"});
            return

        sources = [doc.page_content for doc in filtered_docs]
        yield json.dumps({"type": "sources", "data": sources})

        # --- 5. 最终生成回答 ---
        context = "\n".join([f"[依据{i + 1}]: {d.page_content}" for i, d in enumerate(filtered_docs)])
        history_str = "\n".join(history_list)

        final_prompt = RAG_SYSTEM_PROMPT.format(context=context, history=history_str, query=query)

        print(f"🤖 AI 开始吐字...")
        full_answer = ""
        async for chunk in self.llm.astream(final_prompt):
            if chunk.content:
                full_answer += chunk.content
                yield json.dumps({"type": "content", "data": chunk.content}, ensure_ascii=False)

        # --- 6. 更新 Redis 历史记录 ---
        if self.redis_client:
            history_list.extend([f"用户: {query}", f"助手: {full_answer}"])
            self.redis_client.setex(history_key, 3600, json.dumps(history_list[-16:]))

        print(f"✅ 回答生成完毕\n{'=' * 50}")
        yield json.dumps({"type": "done", "full_answer": full_answer})

    def ingest_pdf(self, file_upload_object, filename: str) -> int:
        upload_path = os.path.join(settings.BASE_DIR, "data", "uploads")
        os.makedirs(upload_path, exist_ok=True)
        file_save_path = os.path.join(upload_path, filename)
        with open(file_save_path, "wb") as buffer:
            shutil.copyfileobj(file_upload_object.file, buffer)
        loader = PyPDFLoader(file_save_path)
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=450, chunk_overlap=100)
        valid_texts = [self.strict_clean(doc.page_content) for doc in text_splitter.split_documents(loader.load()) if
                       len(doc.page_content) > 10]
        if self.vector_db is None:
            self.vector_db = FAISS.from_texts(valid_texts, self.custom_embeddings)
        else:
            self.vector_db.add_texts(valid_texts)
        os.makedirs(self.index_path, exist_ok=True)
        self.vector_db.save_local(self.index_path)
        return len(valid_texts)