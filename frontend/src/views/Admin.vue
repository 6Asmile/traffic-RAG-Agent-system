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

      <!-- 图表区域 -->
      <div class="charts-section">
        <el-divider content-position="left">
          <el-icon><PieChart /></el-icon> 用户提问热点分析 (K-Means)
        </el-divider>
        <!-- 使用 ref 绑定 DOM -->
        <div ref="chartRef" class="chart-container"></div>
      </div>
      
      <div class="admin-footer">
        <el-statistic title="系统总切片数" :value="totalChunks" />
        <el-button @click="$router.back()" link :icon="ArrowLeft">返回聊天</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed, nextTick } from 'vue';
import { Plus, Collection, Delete, ArrowLeft, PieChart } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import request from '../api/request';
import * as echarts from 'echarts';

interface DocItem {
  id: number;
  filename: string;
  chunk_count: number;
  upload_time: string;
}

const docs = ref<DocItem[]>([]);
const chartRef = ref<HTMLElement | null>(null);
const headers = { Authorization: `Bearer ${localStorage.getItem('access_token')}` };

// --- 1. 先定义 initChart (或者使用 function 关键字定义) ---
const initChart = (data: any[]) => {
  if (!chartRef.value || !data || data.length === 0) return;
  
  const myChart = echarts.init(chartRef.value);
  const option = {
    title: { text: '近期咨询热点分布', left: 'center', textStyle: { fontSize: 16, color: '#333' } },
    tooltip: { trigger: 'item', formatter: '{a} <br/>{b}: {c} ({d}%)' },
    legend: { orient: 'vertical', left: 'left', top: 'center' },
    series: [
      {
        name: '提问频次',
        type: 'pie',
        radius: ['40%', '70%'],
        avoidLabelOverlap: false,
        itemStyle: { borderRadius: 10, borderColor: '#fff', borderWidth: 2 },
        label: { show: false, position: 'center' },
        emphasis: { label: { show: true, fontSize: 20, fontWeight: 'bold' } },
        data: data.map((item: any) => ({
          value: item.count,
          name: item.topic
        }))
      }
    ]
  };
  myChart.setOption(option);
  
  // 响应式缩放
  window.addEventListener('resize', () => myChart.resize());
};

// --- 2. 再定义 fetchDocs ---
const fetchDocs = async () => {
  try {
    const res = await request.get('/v1/chat/knowledge_list');
    docs.value = res.data;

    // 获取分析数据
    const anaRes = await request.get('/v1/chat/analytics');
    if (anaRes.data && anaRes.data.length > 0) {
      await nextTick(); // 确保 DOM 已渲染
      initChart(anaRes.data);
    }
  } catch (e) {
    ElMessage.error('获取数据失败');
  }
};

const onUploadSuccess = () => {
  ElMessage.success('知识库已更新');
  fetchDocs();
};

const handleDelete = async (row: DocItem) => {
  try {
    await ElMessageBox.confirm(`确定要删除 [${row.filename}] 吗？`, '警告', { type: 'warning' });
    await request.delete(`/v1/chat/knowledge/${row.id}`);
    ElMessage.success('删除成功');
    fetchDocs();
  } catch (e) {}
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
  width: 1000px; height: 90%; background: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(20px); border-radius: 24px; border: 1px solid rgba(255,255,255,0.5);
  display: flex; flex-direction: column; padding: 30px; box-shadow: 0 20px 50px rgba(0,0,0,0.05);
}
.admin-header {
  display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;
  .title-section { display: flex; align-items: center; gap: 15px; h2 { margin: 0; font-size: 22px; } }
}
.table-wrapper { flex: 1; overflow-y: auto; margin-bottom: 20px; }
.chart-container {
  width: 100%; height: 350px; margin-top: 10px;
}
.admin-footer {
  display: flex; justify-content: space-between; align-items: center;
  border-top: 1px solid rgba(0,0,0,0.05); padding-top: 15px;
}
.custom-scrollbar::-webkit-scrollbar { width: 5px; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.1); border-radius: 10px; }
</style>