<template>
  <div class="admin-page">
    <div class="bg-glow"></div>
    <div class="glass-card custom-scrollbar">
      <!-- 1. 标题与上传区域 -->
      <header class="admin-header">
        <div class="title-section">
          <el-icon :size="28" color="#409eff"><Collection /></el-icon>
          <h2>知识库管理</h2>
        </div>
        <el-upload 
          :action="uploadUrl"
          :headers="headers" 
          :on-success="onUploadSuccess" 
          :show-file-list="false"
          accept=".pdf, .txt, .docx, .md"
          class="upload-btn-wrapper"
        >
          <el-button type="primary" :icon="Plus" round>上传法规</el-button>
        </el-upload>
      </header>

      <!-- 2. 知识库列表表格 -->
      <div class="table-wrapper">
        <el-table :data="docs" style="width: 100%" class="modern-table" size="small">
          <el-table-column prop="filename" label="文件名" min-width="180" show-overflow-tooltip />
          
          <!-- 【核心修改】判断 chunk_count，如果是 0 显示解析中，否则显示具体数字 -->
          <el-table-column label="切片状态" width="100" align="center">
            <template #default="scope">
              <el-tag v-if="scope.row.chunk_count === 0" type="warning" effect="light" size="small">
                <el-icon class="is-loading"><Loading /></el-icon> 解析中
              </el-tag>
              <el-tag v-else type="success" effect="plain" size="small">
                {{ scope.row.chunk_count }} 块
              </el-tag>
            </template>
          </el-table-column>

          <!-- 移动端隐藏上传时间列以节省空间 -->
          <el-table-column prop="upload_time" label="日期" width="100" align="center" class-name="hidden-xs">
            <template #default="scope">
              {{ formatShortDate(scope.row.upload_time) }}
            </template>
          </el-table-column>
          
          <el-table-column label="操作" width="80" align="center">
            <template #default="scope">
              <!-- 加入防呆设计：如果在解析中，不允许删除，防止后台线程崩溃 -->
              <el-button 
                type="danger" 
                link 
                @click="handleDelete(scope.row)" 
                :disabled="scope.row.chunk_count === 0"
              >
                <el-icon><Delete /></el-icon>
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 3. 数据分析看板区域 -->
      <div class="analytics-dashboard">
        <el-divider content-position="left">
          <el-icon><PieChart /></el-icon> 热点分析
        </el-divider>

        <el-row :gutter="20">
          <el-col :xs="24" :sm="10">
            <div class="inner-card chart-box">
              <div ref="chartRef" class="echarts-container"></div>
            </div>
          </el-col>

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
          <!-- 刷新按钮也用来刷新列表状态 -->
          <el-button type="default" @click="fetchDocs" round>
            <el-icon><RefreshRight /></el-icon> 刷新列表状态
          </el-button>
          <el-button type="primary" :loading="analyzing" @click="startAnalysis" round>
            <el-icon v-if="!analyzing"><Refresh /></el-icon> 重新分析热点
          </el-button>
        </div>
      </div>

      <div class="admin-actions-grid">
        <el-card class="admin-card">
          <h3>图谱管理</h3>
          <p>从最新 AI 回答中提取知识链路</p>
          <el-button type="success" :icon="Share" @click="handleBuildGraph" :loading="loadingGraph">
            更新知识图谱
          </el-button>
        </el-card>

        <el-card class="admin-card">
          <h3>题库管理</h3>
          <p>强制触发 AI 生成新的每日一练题目</p>
          <el-button type="warning" :icon="Edit" @click="handleGenerateQuiz" :loading="loadingQuiz">
            生成新题目
          </el-button>
        </el-card>
      </div>

      <!-- RAG 评估区域 -->
      <div class="eval-section">
        <el-divider content-position="left">
          <el-icon><DataAnalysis /></el-icon> RAG 质量评估
        </el-divider>

        <div class="eval-actions">
          <el-button type="primary" :icon="CaretRight" @click="handleInitDefaultDataset" :loading="loadingInitDataset">
            初始化评估数据集
          </el-button>
          <el-button type="success" :icon="CaretRight" @click="handleRunEvaluation" :loading="loadingEval">
            开始评估
          </el-button>
          <el-button @click="fetchEvalLatest" :icon="Refresh">刷新结果</el-button>
        </div>

        <div v-if="evalLatest.status === 'success'" class="eval-score-cards">
          <div v-if="evalLatest.run_status === 'running'" class="eval-running-banner">
            <el-icon class="is-loading"><Loading /></el-icon>
            <span>评估进行中... 已完成 {{ evalLatest.completed_count || 0 }} / {{ evalLatest.total_questions || 0 }}</span>
          </div>
          <div class="score-card">
            <div class="score-value">{{ formatScore(evalLatest.avg_scores?.faithfulness) }}</div>
            <div class="score-label">忠实度</div>
          </div>
          <div class="score-card">
            <div class="score-value">{{ formatScore(evalLatest.avg_scores?.context_recall) }}</div>
            <div class="score-label">上下文召回率</div>
          </div>
          <div class="score-card">
            <div class="score-value">{{ formatScore(evalLatest.avg_scores?.answer_accuracy) }}</div>
            <div class="score-label">答案准确性</div>
          </div>
          <div class="score-card">
            <div class="score-value">{{ formatScore(evalLatest.avg_scores?.answer_relevancy) }}</div>
            <div class="score-label">回答相关性</div>
          </div>
        </div>

        <div v-if="evalLatest.status === 'no_data'" class="no-data" style="padding: 20px; text-align: center; color: #999;">
          暂无评估数据，请先初始化数据集并运行评估
        </div>

        <div v-if="evalLatest.details && evalLatest.details.length > 0" class="eval-detail-table">
          <el-table :data="evalLatest.details" size="small" style="width: 100%">
            <el-table-column prop="question" label="问题" min-width="150" show-overflow-tooltip />
            <el-table-column prop="faithfulness" label="忠实度" width="90" align="center">
              <template #default="scope">
                <span :class="getScoreClass(scope.row.faithfulness)">{{ formatScore(scope.row.faithfulness) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="context_recall" label="上下文召回率" width="110" align="center">
              <template #default="scope">
                <span :class="getScoreClass(scope.row.context_recall)">{{ formatScore(scope.row.context_recall) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="answer_accuracy" label="答案准确性" width="100" align="center">
              <template #default="scope">
                <span :class="getScoreClass(scope.row.answer_accuracy)">{{ formatScore(scope.row.answer_accuracy) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="answer_relevancy" label="回答相关性" width="100" align="center">
              <template #default="scope">
                <span :class="getScoreClass(scope.row.answer_relevancy)">{{ formatScore(scope.row.answer_relevancy) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="avg_score" label="平均分" width="80" align="center">
              <template #default="scope">
                <el-tag :type="scope.row.avg_score >= 0.7 ? 'success' : scope.row.avg_score >= 0.4 ? 'warning' : 'danger'" size="small">
                  {{ formatScore(scope.row.avg_score) }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <div v-if="evalHistory.length > 0" class="eval-history">
          <h4>评估历史</h4>
          <el-table :data="evalHistory" size="small">
            <el-table-column prop="run_id" label="批次ID" width="120" />
            <el-table-column prop="total_questions" label="问题数" width="80" align="center" />
            <el-table-column prop="avg_score" label="平均分" width="90" align="center">
              <template #default="scope">
                {{ formatScore(scope.row.avg_score) }}
              </template>
            </el-table-column>
            <el-table-column prop="run_time" label="时间" width="160" />
          </el-table>
        </div>
      </div>

      <!-- 4. 底部状态栏 -->
      <footer class="admin-footer">
        <div class="stat-item">
          <span class="label">当前有效总切片:</span>
          <span class="value">{{ totalChunks }}</span>
        </div>
        <el-button @click="$router.push('/')" link :icon="HomeFilled">返回系统首页</el-button>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, computed, nextTick } from 'vue';
import {Plus, Collection, Delete, PieChart, Refresh, Share, Edit, Loading, RefreshRight, HomeFilled, DataAnalysis, CaretRight } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import request from '../api/request';
import * as echarts from 'echarts';
import { API_BASE_URL } from '../api/config';

const uploadUrl = computed(() => `${API_BASE_URL}/v1/chat/upload`);

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

const docs = ref<DocItem[]>([]);
const hotTopics = ref<HotTopic[]>([]);
const chartRef = ref<HTMLElement | null>(null);
const analyzing = ref(false);
const headers = { Authorization: `Bearer ${localStorage.getItem('access_token')}` };
const loadingGraph = ref(false);
const loadingQuiz = ref(false);
const loadingEval = ref(false);
const loadingInitDataset = ref(false);
const evalLatest = ref<any>({ status: 'no_data' });
const evalHistory = ref<any[]>([]);
const evalPollingTimer = ref<any>(null);
const evalActiveRun = ref<any>({ status: 'idle' });

const initChart = (data: { topic: string; count: number }[]) => {
  if (!chartRef.value || !data.length) return;
  const myChart = echarts.init(chartRef.value);
  const option = {
    title: { text: '咨询热点分布', left: 'center', textStyle: { fontSize: 14 } },
    tooltip: { trigger: 'item', formatter: '{b}: {c}' },
    series:[
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

// 【核心修改】处理上传成功后的反馈
const onUploadSuccess = (response: any) => {
  if (response.status === 'processing') {
    // 后端返回 processing，说明已进入后台队列
    ElMessage.success({
      message: response.message || '文件已放入后台解析队列',
      duration: 4000
    });
  } else {
    ElMessage.success('上传并解析成功');
  }
  // 无论哪种情况，立刻刷新列表，把 chunk_count = 0 的记录展示出来
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

const handleBuildGraph = async () => {
  loadingGraph.value = true;
  ElMessage.info('图谱更新任务已在后台启动，处理约需 1-2 分钟...');
  try {
    await request.post('/v1/chat/build_graph');
    setTimeout(() => {
        ElMessage.success('后台正在处理中，稍后请刷新页面查看变化');
    }, 2000);
  } catch (e) { 
    ElMessage.error('任务启动失败'); 
  } finally {
    loadingGraph.value = false;
  }
};

const handleGenerateQuiz = async () => {
  loadingQuiz.value = true;
  ElMessage.info('题库生成任务已在后台启动，处理约需 1-2 分钟...');
  try {
    await request.post('/v1/quiz/admin_generate');
    setTimeout(() => {
        ElMessage.success('后台正在疯狂出题中，稍后可去刷题查看！');
    }, 2000);
  } catch (e) { 
    ElMessage.error('生成任务启动失败'); 
  } finally {
    loadingQuiz.value = false;
  }
};

// 辅助计算
const totalChunks = computed(() => docs.value.reduce((acc, cur) => acc + cur.chunk_count, 0));
const formatShortDate = (t: string) => {
  const d = new Date(t);
  return `${d.getMonth()+1}-${d.getDate()}`;
};

const formatScore = (val: number | null | undefined) => {
  if (val === null || val === undefined) return '-';
  return (val * 100).toFixed(1) + '%';
};

const getScoreClass = (val: number | null | undefined) => {
  if (val === null || val === undefined) return 'score-na';
  if (val >= 0.7) return 'score-good';
  if (val >= 0.4) return 'score-mid';
  return 'score-bad';
};

const fetchEvalLatest = async () => {
  try {
    const res = await request.get('/v1/evaluation/latest');
    evalLatest.value = res.data;
  } catch (e) {
    console.error('获取评估结果失败');
  }
};

const fetchEvalHistory = async () => {
  try {
    const res = await request.get('/v1/evaluation/history');
    evalHistory.value = res.data;
  } catch (e) {
    console.error('获取评估历史失败');
  }
};

const fetchActiveRun = async () => {
  try {
    const res = await request.get('/v1/evaluation/active_run');
    evalActiveRun.value = res.data;
  } catch (e) {
    console.error('获取评估状态失败');
  }
};

const startEvalPolling = () => {
  stopEvalPolling();
  evalPollingTimer.value = setInterval(async () => {
    await fetchActiveRun();
    await fetchEvalLatest();
    if (evalActiveRun.value.status !== 'running') {
      stopEvalPolling();
      fetchEvalHistory();
    }
  }, 30000);
};

const stopEvalPolling = () => {
  if (evalPollingTimer.value) {
    clearInterval(evalPollingTimer.value);
    evalPollingTimer.value = null;
  }
};

const handleRunEvaluation = async () => {
  loadingEval.value = true;
  try {
    const res = await request.post('/v1/evaluation/run');
    ElMessage.success(res.data.message || '评估任务已启动');
    evalActiveRun.value = { status: 'running', run_id: res.data.run_id, total: 0, completed_count: 0, progress: 0 };
    startEvalPolling();
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '评估启动失败');
  } finally {
    loadingEval.value = false;
  }
};

const handleInitDefaultDataset = async () => {
  loadingInitDataset.value = true;
  try {
    const res = await request.post('/v1/evaluation/datasets/init_default');
    ElMessage.success(res.data.message || '初始化完成');
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '初始化失败');
  } finally {
    loadingInitDataset.value = false;
  }
};

onMounted(async () => {
  fetchDocs();
  fetchEvalLatest();
  fetchEvalHistory();
  await fetchActiveRun();
  if (evalActiveRun.value.status === 'running') {
    startEvalPolling();
  }
});

onBeforeUnmount(() => {
  stopEvalPolling();
});
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

.eval-section {
  margin-top: 20px;

  .eval-actions {
    display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap;
  }

  .eval-score-cards {
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 20px;

    @media (max-width: 768px) {
      grid-template-columns: repeat(2, 1fr);
    }

    .eval-running-banner {
      grid-column: 1 / -1;
      background: linear-gradient(135deg, #e6a23c 0%, #f56c6c 100%);
      border-radius: 12px; padding: 12px 20px; text-align: center; color: #fff;
      display: flex; align-items: center; justify-content: center; gap: 8px;
      font-size: 14px;
    }

    .score-card {
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      border-radius: 16px; padding: 20px; text-align: center; color: #fff;

      .score-value {
        font-size: 28px; font-weight: bold; margin-bottom: 5px;
      }

      .score-label {
        font-size: 12px; opacity: 0.85;
      }
    }
  }

  .eval-detail-table {
    margin-bottom: 20px;
  }

  .eval-history {
    h4 { margin: 10px 0; color: #333; }
  }

  .score-good { color: #67c23a; font-weight: bold; }
  .score-mid { color: #e6a23c; font-weight: bold; }
  .score-bad { color: #f56c6c; font-weight: bold; }
  .score-na { color: #999; }
}
</style>