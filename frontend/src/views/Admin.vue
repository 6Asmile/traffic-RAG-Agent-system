<template>
  <div class="admin-page">
    <div class="bg-glow"></div>
    <div class="glass-card">
      <header class="admin-header">
        <div class="title-section">
          <el-icon :size="28" color="#409eff"><Collection /></el-icon>
          <h2>交通法知识库管理</h2>
        </div>
        <el-upload 
          action="/api/v1/chat/upload" 
          :headers="headers" 
          :on-success="onUploadSuccess" 
          :show-file-list="false"
        >
          <el-button type="primary" :icon="Plus" round>上传 PDF 法律法规</el-button>
        </el-upload>
      </header>

      <div class="table-wrapper custom-scrollbar">
        <el-table :data="docs" style="width: 100%" class="modern-table">
          <el-table-column prop="filename" label="文件名" min-width="250" />
          <el-table-column prop="chunk_count" label="切片数量" width="120" align="center" />
          <el-table-column prop="upload_time" label="上传时间" width="200" align="center">
            <template #default="scope">
              {{ formatTime(scope.row.upload_time) }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120" align="center">
            <template #default="scope">
              <el-button type="danger" link @click="handleDelete(scope.row)">
                <el-icon><Delete /></el-icon> 清空索引
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
      
      <div class="admin-footer">
        <el-statistic title="系统总切片数" :value="totalChunks" />
        <el-button @click="$router.back()" link :icon="ArrowLeft">返回聊天</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { Plus, Collection, Delete, ArrowLeft } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import request from '../api/request';

interface DocItem {
  id: number;
  filename: string;
  chunk_count: number;
  upload_time: string;
}

const docs = ref<DocItem[]>([]);
const headers = { Authorization: `Bearer ${localStorage.getItem('access_token')}` };

const fetchDocs = async () => {
  try {
    const res = await request.get('/v1/chat/knowledge_list');
    docs.value = res.data;
  } catch (e) {
    ElMessage.error('获取列表失败');
  }
};

const onUploadSuccess = () => {
  ElMessage.success('文件处理成功！知识库已更新');
  fetchDocs();
};

const handleDelete = async (row: DocItem) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除 [${row.filename}] 及其相关的向量索引吗？此操作不可逆。`,
      '警告',
      { type: 'warning', confirmButtonText: '确定删除', cancelButtonText: '取消' }
    );
    
    // 注意：后端需要对应实现这个 DELETE 接口
    await request.delete(`/v1/chat/knowledge/${row.id}`);
    ElMessage.success('索引删除成功');
    fetchDocs();
  } catch (e) {
    // 用户取消或请求失败
  }
};

const totalChunks = computed(() => docs.value.reduce((acc, cur) => acc + cur.chunk_count, 0));

const formatTime = (t: string) => new Date(t).toLocaleString();

onMounted(fetchDocs);
</script>

<style scoped lang="scss">
.admin-page {
  height: 100vh; width: 100vw; display: flex; justify-content: center; align-items: center;
  background: #f0f2f5; position: relative; overflow: hidden;
}
.bg-glow {
  position: absolute; width: 600px; height: 600px; background: rgba(64, 158, 255, 0.1);
  filter: blur(100px); top: -200px; left: -200px;
}
.glass-card {
  width: 900px; height: 80%; background: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(20px); border-radius: 24px; border: 1px solid rgba(255,255,255,0.5);
  display: flex; flex-direction: column; padding: 40px; box-shadow: 0 20px 50px rgba(0,0,0,0.05);
}
.admin-header {
  display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px;
  .title-section { display: flex; align-items: center; gap: 15px; h2 { margin: 0; font-size: 24px; } }
}
.table-wrapper { flex: 1; overflow-y: auto; margin-bottom: 20px; }
.modern-table {
  background: transparent !important;
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
}
.admin-footer {
  display: flex; justify-content: space-between; align-items: center;
  border-top: 1px solid rgba(0,0,0,0.05); padding-top: 20px;
}
.custom-scrollbar::-webkit-scrollbar { width: 5px; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.1); border-radius: 10px; }
</style>