<template>
  <div class="admin-layout-wrapper">
    <!-- 左侧导航 -->
    <aside class="sidebar">
      <div class="logo">
        <el-icon class="mr-2"><Guide /></el-icon> ITQA 控制中枢
      </div>
      <div class="menu-list custom-scrollbar">
        <div v-for="(group, gIndex) in ADMIN_MENUS" :key="gIndex">
          <div class="menu-group-title">{{ group.group }}</div>
          <div 
            v-for="item in group.items" :key="item.id"
            :class="['menu-item', activePage === item.id ? 'active' : '']"
            @click="switchPage(item.id)"
          >
            <!-- 修复：通过 iconMap 映射对象解决动态组件找不到的警告 -->
            <el-icon class="menu-icon"><component :is="iconMap[item.icon]" /></el-icon>
            {{ item.label }}
          </div>
        </div>
      </div>
      <div class="sidebar-footer">
        <el-button link @click="$router.push('/')" :icon="HomeFilled">返回前台首页</el-button>
      </div>
    </aside>

    <!-- 右侧主体 -->
    <main class="main-layout">
      <!-- 顶部 Header -->
      <header class="header">
        <div class="search-box">
          <el-input placeholder="🔍 请输入搜索内容..." class="search-input" />
        </div>
        <div class="header-actions">
          <!-- 消息通知 Popover -->
          <el-popover placement="bottom" title="系统通知" :width="250" trigger="click">
            <template #reference>
              <el-badge :value="3" class="mr-4" type="danger" style="cursor: pointer;">
                <el-button circle :icon="Bell" />
              </el-badge>
            </template>
             <div style="font-size: 13px; color: #666; line-height: 1.8;">
              <p><span class="text-success">●</span> 缓存命中率：{{ dashStats.metrics.cache_hit_rate || '0%' }}</p>
              <p><span class="text-primary">●</span> 当前总用户：{{ dashStats.metrics.total_users || 0 }} 名</p>
              <p><span class="text-warning">●</span> 知识库切片：{{ dashStats.metrics.total_chunks || 0 }} 块</p>
            </div>
          </el-popover>

          <!-- 用户头像下拉菜单 -->
          <el-dropdown trigger="click">
            <el-avatar class="avatar" :size="32" :src="fullAvatarUrl" style="cursor: pointer;">
              {{ currentUser.username ? currentUser.username.charAt(0).toUpperCase() : 'A' }}
            </el-avatar>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="$router.push('/profile')">个人中心</el-dropdown-item>
                <el-dropdown-item @click="$router.push('/')">返回前台</el-dropdown-item>
                <el-dropdown-item divided @click="handleLogout" style="color: #f56c6c;">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <div class="content-scroll custom-scrollbar">
        <!-- ================== 页面1：数据概览 ================== -->
        <section v-show="activePage === 'dashboard'" class="page">
          <el-row :gutter="24" class="mb-4">
            <el-col :span="6">
              <div class="td-card">
                <div class="td-card-title">总用户数</div>
                <div class="metric-value">{{ dashStats.metrics.total_users || 0 }}</div>
                <div class="metric-desc"><span class="trend-up">正常</span> 系统用户总量</div>
              </div>
            </el-col>
            <el-col :span="6">
              <div class="td-card">
                <div class="td-card-title">有效知识切片</div>
                <div class="metric-value">{{ dashStats.metrics.total_chunks || 0 }}</div>
                <div class="metric-desc"><span class="trend-up">实时同步</span></div>
              </div>
            </el-col>
            <el-col :span="6">
              <div class="td-card">
                <div class="td-card-title">语义缓存拦截率</div>
                <div class="metric-value">{{ dashStats.metrics.cache_hit_rate || '0%' }}</div>
                <div class="metric-desc">大幅节省 API 调用</div>
              </div>
            </el-col>
            <el-col :span="6">
              <div class="td-card">
                <div class="td-card-title">昨日预估账单</div>
                <div class="metric-value" style="color: #d54941">{{ dashStats.metrics.estimated_cost || '$0' }}</div>
                <div class="metric-desc">Token 消耗折算</div>
              </div>
            </el-col>
          </el-row>

          <el-row :gutter="24">
            <el-col :span="16">
              <div class="td-card">
                <div class="td-card-title">
                  大模型 Token 消耗趋势 (7天)
                  <el-button size="small" @click="fetchDashboard">刷新图表</el-button>
                </div>
                <div ref="tokenChartRef" style="height: 300px;"></div>
              </div>
            </el-col>
            <el-col :span="8">
              <div class="td-card">
                <div class="td-card-title">高频调用用户 Top 5</div>
                <table class="td-table">
                  <thead><tr><th>排名</th><th>用户名</th><th>提问数</th></tr></thead>
                  <tbody>
                    <tr v-for="(u, idx) in dashStats.top_users" :key="idx">
                      <td><span class="tag tag-primary">{{ idx + 1 }}</span></td>
                      <td>{{ u.username }}</td>
                      <td>{{ u.count }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </el-col>
          </el-row>
        </section>

        <!-- ================== 页面2：AI 引擎控制 ================== -->
        <section v-show="activePage === 'ai-models'" class="page">
          <div class="td-card">
            <div class="td-card-title">
              <span>模型配置中枢 (LLM / Embedding)</span>
              <el-button type="primary" :icon="Plus" @click="showModelDialog = true">新增节点</el-button>
            </div>
            
            <table class="td-table mt-4">
              <thead><tr><th>类型</th><th>模型名称</th><th>提供商</th><th>Base URL</th><th>状态</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="cfg in aiConfigs" :key="cfg.id">
                  <td><el-tag size="small" type="info">{{ cfg.config_type.toUpperCase() }}</el-tag></td>
                  <td><b>{{ cfg.model_name }}</b></td>
                  <td>{{ cfg.provider_name }}</td>
                  <td style="color:#888">{{ cfg.base_url }}</td>
                  <td>
                    <span v-if="cfg.is_active" class="tag tag-success">已启用 (活跃)</span>
                    <span v-else class="tag tag-warning">待机</span>
                  </td>
                  <td>
                    <el-switch 
                      v-model="cfg.is_active" 
                      @change="handleActivateConfig(cfg)"
                      :disabled="cfg.is_active" 
                    />
                    <el-button 
                      size="small" 
                      class="ml-2" 
                      :loading="cfg.pinging"
                      @click="pingModel(cfg)"
                    >测速</el-button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <!-- ================== 页面3：知识库与题库 ================== -->
        <section v-show="activePage === 'knowledge'" class="page">
          <div class="td-card">
            <div class="td-card-title">
              <span>文档与索引管理</span>
              <div>
                <el-button type="warning" :icon="Edit" :loading="loadingQuiz" @click="handleGenerateQuiz">生成每日一练新题</el-button>
                <el-upload 
                  class="d-inline-block ml-2"
                  :action="uploadUrl" :headers="headers" :show-file-list="false" :on-success="onUploadSuccess"
                >
                  <el-button type="primary" :icon="Plus">上传新法规</el-button>
                </el-upload>
              </div>
            </div>
            <table class="td-table mt-4">
              <thead><tr><th>文档名称</th><th>上传时间</th><th>解析状态</th><th>切片数</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="doc in docs" :key="doc.id">
                  <td>{{ doc.filename }}</td>
                  <td>{{ formatShortDate(doc.upload_time) }}</td>
                  <td>
                    <span v-if="doc.chunk_count === 0" class="tag tag-warning"><el-icon class="is-loading"><Loading/></el-icon> Docling 解析中</span>
                    <span v-else class="tag tag-success">解析完成</span>
                  </td>
                  <td>{{ doc.chunk_count === 0 ? '--' : doc.chunk_count }}</td>
                  <td>
                    <el-button link type="danger" :disabled="doc.chunk_count === 0" @click="handleDeleteDoc(doc)">删除</el-button>
                  </td>
                </tr>
              </tbody>
            </table>
            <div class="mt-4 text-center">
              <el-button size="small" @click="fetchDocs" :icon="Refresh">刷新解析状态</el-button>
            </div>
          </div>
        </section>

        <!-- ================== 页面4：舆情与图谱 ================== -->
        <section v-show="activePage === 'analytics'" class="page">
          <el-row :gutter="24">
            <el-col :span="12">
              <div class="td-card">
                <div class="td-card-title">
                  热点话题聚类 (K-Means)
                  <el-button size="small" :loading="analyzing" @click="startAnalysis" :icon="Refresh">重新分析</el-button>
                </div>
                <div ref="pieChartRef" style="height: 300px;"></div>
              </div>
            </el-col>
            <el-col :span="12">
              <div class="td-card">
                <div class="td-card-title">AI 提取真实提问特征</div>
                <el-scrollbar height="300px">
                  <div v-if="hotTopics.length === 0" class="text-gray text-center mt-4">暂无分析数据</div>
                  <div v-for="item in hotTopics" :key="item.id" class="mb-3 border-bottom pb-2">
                    <span class="tag tag-primary mb-1">{{ item.topic_name }} ({{ item.hit_count }}次)</span>
                    <div class="text-sm text-gray mt-1">
                      <div v-for="(q, idx) in item.representative_queries" :key="idx">"{{ q }}"</div>
                    </div>
                  </div>
                </el-scrollbar>
              </div>
            </el-col>
          </el-row>
          <div class="td-card">
            <div class="td-card-title">
              交通法规逻辑图谱
              <el-button type="success" size="small" :icon="Share" :loading="loadingGraph" @click="handleBuildGraph">从对话更新图谱</el-button>
            </div>
            <div ref="graphChartRef" style="height: 450px; background: #fafafa; border: 1px solid #eee; border-radius: 8px;"></div>
          </div>
        </section>

        <!-- ================== 页面5：用户与系统 ================== -->
        <section v-show="activePage === 'users'" class="page">
          <div class="td-card">
            <div class="td-card-title">平台注册用户</div>
            <table class="td-table">
              <thead><tr><th>ID</th><th>用户名</th><th>注册日期</th><th>权限角色</th><th>状态</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="u in users" :key="u.id">
                  <td>{{ u.id }}</td>
                  <td>{{ u.username }}</td>
                  <td>{{ formatShortDate(u.created_at) }}</td>
                  <td>
                    <span :class="['tag', u.role === 'admin' ? 'tag-error' : 'tag-primary']">{{ u.role }}</span>
                  </td>
                   <td>
                    <span v-if="u.is_active !== false" class="tag tag-success">正常</span>
                    <span v-else class="tag tag-warning">已封禁</span>
                  </td>
                  <td>
                    <el-dropdown @command="(cmd) => handleUserAction(cmd, u)">
                      <el-button size="small" type="primary" plain>
                        管理 <el-icon class="el-icon--right"><ArrowDown /></el-icon>
                      </el-button>
                      <template #dropdown>
                        <el-dropdown-menu>
                          <el-dropdown-item command="toggle_role">
                            {{ u.role === 'admin' ? '降级为普通用户' : '设为管理员' }}
                          </el-dropdown-item>
                          <el-dropdown-item command="reset_pwd">重置密码</el-dropdown-item>
                          <el-dropdown-item command="toggle_status" :style="{ color: u.is_active !== false ? '#e6a23c' : '#67c23a' }">
                            {{ u.is_active !== false ? '封禁该账号' : '解封该账号' }}
                          </el-dropdown-item>
                          <el-dropdown-item divided command="delete" style="color: red;">删除用户</el-dropdown-item>
                        </el-dropdown-menu>
                      </template>
                    </el-dropdown>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section v-show="activePage === 'system'" class="page">
          <div class="td-card">
            <div class="td-card-title">微服务链路状态</div>
            <div class="status-grid mb-4">
              <div class="status-box"><span :class="sysStatus.mysql === 'ok' ? 'text-success' : 'text-error'">●</span> MySQL 数据库</div>
              <div class="status-box"><span :class="sysStatus.redis === 'ok' ? 'text-success' : 'text-error'">●</span> Redis 语义缓存</div>
            </div>
            <div class="td-card-title">拦截日志 (Mock)</div>
            <div class="terminal">
              >[18:01:23] 用户请求: "外星人是否考取了地球驾照？"<br>
              >[18:01:24] Rerank 相似度得分: 0.12 (阈值 0.35)<br>
              > <span style="color:#f56c6c">[18:01:24] 触发防御机制，已拦截防幻觉回复。</span><br>
              >[18:02:10] 系统心跳正常，内存占用正常...
            </div>
          </div>
        </section>

      </div>
      
      <!-- 底部状态栏 -->
      <footer class="admin-footer">
        <div class="stat-item">
          <span class="label">当前有效总切片:</span>
          <span class="value">{{ totalChunks }}</span>
        </div>
      </footer>
    </main>

    <!-- ================== 新增模型配置弹窗 ================== -->
    <el-dialog v-model="showModelDialog" title="新增 AI 模型节点" width="500px" append-to-body>
      <el-form :model="modelForm" label-position="top">
        <el-form-item label="节点类型">
          <el-radio-group v-model="modelForm.config_type">
            <el-radio label="llm">对话模型 (LLM)</el-radio>
            <el-radio label="embedding">向量模型 (Embedding)</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="提供商 (如 Aliyun, DeepSeek)">
          <el-input v-model="modelForm.provider_name" placeholder="请输入提供商名称" />
        </el-form-item>
        <el-form-item label="模型名称 (如 qwen-max)">
          <el-input v-model="modelForm.model_name" placeholder="请输入具体的模型版本号" />
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input v-model="modelForm.base_url" placeholder="https://api.example.com/v1" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="modelForm.api_key" type="password" show-password placeholder="sk-..." />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showModelDialog = false">取消</el-button>
        <el-button type="primary" @click="submitNewModel" :loading="savingModel">保存配置</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
// 【修复】 增加了 computed 的导入
import { ref, onMounted, nextTick, computed } from 'vue';
import { useRouter } from 'vue-router';
import { 
  Guide, DataLine, Cpu, Collection, PieChart, User, Monitor, HomeFilled, 
  Bell, Plus, Delete, Loading, Refresh, Edit, Share, ArrowDown 
} from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import * as echarts from 'echarts';
import request from '../api/request';
import { API_BASE_URL, STATIC_BASE_URL } from '../api/config';
import { ADMIN_MENUS, CHART_COLORS, ADMIN_API } from '../config/constants';

// 【修复】 定义图标映射字典，解决 Vue 控制台动态组件找不到的问题
const iconMap: Record<string, any> = {
  DataLine, Cpu, Collection, PieChart, User, Monitor
};

const router = useRouter();
const uploadUrl = `${API_BASE_URL}/v1/chat/upload`;
const headers = { Authorization: `Bearer ${localStorage.getItem('access_token')}` };
const currentUser = ref({ username: '', avatar: '' });

// --- 页面状态 ---
const activePage = ref('dashboard');
const dashStats = ref({ metrics: {} as any, chart: {} as any, top_users: [] });
const aiConfigs = ref<any[]>([]);
const docs = ref<any[]>([]);
const hotTopics = ref<any[]>([]);
const users = ref<any[]>([]);
const sysStatus = ref({ mysql: '', redis: '' });

// --- Loading 状态 ---
const analyzing = ref(false);
const loadingGraph = ref(false);
const loadingQuiz = ref(false);

// --- 模型配置表单状态 ---
const showModelDialog = ref(false);
const savingModel = ref(false);
const modelForm = ref({ config_type: 'llm', provider_name: '', model_name: '', base_url: '', api_key: '' });

// 计算头像全路径
const fullAvatarUrl = computed(() => {
  if (!currentUser.value.avatar) return '';
  return `${STATIC_BASE_URL}${currentUser.value.avatar}?t=${Date.now()}`;
});

// 【修复】 补充底部的总切片计算
const totalChunks = computed(() => docs.value.reduce((acc, cur) => acc + (cur.chunk_count || 0), 0));

// --- 图表 Refs ---
const tokenChartRef = ref<HTMLElement | null>(null);
const pieChartRef = ref<HTMLElement | null>(null);
const graphChartRef = ref<HTMLElement | null>(null);
let charts: echarts.ECharts[] =[];

// --- 方法：切换菜单 ---
const switchPage = async (pageId: string) => {
  activePage.value = pageId;
  if (pageId === 'dashboard') await fetchDashboard();
  if (pageId === 'ai-models') await fetchConfigs();
  if (pageId === 'knowledge') await fetchDocs();
  if (pageId === 'analytics') {
    await fetchHotTopics();
    await fetchGraph();
  }
  if (pageId === 'users') await fetchUsers();
  if (pageId === 'system') await fetchSystemStatus();
};

// --- 方法：登出 ---
const handleLogout = () => {
  localStorage.removeItem('access_token');
  router.push('/login');
};

// --- 方法：API 调用 ---
const fetchDashboard = async () => {
  const res = await request.get(ADMIN_API.STATS);
  dashStats.value = res.data;
  await nextTick();
  initTokenChart(res.data.chart);
};

const fetchConfigs = async () => {
  const res = await request.get(ADMIN_API.CONFIGS);
  aiConfigs.value = res.data;
};

const handleActivateConfig = async (cfg: any) => {
  try {
    await request.patch(ADMIN_API.ACTIVATE_CONFIG(cfg.id));
    ElMessage.success(`已切换活跃模型至: ${cfg.model_name}`);
    await fetchConfigs();
  } catch (e) {
    cfg.is_active = false;
  }
};

const pingModel = async (cfg: any) => {
  cfg.pinging = true;
  try {
    const res = await request.post(`/v1/admin/configs/${cfg.id}/ping`);
    ElMessage.success(`✅ 测速成功：${res.data.message} (延迟: ${res.data.delay}ms)`);
  } catch (e: any) {
    ElMessage.error(`❌ 测速失败：${e.response?.data?.detail || '网络超时'}`);
  } finally {
    cfg.pinging = false;
  }
};

const submitNewModel = async () => {
  if (!modelForm.value.model_name || !modelForm.value.api_key) return ElMessage.warning("请填写完整参数");
  savingModel.value = true;
  try {
    await request.post('/v1/admin/configs', modelForm.value);
    ElMessage.success("新增节点成功！");
    showModelDialog.value = false;
    modelForm.value = { config_type: 'llm', provider_name: '', model_name: '', base_url: '', api_key: '' }; // 重置表单
    fetchConfigs();
  } catch (e) {
    ElMessage.error("保存失败");
  } finally { savingModel.value = false; }
};

const fetchDocs = async () => {
  const res = await request.get('/v1/chat/knowledge_list');
  docs.value = res.data;
};

const onUploadSuccess = (response: any) => {
  if (response.status === 'processing') ElMessage.success('已放入后台解析队列');
  else ElMessage.success('上传成功');
  fetchDocs();
};

const handleDeleteDoc = async (row: any) => {
  await ElMessageBox.confirm(`确定删除 ${row.filename} 吗？`, '警告', { type: 'warning' });
  await request.delete(`/v1/chat/knowledge/${row.id}`);
  ElMessage.success('删除成功');
  fetchDocs();
};

const fetchHotTopics = async () => {
  const res = await request.get('/v1/chat/analytics');
  hotTopics.value = res.data;
  await nextTick();
  initPieChart(res.data);
};

const startAnalysis = async () => {
  analyzing.value = true;
  try {
    await request.post('/v1/chat/perform_analysis');
    ElMessage.success('热点分析已更新');
    await fetchHotTopics();
  } finally { analyzing.value = false; }
};

const fetchGraph = async () => {
  const res = await request.get('/v1/chat/knowledge_graph');
  await nextTick();
  initGraphChart(res.data);
};

const handleBuildGraph = async () => {
  loadingGraph.value = true;
  ElMessage.info('图谱更新任务已后台启动...');
  try {
    await request.post('/v1/chat/build_graph');
  } finally { loadingGraph.value = false; }
};

const handleGenerateQuiz = async () => {
  loadingQuiz.value = true;
  ElMessage.info('题库生成任务已后台启动...');
  try {
    await request.post('/v1/quiz/admin_generate');
  } finally { loadingQuiz.value = false; }
};

const fetchUsers = async () => {
  const res = await request.get(ADMIN_API.USERS);
  users.value = res.data;
};

const handleUserAction = async (cmd: string, user: any) => {
  try {
    if (cmd === 'toggle_role') {
      const newRole = user.role === 'admin' ? 'user' : 'admin';
      await request.patch(`/v1/admin/users/${user.id}/role`, null, { params: { role: newRole } });
      ElMessage.success(`角色已更新为 ${newRole}`);
      fetchUsers();
    } 
    else if (cmd === 'reset_pwd') {
      await ElMessageBox.confirm(`确定要将 ${user.username} 的密码重置为 123456 吗？`, '警告');
      await request.post(`/v1/admin/users/${user.id}/reset_password`);
      ElMessage.success('密码重置成功');
    } 
    else if (cmd === 'toggle_status') {
      const targetStatus = user.is_active === false ? true : false;
      const actionName = targetStatus ? '解封' : '封禁';
      await ElMessageBox.confirm(`确定要 ${actionName} 用户 ${user.username} 吗？`, '账号管理');
      await request.patch(`/v1/admin/users/${user.id}/status`, null, { params: { is_active: targetStatus } });
      ElMessage.success(`已成功${actionName}`);
      fetchUsers();
    }
    else if (cmd === 'delete') {
      await ElMessageBox.confirm(`此操作将永久删除用户 ${user.username} 及其所有对话记录，确定继续？`, '危险操作', { type: 'error' });
      await request.delete(`/v1/admin/users/${user.id}`);
      ElMessage.success('用户已删除');
      fetchUsers();
    }
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || "操作失败");
  }
};

const fetchSystemStatus = async () => {
  const res = await request.get(ADMIN_API.SYS_STATUS);
  sysStatus.value = res.data;
};

// --- ECharts 渲染逻辑 ---
const initTokenChart = (data: any) => {
  if (!tokenChartRef.value) return;
  const chart = echarts.init(tokenChartRef.value);
  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['Input Token', 'Output Token'] },
    xAxis: { type: 'category', data: data.days },
    yAxis: { type: 'value' },
    series:[
      { name: 'Input Token', type: 'line', smooth: true, data: data.input_tokens, itemStyle: { color: CHART_COLORS.primary } },
      { name: 'Output Token', type: 'line', smooth: true, data: data.output_tokens, itemStyle: { color: CHART_COLORS.success } }
    ]
  });
  charts.push(chart);
};

const initPieChart = (data: any[]) => {
  if (!pieChartRef.value || !data.length) return;
  const chart = echarts.init(pieChartRef.value);
  chart.setOption({
    tooltip: { trigger: 'item' },
    series:[{
      type: 'pie', radius:['40%', '70%'],
      data: data.map((item, i) => ({ 
        value: item.hit_count, name: item.topic_name, 
        itemStyle: { color: CHART_COLORS.pieColors[i % CHART_COLORS.pieColors.length] } 
      }))
    }]
  });
  charts.push(chart);
};

const initGraphChart = (data: any) => {
  if (!graphChartRef.value || !data.nodes || data.nodes.length === 0) return;
  const chart = echarts.init(graphChartRef.value);
  
  // 动态提取类别
  const categoriesSet = new Set(data.nodes.map((n: any) => n.category));
  const categories = Array.from(categoriesSet).map(name => ({ name }));

  chart.setOption({
    tooltip: {
      formatter: function (params: any) {
        if (params.dataType === 'node') return `实体: ${params.data.name}<br>类别: ${params.data.category}`;
        return `关系: ${params.data.value}`;
      }
    }, 
    animationDurationUpdate: 1500, animationEasingUpdate: 'quinticInOut',
    series:[{
      type: 'graph', layout: 'force', symbolSize: 35, roam: true,
      label: { show: true, fontSize: 11, position: 'right' }, 
      edgeSymbol:['none', 'arrow'], edgeSymbolSize: [4, 6],
      data: data.nodes.map((n:any) => ({ name: n.name, category: n.category, itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,0.2)' } })),
      links: data.links,
      categories: categories,
      force: { repulsion: 300, edgeLength:[80, 150] },
      lineStyle: { color: 'source', curveness: 0.2 }
    }]
  });
  charts.push(chart);
};

window.addEventListener('resize', () => charts.forEach(c => c.resize()));

const formatShortDate = (t: string) => t ? t.substring(0, 10) : '';

onMounted(async () => {
  try {
    const res = await request.get('/v1/chat/me');
    currentUser.value = res.data;
  } catch (e) {
    console.error("获取管理员信息失败");
  }
  await switchPage('dashboard');
});
</script>

<style scoped lang="scss">
.admin-layout-wrapper {
  display: flex; height: 100vh; background: #f3f4f7; color: #181818; overflow: hidden;
}

.sidebar {
  width: 232px; background: #fff; border-right: 1px solid #e7e7e7;
  display: flex; flex-direction: column; z-index: 100;
  .logo { height: 64px; display: flex; align-items: center; padding: 0 24px; font-size: 18px; font-weight: bold; color: #0052d9; border-bottom: 1px solid #e7e7e7; }
  .menu-list { flex: 1; padding: 12px 0; }
  .menu-group-title { padding: 12px 24px 4px; font-size: 12px; color: #5e6066; }
  .menu-item {
    padding: 12px 24px; margin: 4px 8px; border-radius: 4px; cursor: pointer; display: flex; align-items: center; font-size: 14px; transition: 0.2s;
    &:hover { background: #f2f3f5; }
    &.active { background: rgba(0, 82, 217, 0.1); color: #0052d9; font-weight: bold; }
    .menu-icon { margin-right: 10px; font-size: 16px; }
  }
  .sidebar-footer { padding: 15px; border-top: 1px solid #e7e7e7; text-align: center; }
}

.main-layout { flex: 1; display: flex; flex-direction: column; min-width: 0; }

.header {
  height: 64px; background: #fff; border-bottom: 1px solid #e7e7e7;
  display: flex; justify-content: space-between; align-items: center; padding: 0 24px;
  .search-input { width: 300px; }
  .header-actions { display: flex; align-items: center; }
  .avatar { background: #0052d9; color: #fff; cursor: pointer; }
}

.content-scroll { flex: 1; padding: 24px; overflow-y: auto; }

/* TDesign 卡片 */
.td-card {
  background: #fff; border-radius: 8px; padding: 24px; margin-bottom: 24px; box-shadow: 0 1px 4px rgba(0,0,0,0.02);
  .td-card-title { font-size: 16px; font-weight: bold; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; }
}

/* 指标项 */
.metric-value { font-size: 28px; font-weight: bold; margin: 8px 0; color: #181818; }
.metric-desc { font-size: 12px; color: #5e6066; .trend-up { color: #2ba471; margin-right: 5px; font-weight: bold; } }

/* 表格与标签 */
.td-table {
  width: 100%; border-collapse: collapse; font-size: 13px;
  th, td { padding: 12px; text-align: left; border-bottom: 1px solid #e7e7e7; }
  th { background: #fbfbfb; color: #5e6066; font-weight: normal; }
}
.tag { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 12px; }
.tag-success { background: #e3f9e9; color: #2ba471; }
.tag-warning { background: #fff1e9; color: #e37318; }
.tag-error { background: #ffedeb; color: #d54941; }
.tag-primary { background: #e0ebff; color: #0052d9; }

/* 其他公用样式 */
.mb-4 { margin-bottom: 16px; } .mt-4 { margin-top: 16px; } .mr-4 { margin-right: 16px; }
.d-inline-block { display: inline-block; } .ml-2 { margin-left: 8px; }
.border-bottom { border-bottom: 1px dashed #eee; } .pb-2 { padding-bottom: 8px; }
.text-sm { font-size: 12px; } .text-gray { color: #666; }
.text-success { color: #2ba471; } .text-error { color: #d54941; }

.status-grid { display: flex; gap: 20px; .status-box { padding: 15px; border: 1px solid #eee; border-radius: 8px; background: #fafafa; font-size: 14px;} }
.terminal { background: #1e1e1e; color: #4ade80; padding: 16px; border-radius: 6px; font-family: monospace; height: 150px; overflow-y: auto; font-size: 13px; line-height: 1.6; }

.custom-scrollbar::-webkit-scrollbar { width: 6px; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #ccc; border-radius: 4px; }

.admin-footer {
  padding: 15px 24px;
  background: #fff;
  border-top: 1px solid #e7e7e7;
  display: flex;
  justify-content: space-between;
  align-items: center;
  .stat-item {
    font-size: 13px;
    color: #5e6066;
    .value { font-weight: bold; color: #0052d9; margin-left: 8px; }
  }
}
</style>