# app/services/cache_service.py

import json
import numpy as np
from redis import Redis


class CacheManager:
    def __init__(self, redis_client: Redis):
        self.redis = redis_client
        # --- 核心修复：在这里定义 self.CACHE_KEY ---
        self.CACHE_KEY = "traffic_semantic_cache"

    def _cosine_similarity(self, v1, v2):
        denominator = np.linalg.norm(v1) * np.linalg.norm(v2)
        if denominator == 0:
            return 0.0
        return float(np.dot(v1, v2) / denominator)

    def get_semantic_cache(self, query_vector: list, threshold=0.96):
        """语义匹配逻辑"""
        if not self.redis:
            return None, None

        # 获取所有缓存项
        all_cache = self.redis.hgetall(self.CACHE_KEY)
        for _, val in all_cache.items():
            try:
                data = json.loads(val)
                cached_vector = data.get("vector")
                if not cached_vector:
                    continue
            except Exception:
                continue
            # 计算当前问题向量与历史缓存向量的相似度
            if self._cosine_similarity(query_vector, cached_vector) > threshold:
                return data.get("answer"), data.get("sources")
        return None, None

    def set_semantic_cache(self, query_vector: list, answer: str, sources: list):
        """存入语义缓存"""
        if not self.redis: return
        data = {
            "vector": query_vector,
            "answer": answer,
            "sources": sources
        }
        # 使用哈希存储，key 为向量的哈希值
        import hashlib
        h = hashlib.md5(str(query_vector).encode()).hexdigest()
        self.redis.hset(self.CACHE_KEY, h, json.dumps(data))
