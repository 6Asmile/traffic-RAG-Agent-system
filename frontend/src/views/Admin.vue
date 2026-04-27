<template>
  <div class="admin-layout-wrapper">
    <!-- 左侧导航 -->
    <aside class="sidebar">
      <div class="logo">
        <el-icon class="mr-2"><Guide /></el-icon> ITQA 控制中枢
      </div>
      <div class="menu-list custom-scrollbar">
        <div style="padding: 8px 12px;">
          <el-input v-model="menuSearch" size="small" placeholder="搜索菜单..." clearable />
        </div>
        <div v-if="recentMenuItems.length" style="padding: 0 12px 8px;">
          <div class="menu-group-title">最近访问</div>
          <div
            v-for="item in recentMenuItems" :key="`recent-${item.id}`"
            :class="['menu-item', activePage === item.id ? 'active' : '']"
            @click="switchPage(item.id)"
          >
            <el-icon class="menu-icon"><component :is="iconMap[item.icon]" /></el-icon>
            {{ item.label }}
          </div>
        </div>
        <div v-for="(group, gIndex) in filteredMenus" :key="gIndex">
          <div class="menu-group-title menu-group-clickable" @click="toggleGroup(group.group)">
            <span>{{ group.group }}</span>
            <span>{{ collapsedGroups.has(group.group) ? '+' : '-' }}</span>
          </div>
          <template v-if="!collapsedGroups.has(group.group)">
            <div 
              v-for="item in group.items" :key="item.id"
              :class="['menu-item', activePage === item.id ? 'active' : '']"
              @click="switchPage(item.id)"
            >
              <el-icon class="menu-icon"><component :is="iconMap[item.icon]" /></el-icon>
              {{ item.label }}
            </div>
          </template>
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
          <el-popover placement="bottom" title="系统通知" :width="360" trigger="click">
            <template #reference>
              <el-badge :value="notificationUnreadCount" :hidden="notificationUnreadCount <= 0" class="mr-4" type="danger" style="cursor: pointer;">
                <el-button circle :icon="Bell" @click="fetchNotifications()" />
              </el-badge>
            </template>
            <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
              <el-select v-model="notificationSeverityFilter" size="small" style="width: 110px;">
                <el-option label="全部" value="all" />
                <el-option label="错误" value="error" />
                <el-option label="告警" value="warn" />
                <el-option label="信息" value="info" />
              </el-select>
              <el-button size="small" @click="markAllNotificationsRead">全部已读</el-button>
            </div>
            <div style="max-height: 260px; overflow-y: auto;">
              <div
                v-for="item in filteredNotifications"
                :key="item.key"
                style="padding: 8px; border-bottom: 1px solid #eee; cursor: pointer;"
                @click="jumpFromNotification(item)"
              >
                <div style="display: flex; justify-content: space-between;">
                  <strong>{{ item.title }}</strong>
                  <span :class="['tag', item.severity === 'error' ? 'tag-error' : item.severity === 'warn' ? 'tag-warning' : 'tag-primary']">
                    {{ item.severity }}
                  </span>
                </div>
                <div class="text-sm text-gray" style="margin-top: 4px;">{{ item.message }}</div>
              </div>
              <div v-if="filteredNotifications.length === 0" class="text-gray text-center mt-2">暂无通知</div>
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
          <div class="td-card" style="padding: 12px 16px;">
            <div style="display: flex; gap: 8px; align-items: center;">
              <span>统计窗口</span>
              <el-select v-model="dashboardDays" style="width: 120px;">
                <el-option :value="1" label="近1天" />
                <el-option :value="7" label="近7天" />
                <el-option :value="14" label="近14天" />
                <el-option :value="30" label="近30天" />
              </el-select>
              <el-button size="small" @click="fetchDashboard">刷新</el-button>
            </div>
          </div>
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

          <el-row :gutter="24" class="mb-4">
            <el-col :span="8">
              <div class="td-card">
                <div class="td-card-title">检索成功率</div>
                <div class="metric-value" :style="{ color: (dashStats.metrics.success_rate || 0) < 65 ? '#d54941' : '#2ba471' }">
                  {{ dashStats.metrics.success_rate || 0 }}%
                </div>
                <div class="metric-desc">失败率 {{ dashStats.metrics.failure_rate || 0 }}%</div>
              </div>
            </el-col>
            <el-col :span="8">
              <div class="td-card">
                <div class="td-card-title">P95 检索时延</div>
                <div class="metric-value" :style="{ color: (dashStats.metrics.p95_latency_ms || 0) > 12000 ? '#d54941' : '#181818' }">
                  {{ dashStats.metrics.p95_latency_ms || 0 }} ms
                </div>
                <div class="metric-desc">近 {{ dashboardDays }} 天</div>
              </div>
            </el-col>
            <el-col :span="8">
              <div class="td-card">
                <div class="td-card-title">异常标记</div>
                <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                  <span v-for="flag in dashStats.metrics.anomaly_flags || []" :key="flag" class="tag tag-warning">{{ flag }}</span>
                  <span v-if="!(dashStats.metrics.anomaly_flags || []).length" class="tag tag-success">none</span>
                </div>
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

        <!-- ================== 页面2：检索质量看板 ================== -->
        <section v-show="activePage === 'retrieval'" class="page">
          <div class="td-card">
            <div class="td-card-title">
              <span>检索质量看板</span>
              <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
                <el-select v-model="retrievalFilters.days" style="width: 120px;">
                  <el-option :value="1" label="近1天" />
                  <el-option :value="7" label="近7天" />
                  <el-option :value="14" label="近14天" />
                  <el-option :value="30" label="近30天" />
                </el-select>
                <el-select v-model="retrievalFilters.mode" style="width: 120px;" clearable placeholder="全部模式">
                  <el-option value="fast" label="fast" />
                  <el-option value="expert" label="expert" />
                </el-select>
                <el-input v-model="retrievalFilters.source" style="width: 200px;" placeholder="来源过滤 source" clearable />
                <el-button type="primary" :loading="loadingRetrieval" @click="handleRetrievalFilterSearch">查询</el-button>
              </div>
            </div>

            <el-row :gutter="16" class="mb-4">
              <el-col :span="6">
                <div class="td-card" style="margin-bottom: 0; padding: 16px;">
                  <div class="td-card-title">总检索次数</div>
                  <div class="metric-value">{{ retrievalDashboard.summary.total_queries || 0 }}</div>
                </div>
              </el-col>
              <el-col :span="6">
                <div class="td-card" style="margin-bottom: 0; padding: 16px;">
                  <div class="td-card-title">平均最终命中数</div>
                  <div class="metric-value">{{ retrievalDashboard.summary.avg_final_count || 0 }}</div>
                </div>
              </el-col>
              <el-col :span="6">
                <div class="td-card" style="margin-bottom: 0; padding: 16px;">
                  <div class="td-card-title">Rerank 回退率</div>
                  <div class="metric-value">{{ formatPercent(retrievalDashboard.summary.rerank_fallback_rate) }}</div>
                </div>
              </el-col>
              <el-col :span="6">
                <div class="td-card" style="margin-bottom: 0; padding: 16px;">
                  <div class="td-card-title">平均检索耗时</div>
                  <div class="metric-value">{{ retrievalDashboard.summary.avg_latency_ms || 0 }} ms</div>
                </div>
              </el-col>
            </el-row>

            <el-row :gutter="16">
              <el-col :span="14">
                <div class="td-card" style="margin-bottom: 0; padding: 16px;">
                  <div class="td-card-title">按日趋势</div>
                  <div ref="retrievalTrendChartRef" style="height: 280px;"></div>
                </div>
              </el-col>
              <el-col :span="10">
                <div class="td-card" style="margin-bottom: 0; padding: 16px;">
                  <div class="td-card-title">模式拆分</div>
                  <table class="td-table">
                    <thead><tr><th>模式</th><th>总次数</th><th>平均命中</th><th>平均耗时</th></tr></thead>
                    <tbody>
                      <tr v-for="item in retrievalDashboard.mode_breakdown" :key="item.mode">
                        <td>{{ item.mode }}</td>
                        <td>{{ item.total_queries }}</td>
                        <td>{{ item.avg_final_count }}</td>
                        <td>{{ item.avg_latency_ms }} ms</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </el-col>
            </el-row>
          </div>

          <div class="td-card">
            <div class="td-card-title">
              <span>Run 明细追踪</span>
              <div style="display: flex; gap: 8px;">
                <el-input v-model="retrievalRunId" style="width: 420px;" placeholder="输入 run_id 精确查询（留空则显示最近 run）" clearable />
                <el-switch v-model="retrievalRunOnly" active-text="仅 run_id" />
                <el-button type="primary" @click="fetchRetrievalRunDetails">查询 run / 最近</el-button>
              </div>
            </div>
            <table class="td-table">
              <thead><tr><th>时间</th><th>run_id</th><th>source</th><th>mode</th><th>faiss</th><th>bm25</th><th>fusion</th><th>final</th><th>threshold</th><th>top</th><th>耗时</th></tr></thead>
              <tbody>
                <tr v-for="row in retrievalRunItems" :key="row.id">
                  <td>{{ formatDateTime(row.created_at) }}</td>
                  <td>{{ row.run_id || '-' }}</td>
                  <td>{{ row.source }}</td>
                  <td>{{ row.mode }}</td>
                  <td>{{ row.faiss_count }}</td>
                  <td>{{ row.bm25_count }}</td>
                  <td>{{ row.fusion_count }}</td>
                  <td>{{ row.final_count }}</td>
                  <td>{{ row.threshold_used }}</td>
                  <td>{{ row.top_score }}</td>
                  <td>{{ row.latency_ms }} ms</td>
                </tr>
                <tr v-if="retrievalRunItems.length === 0">
                  <td colspan="11" class="text-gray">暂无 run 明细数据</td>
                </tr>
              </tbody>
            </table>
          </div>
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
                <el-button class="ml-2" @click="rebuildBm25">重建 BM25</el-button>
                <el-upload 
                  class="d-inline-block ml-2"
                  :action="uploadUrl" :headers="headers" :show-file-list="false" :on-success="onUploadSuccess"
                >
                  <el-button type="primary" :icon="Plus">上传新法规</el-button>
                </el-upload>
              </div>
            </div>
            <table class="td-table mt-4">
              <thead><tr><th>文档名称</th><th>上传时间</th><th>解析状态</th><th>切片数</th><th>质量评分</th><th>命中估计</th><th>切片预览</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="doc in knowledgeEnhancedDocs" :key="doc.id">
                  <td>{{ doc.filename }}</td>
                  <td>{{ formatShortDate(doc.upload_time) }}</td>
                  <td>
                    <span v-if="doc.status === 'processing'" class="tag tag-warning"><el-icon class="is-loading"><Loading/></el-icon> 解析中 {{ doc.progress_pct }}%</span>
                    <span v-else-if="doc.status === 'ready'" class="tag tag-success">解析完成</span>
                    <span v-else class="tag tag-error">解析失败</span>
                  </td>
                  <td>{{ doc.chunk_count === 0 ? '--' : doc.chunk_count }}</td>
                  <td>
                    <div v-if="doc.quality_metrics && doc.quality_metrics.total_chunks > 0">
                      <span class="tag" :class="qualityScoreTagClass(doc.quality_metrics.quality_score)">
                        {{ Number(doc.quality_metrics.quality_score || 0).toFixed(1) }}
                      </span>
                      <div class="text-sm text-gray mt-1">路线: {{ parseRouteLabel(doc.parse_meta) }}</div>
                      <div class="text-sm text-gray">空块率: {{ formatRate(doc.quality_metrics.empty_chunk_rate) }}</div>
                      <div class="text-sm text-gray">乱码率: {{ formatRate(doc.quality_metrics.garbled_chunk_rate) }}</div>
                      <div class="text-sm text-gray">超短率: {{ formatRate(doc.quality_metrics.short_chunk_rate) }}</div>
                      <div class="text-sm text-gray">表格率: {{ formatRate(doc.quality_metrics.table_chunk_rate) }}</div>
                    </div>
                    <span v-else class="text-gray">-</span>
                    <div v-if="doc.parse_error" class="text-sm" style="color:#d54941; max-width: 260px; word-break: break-all;">
                      {{ truncateText(doc.parse_error, 120) }}
                    </div>
                  </td>
                  <td>{{ doc.recall_hits_estimated || 0 }}</td>
                  <td>
                    <div v-if="doc.chunk_count > 0">
                      <el-link type="primary" @click="openChunkPreview(doc)">点击查看详情</el-link>
                      <div v-if="doc.chunk_preview?.length" class="text-sm text-gray mt-1">
                        {{ truncateText(doc.chunk_preview[0], 120) }}
                      </div>
                    </div>
                    <div v-else-if="doc.chunk_preview?.length" class="text-sm text-gray">
                      {{ truncateText(doc.chunk_preview[0], 120) }}
                    </div>
                    <span v-else class="text-gray">-</span>
                  </td>
                  <td>
                    <el-button link @click="retryParseDoc(doc)" :disabled="doc.status === 'processing'">重试解析</el-button>
                    <el-button link type="danger" :disabled="doc.status === 'processing'" @click="handleDeleteDoc(doc)">删除</el-button>
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
              <div style="display: flex; gap: 8px; align-items: center;">
                <span class="text-sm text-gray">节点 {{ graphStats.node_count }} / 关系 {{ graphStats.link_count }}</span>
                <el-button type="success" size="small" :icon="Share" :loading="loadingGraph" @click="handleBuildGraph">从知识库扩展图谱</el-button>
              </div>
            </div>
            <div ref="graphChartRef" style="height: 450px; background: #fafafa; border: 1px solid #eee; border-radius: 8px;"></div>
            <div v-if="graphEmptyMessage" class="text-sm text-gray mt-2">{{ graphEmptyMessage }}</div>
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
                    <el-dropdown @command="(cmd: string) => handleUserAction(cmd, u)">
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

        <section v-show="activePage === 'audit'" class="page">
          <div class="td-card">
            <div class="td-card-title">
              <span>管理员审计日志</span>
              <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
                <el-select v-model="auditFilters.days" style="width: 120px;">
                  <el-option :value="1" label="近1天" />
                  <el-option :value="7" label="近7天" />
                  <el-option :value="14" label="近14天" />
                  <el-option :value="30" label="近30天" />
                </el-select>
                <el-input v-model="auditFilters.action" style="width: 180px;" placeholder="操作类型 action" clearable />
                <el-input v-model="auditFilters.actor_user_id" style="width: 160px;" placeholder="操作人ID" clearable />
                <el-input v-model="auditFilters.keyword" style="width: 220px;" placeholder="关键词(目标/详情)" clearable />
                <el-button type="primary" :loading="loadingAuditLogs" @click="fetchAuditLogs">查询</el-button>
              </div>
            </div>

            <el-row :gutter="16" class="mb-4">
              <el-col :span="8">
                <div class="td-card" style="margin-bottom: 0; padding: 16px;">
                  <div class="td-card-title">日志总数</div>
                  <div class="metric-value">{{ auditLogs.total || 0 }}</div>
                </div>
              </el-col>
              <el-col :span="16">
                <div class="td-card" style="margin-bottom: 0; padding: 16px;">
                  <div class="td-card-title">操作分布</div>
                  <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                    <span
                      v-for="item in auditLogs.action_breakdown"
                      :key="item.action"
                      class="tag tag-primary"
                    >
                      {{ item.action }}: {{ item.count }}
                    </span>
                    <span v-if="!auditLogs.action_breakdown?.length" class="text-gray">暂无统计数据</span>
                  </div>
                </div>
              </el-col>
            </el-row>

            <table class="td-table">
              <thead><tr><th>时间</th><th>操作人</th><th>action</th><th>目标</th><th>结果</th><th>IP</th><th>详情</th></tr></thead>
              <tbody>
                <tr v-for="row in auditLogs.items" :key="row.id">
                  <td>{{ formatDateTime(row.created_at) }}</td>
                  <td>{{ row.actor_username }} ({{ row.actor_user_id }})</td>
                  <td>{{ row.action }}</td>
                  <td>{{ row.target_type }} / {{ row.target_id }}</td>
                  <td>
                    <span :class="['tag', row.result === 'success' ? 'tag-success' : 'tag-warning']">
                      {{ row.result }}
                    </span>
                  </td>
                  <td>{{ row.client_ip }}</td>
                  <td>{{ row.detail_json }}</td>
                </tr>
                <tr v-if="(auditLogs.items || []).length === 0">
                  <td colspan="7" class="text-gray">暂无审计日志</td>
                </tr>
              </tbody>
            </table>

            <div class="mt-4">
              <div class="td-card-title">灰度发布配置</div>
              <el-row :gutter="12">
                <el-col :span="6">
                  <el-select v-model="rolloutForm.config_type" style="width: 100%;" @change="syncRolloutFormByType">
                    <el-option value="llm" label="LLM" />
                    <el-option value="embedding" label="Embedding" />
                  </el-select>
                </el-col>
                <el-col :span="6">
                  <el-select v-model="rolloutForm.baseline_config_id" style="width: 100%;" placeholder="baseline">
                    <el-option
                      v-for="item in rolloutOptions"
                      :key="`base-${item.id}`"
                      :value="item.id"
                      :label="`${item.model_name} (${item.provider_name})`"
                    />
                  </el-select>
                </el-col>
                <el-col :span="6">
                  <el-select v-model="rolloutForm.canary_config_id" style="width: 100%;" placeholder="canary">
                    <el-option
                      v-for="item in rolloutOptions"
                      :key="`canary-${item.id}`"
                      :value="item.id"
                      :label="`${item.model_name} (${item.provider_name})`"
                    />
                  </el-select>
                </el-col>
                <el-col :span="6">
                  <el-switch v-model="rolloutForm.enabled" active-text="启用灰度" inactive-text="关闭灰度" />
                </el-col>
              </el-row>
              <div class="mt-4" style="display: flex; gap: 12px; align-items: center;">
                <span>灰度比例 {{ rolloutForm.ratio_pct }}%</span>
                <el-slider v-model="rolloutForm.ratio_pct" :min="0" :max="100" style="width: 280px;" />
                <el-button size="small" @click="saveRolloutConfig">保存</el-button>
                <el-button size="small" @click="pingRolloutCompare">测速对比</el-button>
              </div>
              <div v-if="rolloutPingResult" class="mt-2 text-sm text-gray">
                baseline: {{ rolloutPingResult.baseline?.delay_ms || 0 }}ms / {{ rolloutPingResult.baseline?.status || '-' }} |
                canary: {{ rolloutPingResult.canary?.delay_ms || 0 }}ms / {{ rolloutPingResult.canary?.status || '-' }}
              </div>
            </div>
          </div>
        </section>

        <section v-show="activePage === 'system'" class="page">
          <div class="td-card">
            <div class="td-card-title">微服务链路状态</div>
            <div class="status-grid mb-4">
              <div class="status-box"><span :class="sysStatus.mysql === 'ok' ? 'text-success' : 'text-error'">●</span> MySQL 数据库</div>
              <div class="status-box"><span :class="sysStatus.redis === 'ok' ? 'text-success' : 'text-error'">●</span> Redis 语义缓存</div>
              <div class="status-box"><span :class="sysStatus.llm_api === 'ok' ? 'text-success' : 'text-error'">●</span> LLM API</div>
              <div class="status-box"><span :class="sysStatus.amap_api === 'ok' ? 'text-success' : 'text-error'">●</span> 地图/天气 API</div>
              <div class="status-box"><span :class="sysStatus.vector_db === 'ok' ? 'text-success' : 'text-error'">●</span> 向量索引</div>
            </div>

            <div class="td-card-title">错误概览</div>
            <div class="status-grid mb-4">
              <div class="status-box">最近1小时错误数: <b>{{ sysStatus.error_count_1h || 0 }}</b></div>
              <div class="status-box">LLM 延迟: <b>{{ sysStatus.llm_latency_ms || 0 }} ms</b></div>
            </div>
            <div class="terminal">
              <div>last_error_at: {{ formatDateTime(sysStatus.last_error_at) || '-' }}</div>
              <div>last_error: {{ sysStatus.last_error || '-' }}</div>
            </div>

            <div class="td-card-title mt-4">健康趋势（近12小时）</div>
            <div ref="systemHealthChartRef" style="height: 260px;"></div>

            <div class="td-card-title mt-4">依赖错误详情</div>
            <div class="terminal">
              <div>mysql: {{ sysStatus.errors?.mysql || '-' }}</div>
              <div>redis: {{ sysStatus.errors?.redis || '-' }}</div>
              <div>llm_api: {{ sysStatus.errors?.llm_api || '-' }}</div>
              <div>amap_api: {{ sysStatus.errors?.amap_api || '-' }}</div>
              <div>vector_db: {{ sysStatus.errors?.vector_db || '-' }}</div>
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

    <el-dialog
      v-model="chunkPreviewVisible"
      :title="`切片详情 - ${chunkPreviewDocName}`"
      width="78%"
      top="5vh"
      destroy-on-close
    >
      <div v-loading="chunkPreviewLoading" class="chunk-preview-body">
        <div class="chunk-preview-sidebar">
          <el-scrollbar height="56vh">
            <div
              v-for="(item, idx) in chunkPreviewItems"
              :key="`${item.index}-${idx}`"
              :class="['chunk-preview-item', selectedChunkPos === idx ? 'active' : '']"
              @click="selectedChunkPos = idx"
            >
              <div class="chunk-preview-title">切片 #{{ item.index + 1 }}</div>
              <div class="chunk-preview-snippet">{{ item.preview }}</div>
            </div>
            <div v-if="chunkPreviewItems.length === 0" class="text-gray text-center mt-2">暂无可渲染切片</div>
          </el-scrollbar>
          <div class="mt-2">
            <el-pagination
              v-if="chunkPreviewTotal > chunkPreviewPageSize"
              background
              layout="prev, pager, next"
              :page-size="chunkPreviewPageSize"
              :total="chunkPreviewTotal"
              :current-page="chunkPreviewPage"
              @current-change="handleChunkPageChange"
            />
          </div>
        </div>
        <div class="chunk-preview-main">
          <div v-if="activeChunkItem" class="chunk-preview-meta">
            当前切片 #{{ activeChunkItem.index + 1 }}
          </div>
          <div
            v-if="activeChunkItem"
            class="chunk-preview-render markdown-body"
            v-html="renderChunkMarkdown(activeChunkItem.content)"
          ></div>
          <div v-else class="text-gray">请选择左侧切片查看详情</div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
// 【修复】 增加了 computed 的导入
import { ref, onMounted, nextTick, computed } from 'vue';
import { useRouter } from 'vue-router';
import { 
  Guide, DataLine, Cpu, Collection, PieChart, User, Monitor, HomeFilled, 
  Bell, Plus, Loading, Refresh, Edit, Share, ArrowDown 
} from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import * as echarts from 'echarts';
import MarkdownIt from 'markdown-it';
import request from '../api/request';
import { API_BASE_URL, STATIC_BASE_URL } from '../api/config';
import { ADMIN_MENUS, CHART_COLORS, ADMIN_API } from '../config/constants';

// 【修复】 定义图标映射字典，解决 Vue 控制台动态组件找不到的问题
const iconMap: Record<string, any> = {
  DataLine, Cpu, Collection, PieChart, User, Monitor
};

const router = useRouter();
const md = new MarkdownIt({ html: true, linkify: true, breaks: true });
const uploadUrl = `${API_BASE_URL}/v1/chat/upload`;
const headers = { Authorization: `Bearer ${localStorage.getItem('access_token')}` };
const currentUser = ref({ username: '', avatar: '' });

// --- 页面状态 ---
const activePage = ref('dashboard');
const dashStats = ref({ metrics: {} as any, chart: {} as any, top_users: [] as any[] });
const aiConfigs = ref<any[]>([]);
const docs = ref<any[]>([]);
const knowledgeEnhancedDocs = ref<any[]>([]);
const hotTopics = ref<any[]>([]);
const users = ref<any[]>([]);
const sysStatus = ref({
  mysql: '',
  redis: '',
  llm_api: '',
  amap_api: '',
  vector_db: '',
  llm_latency_ms: 0,
  error_count_1h: 0,
  last_error: '',
  last_error_at: '',
  errors: {} as any,
  health_history: [] as any[],
});
const auditLogs = ref({
  page: 1,
  page_size: 20,
  total: 0,
  items: [] as any[],
  action_breakdown: [] as any[],
});
const auditFilters = ref({
  days: 7,
  action: '',
  actor_user_id: '',
  keyword: '',
});
const retrievalDashboard = ref({
  summary: {} as any,
  trend: [] as any[],
  mode_breakdown: [] as any[],
});
const retrievalFilters = ref({
  days: 7,
  mode: '',
  source: '',
});
const retrievalRunId = ref('');
const retrievalRunOnly = ref(false);
const retrievalRunItems = ref<any[]>([]);
const dashboardDays = ref(7);
const menuSearch = ref('');
const collapsedGroups = ref(new Set<string>());
const recentMenuIds = ref<string[]>(JSON.parse(localStorage.getItem('admin_recent_menus') || '[]'));
const notifications = ref<any[]>([]);
const notificationUnreadCount = ref(0);
const notificationSeverityFilter = ref('all');
const rolloutState = ref<any>({});
const rolloutForm = ref({
  config_type: 'llm',
  enabled: false,
  baseline_config_id: 0,
  canary_config_id: 0,
  ratio_pct: 0,
});
const rolloutPingResult = ref<any>(null);
const graphStats = ref({ node_count: 0, link_count: 0 });
const graphEmptyMessage = ref('');
const chunkPreviewVisible = ref(false);
const chunkPreviewLoading = ref(false);
const chunkPreviewDocId = ref<number | null>(null);
const chunkPreviewDocName = ref('');
const chunkPreviewItems = ref<any[]>([]);
const chunkPreviewTotal = ref(0);
const chunkPreviewPage = ref(1);
const chunkPreviewPageSize = 60;
const selectedChunkPos = ref(0);

// --- Loading 状态 ---
const analyzing = ref(false);
const loadingGraph = ref(false);
const loadingQuiz = ref(false);
const loadingRetrieval = ref(false);
const loadingAuditLogs = ref(false);

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
const totalChunks = computed(() => knowledgeEnhancedDocs.value.reduce((acc, cur) => acc + (cur.chunk_count || 0), 0));
const filteredMenus = computed(() => {
  const keyword = menuSearch.value.trim().toLowerCase();
  if (!keyword) return ADMIN_MENUS;
  return ADMIN_MENUS.map(group => ({
    ...group,
    items: group.items.filter(item => item.label.toLowerCase().includes(keyword) || item.id.toLowerCase().includes(keyword)),
  })).filter(group => group.items.length > 0);
});
const recentMenuItems = computed(() => {
  const allItems = ADMIN_MENUS.flatMap(group => group.items);
  return recentMenuIds.value
    .map(id => allItems.find(item => item.id === id))
    .filter(Boolean) as any[];
});
const filteredNotifications = computed(() => {
  if (notificationSeverityFilter.value === 'all') return notifications.value;
  return notifications.value.filter(item => item.severity === notificationSeverityFilter.value);
});
const rolloutOptions = computed(() => {
  const list = rolloutForm.value.config_type === 'embedding'
    ? (rolloutState.value.options?.embedding || [])
    : (rolloutState.value.options?.llm || []);
  return Array.isArray(list) ? list : [];
});
const activeChunkItem = computed(() => chunkPreviewItems.value[selectedChunkPos.value] || null);

// --- 图表 Refs ---
const tokenChartRef = ref<HTMLElement | null>(null);
const pieChartRef = ref<HTMLElement | null>(null);
const graphChartRef = ref<HTMLElement | null>(null);
const retrievalTrendChartRef = ref<HTMLElement | null>(null);
const systemHealthChartRef = ref<HTMLElement | null>(null);
let charts: echarts.ECharts[] =[];

// --- 方法：切换菜单 ---
const switchPage = async (pageId: string) => {
  rememberRecentMenu(pageId);
  activePage.value = pageId;
  if (pageId === 'dashboard') await fetchDashboard();
  if (pageId === 'retrieval') {
    await fetchRetrievalMetrics();
    await fetchRecentRetrievalRunDetails();
  }
  if (pageId === 'ai-models') {
    await fetchConfigs();
    await fetchRolloutConfig();
  }
  if (pageId === 'knowledge') await fetchDocs();
  if (pageId === 'analytics') {
    await fetchHotTopics();
    await fetchGraph();
  }
  if (pageId === 'users') await fetchUsers();
  if (pageId === 'audit') await fetchAuditLogs();
  if (pageId === 'system') await fetchSystemStatus();
};

const toggleGroup = (groupName: string) => {
  if (collapsedGroups.value.has(groupName)) collapsedGroups.value.delete(groupName);
  else collapsedGroups.value.add(groupName);
  collapsedGroups.value = new Set(collapsedGroups.value);
};

const rememberRecentMenu = (menuId: string) => {
  const filtered = [menuId, ...recentMenuIds.value.filter(id => id !== menuId)].slice(0, 6);
  recentMenuIds.value = filtered;
  localStorage.setItem('admin_recent_menus', JSON.stringify(filtered));
};

// --- 方法：登出 ---
const handleLogout = () => {
  localStorage.removeItem('access_token');
  router.push('/login');
};

// --- 方法：API 调用 ---
const fetchDashboard = async () => {
  const res = await request.get(ADMIN_API.STATS, { params: { days: dashboardDays.value } });
  dashStats.value = res.data;
  await nextTick();
  initTokenChart(res.data.chart);
};

const fetchNotifications = async () => {
  try {
    const res = await request.get(ADMIN_API.NOTIFICATIONS);
    notifications.value = res.data?.items || [];
    notificationUnreadCount.value = Number(res.data?.unread_count || 0);
  } catch (e: any) {
    notificationUnreadCount.value = 0;
  }
};

const markAllNotificationsRead = async () => {
  try {
    const keys = notifications.value.map(item => item.key).filter(Boolean);
    await request.post(ADMIN_API.NOTIFICATIONS_READ_ALL, { notice_keys: keys });
    await fetchNotifications();
    ElMessage.success('已全部标记已读');
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '操作失败');
  }
};

const jumpFromNotification = async (item: any) => {
  const pageId = String(item?.page_id || '').trim();
  if (!pageId) return;
  await switchPage(pageId);
};

const fetchRetrievalMetrics = async () => {
  loadingRetrieval.value = true;
  try {
    const res = await request.get(ADMIN_API.RETRIEVAL_METRICS, {
      params: {
        days: retrievalFilters.value.days,
        mode: retrievalFilters.value.mode || undefined,
        source: retrievalFilters.value.source || undefined,
      },
    });
    retrievalDashboard.value = {
      summary: res.data?.summary || {},
      trend: res.data?.trend || [],
      mode_breakdown: res.data?.mode_breakdown || [],
    };
    await nextTick();
    initRetrievalTrendChart(retrievalDashboard.value.trend);
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '检索看板加载失败');
  } finally {
    loadingRetrieval.value = false;
  }
};

const handleRetrievalFilterSearch = async () => {
  await fetchRetrievalMetrics();
  await fetchRecentRetrievalRunDetails();
};

const fetchRecentRetrievalRunDetails = async (notifyWhenEmpty: boolean = false) => {
  try {
    const res = await request.get(ADMIN_API.RETRIEVAL_RUN_RECENT, {
      params: {
        days: retrievalFilters.value.days,
        mode: retrievalFilters.value.mode || undefined,
        source: retrievalFilters.value.source || undefined,
        run_only: retrievalRunOnly.value,
        limit: 30,
      },
    });
    retrievalRunItems.value = res.data?.items || [];
    if (notifyWhenEmpty && retrievalRunItems.value.length === 0) {
      ElMessage.info('最近暂无 run 明细');
    }
  } catch (e: any) {
    retrievalRunItems.value = [];
    ElMessage.error(e?.response?.data?.detail || '最近 run 明细加载失败');
  }
};

const fetchRetrievalRunDetails = async () => {
  const runId = retrievalRunId.value.trim();
  if (!runId) {
    await fetchRecentRetrievalRunDetails(true);
    return;
  }
  try {
    const res = await request.get(ADMIN_API.RETRIEVAL_RUN(runId));
    retrievalRunItems.value = res.data?.items || [];
    if (retrievalRunItems.value.length === 0) {
      ElMessage.info('该 run_id 暂无明细，已展示最近 run');
      await fetchRecentRetrievalRunDetails();
    }
  } catch (e: any) {
    retrievalRunItems.value = [];
    ElMessage.error(e?.response?.data?.detail || 'run 明细查询失败');
  }
};

const fetchConfigs = async () => {
  const res = await request.get(ADMIN_API.CONFIGS);
  aiConfigs.value = res.data;
};

const fetchRolloutConfig = async () => {
  try {
    const res = await request.get(ADMIN_API.ROLLOUT);
    rolloutState.value = res.data || {};
    syncRolloutFormByType(rolloutForm.value.config_type);
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '灰度配置加载失败');
  }
};

const syncRolloutFormByType = (type: string) => {
  const mode = type === 'embedding' ? 'embedding' : 'llm';
  const current = mode === 'embedding' ? rolloutState.value?.embedding : rolloutState.value?.llm;
  rolloutForm.value = {
    config_type: mode,
    enabled: !!current?.enabled,
    baseline_config_id: Number(current?.baseline_config_id || 0),
    canary_config_id: Number(current?.canary_config_id || 0),
    ratio_pct: Number(current?.ratio_pct || 0),
  };
};

const saveRolloutConfig = async () => {
  if (!rolloutForm.value.baseline_config_id || !rolloutForm.value.canary_config_id) {
    ElMessage.warning('请先选择 baseline/canary');
    return;
  }
  try {
    await request.patch(ADMIN_API.ROLLOUT, rolloutForm.value);
    ElMessage.success('灰度配置已保存');
    await fetchRolloutConfig();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '灰度配置保存失败');
  }
};

const pingRolloutCompare = async () => {
  if (!rolloutForm.value.baseline_config_id || !rolloutForm.value.canary_config_id) {
    ElMessage.warning('请先选择 baseline/canary');
    return;
  }
  try {
    const res = await request.post(ADMIN_API.ROLLOUT_PING_COMPARE, null, {
      params: {
        config_type: rolloutForm.value.config_type,
        baseline_config_id: rolloutForm.value.baseline_config_id,
        canary_config_id: rolloutForm.value.canary_config_id,
      },
    });
    rolloutPingResult.value = res.data;
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '测速对比失败');
  }
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
  const [rawRes, enhancedRes] = await Promise.all([
    request.get('/v1/chat/knowledge_list'),
    request.get(ADMIN_API.KNOWLEDGE_DOCS_ENHANCED),
  ]);
  docs.value = rawRes.data || [];
  knowledgeEnhancedDocs.value = enhancedRes.data?.items || [];
};

const onUploadSuccess = (response: any) => {
  if (response.status === 'processing') ElMessage.success('已放入后台解析队列');
  else ElMessage.success('上传成功');
  fetchDocs();
};

const retryParseDoc = async (doc: any) => {
  try {
    await request.post(ADMIN_API.KNOWLEDGE_RETRY_PARSE(doc.id));
    ElMessage.success('已提交重试解析');
    await fetchDocs();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '重试解析失败');
  }
};

const rebuildBm25 = async () => {
  try {
    const res = await request.post(ADMIN_API.KNOWLEDGE_REBUILD_BM25);
    ElMessage.success(res.data?.message || '重建完成');
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '重建失败');
  }
};

const openChunkPreview = async (doc: any, page: number = 1) => {
  const safeId = Number(doc?.id || 0);
  if (!safeId) {
    ElMessage.warning('文档ID无效');
    return;
  }
  chunkPreviewVisible.value = true;
  chunkPreviewLoading.value = true;
  chunkPreviewDocId.value = safeId;
  chunkPreviewDocName.value = String(doc?.filename || '');
  chunkPreviewPage.value = Math.max(Number(page || 1), 1);
  selectedChunkPos.value = 0;
  try {
    const offset = (chunkPreviewPage.value - 1) * chunkPreviewPageSize;
    const res = await request.get(ADMIN_API.KNOWLEDGE_DOC_CHUNKS(safeId), {
      params: { limit: chunkPreviewPageSize, offset },
    });
    chunkPreviewItems.value = Array.isArray(res.data?.items) ? res.data.items : [];
    chunkPreviewTotal.value = Number(res.data?.total || 0);
    chunkPreviewDocName.value = String(res.data?.filename || doc?.filename || '');
  } catch (e: any) {
    chunkPreviewItems.value = [];
    chunkPreviewTotal.value = 0;
    ElMessage.error(e?.response?.data?.detail || '切片详情加载失败');
  } finally {
    chunkPreviewLoading.value = false;
  }
};

const handleChunkPageChange = async (page: number) => {
  if (!chunkPreviewDocId.value) return;
  await openChunkPreview({ id: chunkPreviewDocId.value, filename: chunkPreviewDocName.value }, page);
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
  try {
    const res = await request.get('/v1/chat/knowledge_graph');
    const data = res.data || {};
    graphStats.value = {
      node_count: Number(data?.node_count || (Array.isArray(data?.nodes) ? data.nodes.length : 0)),
      link_count: Number(data?.link_count || (Array.isArray(data?.links) ? data.links.length : 0)),
    };
    graphEmptyMessage.value = String(data?.message || '');
    await nextTick();
    if (!Array.isArray(data?.nodes) || data.nodes.length === 0) {
      initEmptyGraphChart(graphEmptyMessage.value || '暂无图谱数据');
      return;
    }
    initGraphChart(data);
  } catch (e: any) {
    graphStats.value = { node_count: 0, link_count: 0 };
    graphEmptyMessage.value = '图谱加载失败，请检查后端日志';
    await nextTick();
    initEmptyGraphChart(graphEmptyMessage.value);
  }
};

const handleBuildGraph = async () => {
  loadingGraph.value = true;
  try {
    const res = await request.post('/v1/chat/build_graph');
    const status = String(res.data?.status || '');
    const msg = String(res.data?.message || '图谱更新任务已启动');
    if (status === 'error') {
      ElMessage.warning(msg);
      await fetchGraph();
      return;
    }
    ElMessage.success(msg);
    for (let i = 0; i < 8; i++) {
      await new Promise(resolve => window.setTimeout(resolve, 2000));
      await fetchGraph();
      if (graphStats.value.node_count > 0) {
        break;
      }
    }
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

const fetchAuditLogs = async () => {
  loadingAuditLogs.value = true;
  try {
    const res = await request.get(ADMIN_API.AUDIT_LOGS, {
      params: {
        days: auditFilters.value.days,
        action: auditFilters.value.action || undefined,
        actor_user_id: auditFilters.value.actor_user_id || undefined,
        keyword: auditFilters.value.keyword || undefined,
        page: 1,
        page_size: 20,
      },
    });
    auditLogs.value = {
      page: res.data?.page || 1,
      page_size: res.data?.page_size || 20,
      total: res.data?.total || 0,
      items: res.data?.items || [],
      action_breakdown: res.data?.action_breakdown || [],
    };
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '审计日志加载失败');
  } finally {
    loadingAuditLogs.value = false;
  }
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
  await nextTick();
  initSystemHealthChart(sysStatus.value.health_history || []);
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

const initRetrievalTrendChart = (trend: any[]) => {
  if (!retrievalTrendChartRef.value) return;
  const chart = echarts.init(retrievalTrendChartRef.value);
  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['检索次数', '平均耗时(ms)'] },
    xAxis: { type: 'category', data: trend.map((item: any) => item.date) },
    yAxis: [
      { type: 'value', name: '次数' },
      { type: 'value', name: '耗时(ms)' },
    ],
    series: [
      {
        name: '检索次数',
        type: 'bar',
        data: trend.map((item: any) => item.total_queries || 0),
        itemStyle: { color: CHART_COLORS.primary },
      },
      {
        name: '平均耗时(ms)',
        type: 'line',
        yAxisIndex: 1,
        smooth: true,
        data: trend.map((item: any) => item.avg_latency_ms || 0),
        itemStyle: { color: CHART_COLORS.warning },
      },
    ],
  });
  charts.push(chart);
};

const initPieChart = (data: any[]) => {
  if (!pieChartRef.value) return;
  const chart = echarts.init(pieChartRef.value);
  if (!data.length) {
    chart.clear();
    chart.setOption({
      title: {
        text: '暂无聚类数据',
        left: 'center',
        top: 'middle',
        textStyle: { color: '#999', fontSize: 14, fontWeight: 'normal' },
      },
    });
    charts.push(chart);
    return;
  }
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

const initEmptyGraphChart = (message: string) => {
  if (!graphChartRef.value) return;
  const chart = echarts.init(graphChartRef.value);
  chart.clear();
  chart.setOption({
    title: {
      text: message || '暂无图谱数据',
      left: 'center',
      top: 'middle',
      textStyle: {
        color: '#8c8c8c',
        fontSize: 14,
        fontWeight: 'normal',
      },
    },
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

const initSystemHealthChart = (history: any[]) => {
  if (!systemHealthChartRef.value) return;
  const chart = echarts.init(systemHealthChartRef.value);
  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['成功率(%)', '失败数'] },
    xAxis: { type: 'category', data: history.map((row: any) => row.hour) },
    yAxis: [
      { type: 'value', name: '成功率' },
      { type: 'value', name: '失败数' },
    ],
    series: [
      {
        name: '成功率(%)',
        type: 'line',
        smooth: true,
        data: history.map((row: any) => row.success_rate || 0),
        itemStyle: { color: CHART_COLORS.success },
      },
      {
        name: '失败数',
        type: 'bar',
        yAxisIndex: 1,
        data: history.map((row: any) => row.failed_runs || 0),
        itemStyle: { color: CHART_COLORS.danger },
      },
    ],
  });
  charts.push(chart);
};

window.addEventListener('resize', () => charts.forEach(c => c.resize()));

const formatShortDate = (t: string) => t ? t.substring(0, 10) : '';
const formatDateTime = (t: string) => t ? t.replace('T', ' ').slice(0, 19) : '';
const formatPercent = (value: number) => `${((Number(value) || 0) * 100).toFixed(2)}%`;
const formatRate = (value: number) => `${((Number(value) || 0) * 100).toFixed(1)}%`;
const qualityScoreTagClass = (score: number) => {
  const safe = Number(score || 0);
  if (safe >= 85) return 'tag-success';
  if (safe >= 70) return 'tag-primary';
  if (safe >= 50) return 'tag-warning';
  return 'tag-error';
};
const parseRouteLabel = (meta: any) => {
  const route = String(meta?.route || '').trim();
  if (!route) return '-';
  if (route.includes('pdf_batch_smart_ocr_mixed')) return '智能分页+OCR混合';
  if (route.includes('pdf_batch_smart_no_ocr_dep_missing')) return '智能分页(缺OCR依赖)';
  if (route.includes('pdf_batch_smart_no_ocr')) return '智能分页(无OCR)';
  if (route.includes('scanned')) return '扫描PDF链路';
  if (route.includes('docling')) return 'Docling结构化';
  if (route.includes('txt_plain')) return '纯文本';
  return route;
};
const truncateText = (raw: string, maxChars: number = 120) => {
  const text = String(raw || '');
  if (text.length <= maxChars) return text;
  return `${text.slice(0, Math.max(1, maxChars - 1))}...`;
};
const renderChunkMarkdown = (raw: string) => {
  const normalized = String(raw || '')
    .replace(/<!--\s*image\s*-->/gi, '[图片内容]')
    .replace(/<\s*unknown\s*>/gi, '');
  return md.render(normalized);
};

onMounted(async () => {
  try {
    const res = await request.get('/v1/chat/me');
    currentUser.value = res.data;
  } catch (e) {
    console.error("获取管理员信息失败");
  }
  await fetchNotifications();
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
  .menu-group-clickable { display: flex; justify-content: space-between; cursor: pointer; user-select: none; }
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

.chunk-preview-body { display: flex; gap: 12px; min-height: 60vh; }
.chunk-preview-sidebar {
  width: 320px;
  border: 1px solid #eee;
  border-radius: 8px;
  padding: 8px;
  background: #fafafa;
}
.chunk-preview-item {
  border: 1px solid #efefef;
  border-radius: 6px;
  padding: 8px;
  margin-bottom: 8px;
  background: #fff;
  cursor: pointer;
}
.chunk-preview-item.active {
  border-color: #0052d9;
  background: rgba(0, 82, 217, 0.06);
}
.chunk-preview-title { font-size: 12px; color: #0052d9; font-weight: 600; margin-bottom: 4px; }
.chunk-preview-snippet { font-size: 12px; color: #555; line-height: 1.5; word-break: break-all; }
.chunk-preview-main {
  flex: 1;
  border: 1px solid #eee;
  border-radius: 8px;
  padding: 12px;
  background: #fff;
  overflow: auto;
}
.chunk-preview-meta { font-size: 12px; color: #666; margin-bottom: 8px; }
.chunk-preview-render {
  line-height: 1.8;
  color: #222;
  white-space: normal;
  word-break: break-word;
}

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
