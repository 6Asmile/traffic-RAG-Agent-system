// frontend/src/config/constants.ts

// 后台左侧菜单配置
export const ADMIN_MENUS = [
  {
    group: '核心业务',
    items:[
      { id: 'dashboard', icon: 'DataLine', label: '数据概览看板' },
      { id: 'ai-models', icon: 'Cpu', label: 'AI引擎控制中枢' },
      { id: 'knowledge', icon: 'Collection', label: '知识库管家' },
      { id: 'analytics', icon: 'PieChart', label: '舆情挖掘图谱' },
    ]
  },
  {
    group: '系统管理',
    items:[
      { id: 'users', icon: 'User', label: '用户与权限管理' },
      { id: 'system', icon: 'Monitor', label: '系统链路监控' },
    ]
  }
];

// 图表主题颜色常量
export const CHART_COLORS = {
  primary: '#0052d9',
  success: '#2ba471',
  warning: '#e37318',
  danger: '#d54941',
  pieColors:['#d54941', '#e37318', '#0052d9', '#2ba471', '#999999']
};

// API 接口地址抽取
export const ADMIN_API = {
  STATS: '/v1/admin/dashboard/stats',
  CONFIGS: '/v1/admin/configs',
  ACTIVATE_CONFIG: (id: number) => `/v1/admin/configs/${id}/activate`,
  USERS: '/v1/admin/users',
  SYS_STATUS: '/v1/admin/system/status'
};