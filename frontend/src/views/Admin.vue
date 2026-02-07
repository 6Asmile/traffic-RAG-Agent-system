<template>
  <div class="admin-page">
    <div class="bg-glow"></div>
    <div class="glass-card custom-scrollbar">
      <!-- 1. 标题与上传区域：适配移动端自动换行 -->
      <header class="admin-header">
        <div class="title-section">
          <el-icon :size="28" color="#409eff"><Collection /></el-icon>
          <h2>知识库管理</h2>
        </div>
        <el-upload 
          action="/api/v1/chat/upload" 
          :headers="headers" 
          :on-success="onUploadSuccess" 
          :show-file-list="false"
          class="upload-btn-wrapper"
        >
          <el-button type="primary" :icon="Plus" round>上传法规</el-button>
        </el-upload>
      </header>

      <!-- 2. 知识库列表表格：增加高度限制并允许内部滚动 -->
      <div class="table-wrapper">
        <el-table :data="docs" style="width: 100%" class="modern-table" size="small">
          <el-table-column prop="filename" label="文件名" min-width="180" show-overflow-tooltip />
          <el-table-column prop="chunk_count" label="切片" width="60" align="center" />
          <!-- 移动端隐藏上传时间列以节省空间，或者缩窄 -->
          <el-table-column prop="upload_time" label="日期" width="100" align="center" class-name="hidden-xs">
            <template #default="scope">
              {{ formatShortDate(scope.row.upload_time) }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="80" align="center">
            <template #default="scope">
              <el-button type="danger" link @click="handleDelete(scope.row)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 3. 数据分析看板区域：使用响应式栅格 -->
      <div class="analytics-dashboard">
        <el-divider content-position="left">
          <el-icon><PieChart /></el-icon> 热点分析
        </el-divider>

        <el-row :gutter="20">
          <!-- 左侧：ECharts 环形图。在手机上占 24 格（全宽），电脑上占 10 格 -->
          <el-col :xs="24" :sm="10">
            <div class="inner-card chart-box">
              <div ref="chartRef" class="echarts-container"></div>
            </div>
          </el-col>

          <!-- 右侧：AI 热点话题列表。在手机上占 24 格，电脑上占 14 格 -->
          <el-col :xs="24" :sm="14">
            <div class="inner-card topic-list">
              <h3>🔥 智能识别热点话题</h3>
              <el-scrollbar height="300px">
                <div v-if="hotTopics.length === 0" class="no-data">暂无分析数据</div>
                <div v-for="item in hotTopics" :key="item.id" class="topic-item">
                  <div class="topic-header">
                    <span class="topic-title">{{ item.topic_name }}</span>
                    <el-tag size="small" type="danger" effect="dark">{{ item.hit_count }} 次</el-tag>
                  </div>
                  <div class="topic-keywords">
                    <el-tag v-for="k in item.keywords" :key="k" size="small" effect="plain" class="k-tag"># {{ k }}</el-tag>
                  </div>
                  <div class="topic-preview">
                    <p v-for="(q, idx) in item.representative_queries" :key="idx">“ {{ q }} ”</p>
                  </div>
                </div>
              </el-scrollbar>
            </div>
          </el-col>
        </el-row>

        <div class="action-bar">
          <el-button type="primary" :loading="analyzing" @click="startAnalysis" round>
            <el-icon v-if="!analyzing"><Refresh /></el-icon> 重新分析
          </el-button>
        </div>
      </div>

      <!-- 4. 底部状态栏 -->
      <footer class="admin-footer">
        <div class="stat-item">
          <span class="label">总切片:</span>
          <span class="value">{{ totalChunks }}</span>
          <!-- 修改跳转路径 -->
  
        </div>
        <el-button @click="$router.push('/')" link :icon="HomeFilled">返回系统首页</el-button>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed, nextTick } from 'vue';
import { Plus, Collection, Delete, ArrowLeft, PieChart, Refresh } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { HomeFilled } from '@element-plus/icons-vue';
import request from '../api/request';
import * as echarts from 'echarts';

// --- 类型接口定义 ---
interface DocItem {
  id: number;
  filename: string;
  chunk_count: number;
  upload_time: string;
}

interface HotTopic {
  id: number;
  topic_name: string;
  keywords: string[];
  hit_count: number;
  representative_queries: string[];
}

// --- 响应式变量 ---
const docs = ref<DocItem[]>([]);
const hotTopics = ref<HotTopic[]>([]);
const chartRef = ref<HTMLElement | null>(null);
const analyzing = ref(false);
const headers = { Authorization: `Bearer ${localStorage.getItem('access_token')}` };

// --- ECharts 初始化 ---
const initChart = (data: { topic: string; count: number }[]) => {
  if (!chartRef.value || !data.length) return;
  
  const myChart = echarts.init(chartRef.value);
  const option = {
    title: { text: '咨询热点分布', left: 'center', textStyle: { fontSize: 14 } },
    tooltip: { trigger: 'item', formatter: '{b}: {c}' },
    series: [
      {
        type: 'pie',
        radius: ['40%', '70%'],
        avoidLabelOverlap: false,
        itemStyle: { borderRadius: 10, borderColor: '#fff', borderWidth: 2 },
        label: { show: false },
        data: data.map(item => ({ value: item.count, name: item.topic }))
      }
    ]
  };
  myChart.setOption(option);
  window.addEventListener('resize', () => myChart.resize());
};

// --- API 调用 ---
const fetchHotTopics = async () => {
  try {
    const res = await request.get('/v1/chat/analytics');
    hotTopics.value = res.data;
    if (hotTopics.value.length > 0) {
      await nextTick();
      initChart(hotTopics.value.map(i => ({ topic: i.topic_name, count: i.hit_count })));
    }
  } catch (e) {
    console.error("加载分析数据失败");
  }
};

const fetchDocs = async () => {
  try {
    const res = await request.get('/v1/chat/knowledge_list');
    docs.value = res.data;
    await fetchHotTopics();
  } catch (e) {
    ElMessage.error('获取知识库列表失败');
  }
};

const startAnalysis = async () => {
  analyzing.value = true;
  try {
    await request.post('/v1/chat/perform_analysis');
    ElMessage.success('分析完成');
    await fetchHotTopics();
  } catch (e) {
    ElMessage.error('分析失败');
  } finally {
    analyzing.value = false;
  }
};

const onUploadSuccess = () => {
  ElMessage.success('上传成功');
  fetchDocs();
};

const handleDelete = async (row: DocItem) => {
  try {
    await ElMessageBox.confirm(`确定删除 [${row.filename}] 吗？`, '警告', { type: 'warning' });
    await request.delete(`/v1/chat/knowledge/${row.id}`);
    ElMessage.success('删除成功');
    fetchDocs();
  } catch (e) {}
};

// --- 辅助计算 ---
const totalChunks = computed(() => docs.value.reduce((acc, cur) => acc + cur.chunk_count, 0));
const formatShortDate = (t: string) => {
  const d = new Date(t);
  return `${d.getMonth()+1}-${d.getDate()}`;
};

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
  width: 95%; 
  max-width: 1100px; 
  height: 94%; 
  background: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(20px); 
  border-radius: 24px; 
  border: 1px solid rgba(255,255,255,0.5);
  display: flex; 
  flex-direction: column; 
  padding: 20px; 
  box-shadow: 0 20px 50px rgba(0,0,0,0.05);
  overflow-y: auto;

  @media (max-width: 768px) {
    width: 100%;
    height: 100%;
    border-radius: 0;
    padding: 15px;
  }
}

.admin-header {
  display: flex; 
  justify-content: space-between; 
  align-items: center; 
  margin-bottom: 20px;

  @media (max-width: 768px) {
    flex-direction: column;
    align-items: flex-start;
    gap: 15px;
    .upload-btn-wrapper { width: 100%; .el-button { width: 100%; } }
  }

  .title-section { display: flex; align-items: center; gap: 10px; h2 { margin: 0; font-size: 20px; } }
}

.table-wrapper { 
  margin-bottom: 20px; 
  /* 解决移动端表格溢出 */
  :deep(.el-table__inner-wrapper) { overflow-x: auto; }
}

.analytics-dashboard {
  .inner-card {
    background: rgba(255,255,255,0.5); border-radius: 16px; padding: 15px; margin-bottom: 20px;
    border: 1px solid rgba(0,0,0,0.03);
  }
  
  .echarts-container {
    height: 300px; width: 100%;
    @media (max-width: 768px) { height: 250px; }
  }

  .no-data { text-align: center; color: #999; padding: 20px; }
  .action-bar { text-align: center; margin-bottom: 20px; }
}

.topic-item {
  background: #fff; padding: 12px; border-radius: 12px; margin-bottom: 10px;
  .topic-header { 
    display: flex; justify-content: space-between; align-items: center;
    .topic-title { font-weight: bold; color: #333; font-size: 14px; }
  }
  .topic-keywords { margin: 6px 0; display: flex; flex-wrap: wrap; gap: 4px; }
  .topic-preview { 
    font-size: 11px; color: #888; font-style: italic; 
    p { margin: 2px 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  }
}

.admin-footer {
  margin-top: auto; padding-top: 15px; border-top: 1px solid rgba(0,0,0,0.05);
  display: flex; justify-content: space-between; align-items: center;
  .stat-item {
    font-size: 13px; color: #666;
    .value { font-weight: bold; color: #409eff; margin-left: 5px; }
  }
}

/* 移动端隐藏特定列 */
@media (max-width: 600px) {
  .hidden-xs { display: none !important; }
}

.custom-scrollbar::-webkit-scrollbar { width: 4px; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.1); border-radius: 10px; }
</style>