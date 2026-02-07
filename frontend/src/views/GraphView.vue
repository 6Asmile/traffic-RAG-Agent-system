<template>
  <div class="graph-wrapper">
    <!-- 背景装饰 -->
    <div class="bg-glow"></div>
    
    <div class="glass-card">
      <header class="graph-header">
        <div class="title-area">
          <el-icon class="mesh-icon"><Share /></el-icon>
          <div class="text">
            <h2>交通法规逻辑图谱</h2>
            <p>交互式实体关系网络 · 实时逻辑提取</p>
          </div>
        </div>
        <div class="actions">
          <el-button @click="$router.push('/')" circle :icon="HomeFilled" />
          <el-button @click="initChart" circle :icon="RefreshRight" title="重置布局" />
          <el-button @click="$router.back()" circle :icon="Close" />
        </div>
      </header>

      <!-- 图表容器 -->
      <div ref="chartRef" class="chart-container"></div>

      <!-- 底部图例（移动端自适应） -->
      <footer class="graph-footer">
        <div class="legend-bar">
          <div v-for="(color, cat) in categoryColors" :key="cat" class="legend-item">
            <span class="dot" :style="{ backgroundColor: color }"></span>
            <span class="label">{{ categoryMap[cat] || cat }}</span>
          </div>
        </div>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick, computed } from 'vue';
import { Share, ArrowLeft, RefreshRight, Close,HomeFilled } from '@element-plus/icons-vue';
import * as echarts from 'echarts';
import request from '../api/request';

const chartRef = ref<HTMLElement | null>(null);
let myChart: echarts.ECharts | null = null;

// 1. 定义更高级的配色方案
const categoryColors: any = {
  'VIOLATION': '#5470c6', // 违法行为 - 深蓝
  'PENALTY': '#91cc75',   // 处罚措施 - 翠绿
  'BASIS': '#fac858',     // 法律依据 - 金黄
  'MONEY': '#ee6666',     // 罚款金额 - 珊瑚红
  'LIMIT': '#73c0de',     // 限制条件 - 天蓝
  'VEHICLE': '#3ba272'    // 车辆类型 - 墨绿
};

const categoryMap: any = {
  'VIOLATION': '违法行为',
  'PENALTY': '处罚措施',
  'BASIS': '法律依据',
  'MONEY': '罚款金额',
  'LIMIT': '限制条件',
  'VEHICLE': '车辆类型'
};

// 适配移动端参数
const isMobile = window.innerWidth < 768;

const initChart = async () => {
  const res = await request.get('/v1/chat/knowledge_graph');
  const data = res.data;

  if (!chartRef.value) return;
  if (myChart) myChart.dispose();
  
  myChart = echarts.init(chartRef.value);

  const option: any = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(255, 255, 255, 0.9)',
      borderWidth: 0,
      textStyle: { color: '#333' },
      formatter: (params: any) => {
        if (params.dataType === 'node') {
          return `<div style="padding:5px"><b>类别:</b> ${categoryMap[params.data.category] || params.data.category}<br/><b>名称:</b> ${params.name}</div>`;
        }
        return `关系: ${params.data.value}`;
      }
    },
    // 动画平滑设置
    animationDurationUpdate: 1500,
    animationEasingUpdate: 'quinticInOut',
    series: [{
      type: 'graph',
      layout: 'force',
      data: data.nodes.map((n: any) => ({
        name: n.name,
        category: n.category,
        symbolSize: n.category === 'VIOLATION' ? (isMobile ? 40 : 65) : (isMobile ? 25 : 45),
        itemStyle: {
          color: categoryColors[n.category] || '#999',
          shadowBlur: 10,
          shadowColor: 'rgba(0,0,0,0.2)',
          borderWidth: 2,
          borderColor: '#fff'
        },
        label: {
          show: !isMobile || n.category === 'VIOLATION', // 移动端只显示核心标签
          position: 'bottom',
          distance: 5,
          fontSize: isMobile ? 10 : 12,
          color: '#444'
        }
      })),
      links: data.links.map((l: any) => ({
        source: l.source,
        target: l.target,
        value: l.value,
        lineStyle: {
          color: 'source',
          curveness: 0.2, // 曲线增加美感
          width: 1.5,
          opacity: 0.4
        },
        label: {
          show: false, // 默认隐藏连线文字，解决闪烁
          formatter: l.value,
          fontSize: 9
        },
        emphasis: {
          lineStyle: { width: 4, opacity: 1 },
          label: { show: true } // 仅在悬停时显示关系
        }
      })),
      categories: Object.keys(categoryColors).map(key => ({ name: key })),
      roam: true, // 开启缩放和平移
      draggable: true,
      focusNodeAdjacency: true, // 鼠标悬停高亮邻居节点
      // --- 关键：力导向性能优化，解决抖动 ---
      force: {
        repulsion: isMobile ? 400 : 1200, // 斥力
        gravity: 0.1,                     // 向心力
        edgeLength: isMobile ? 100 : 180, // 线长
        layoutAnimation: true             // 开启布局动画
      },
      emphasis: {
        scale: true,
        focus: 'adjacency', // 聚焦邻接节点，背景变暗
        itemStyle: {
          shadowBlur: 20,
          shadowColor: 'rgba(0,0,0,0.3)'
        }
      }
    }]
  };

  myChart.setOption(option);
};

// 处理窗口缩放
const handleResize = () => {
  myChart?.resize();
};

onMounted(() => {
  nextTick(() => {
    initChart();
    window.addEventListener('resize', handleResize);
  });
});

onUnmounted(() => {
  window.removeEventListener('resize', handleResize);
  myChart?.dispose();
});
</script>

<style scoped lang="scss">
.graph-wrapper {
  height: 100vh;
  width: 100vw;
  background: #f4f7f9;
  display: flex;
  justify-content: center;
  align-items: center;
  position: relative;
  overflow: hidden;
}

.bg-glow {
  position: absolute;
  width: 800px;
  height: 800px;
  background: radial-gradient(circle, rgba(64, 158, 255, 0.12) 0%, transparent 70%);
  top: -200px;
  right: -200px;
  z-index: 0;
}

.glass-card {
  width: 95%;
  height: 92%;
  background: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(20px);
  border-radius: 24px;
  border: 1px solid rgba(255, 255, 255, 0.6);
  box-shadow: 0 30px 60px rgba(0, 0, 0, 0.08);
  display: flex;
  flex-direction: column;
  z-index: 1;
  overflow: hidden;

  @media (max-width: 768px) {
    width: 100%;
    height: 100%;
    border-radius: 0;
  }
}

.graph-header {
  padding: 20px 30px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid rgba(0, 0, 0, 0.05);

  @media (max-width: 768px) {
    padding: 15px 20px;
  }

  .title-area {
    display: flex;
    align-items: center;
    gap: 15px;

    .mesh-icon {
      font-size: 28px;
      color: #409eff;
      padding: 10px;
      background: rgba(64, 158, 255, 0.1);
      border-radius: 12px;
    }

    h2 { margin: 0; font-size: 22px; color: #2c3e50; }
    p { margin: 2px 0 0 0; font-size: 13px; color: #909399; }
    
    @media (max-width: 768px) {
      h2 { font-size: 18px; }
      p { display: none; }
    }
  }
}

.chart-container {
  flex: 1;
  width: 100%;
  cursor: grab;
  &:active { cursor: grabbing; }
}

.graph-footer {
  padding: 15px;
  background: rgba(255, 255, 255, 0.5);
  border-top: 1px solid rgba(0, 0, 0, 0.03);

  .legend-bar {
    display: flex;
    justify-content: center;
    flex-wrap: wrap;
    gap: 15px;

    .legend-item {
      display: flex;
      align-items: center;
      gap: 6px;
      .dot { width: 10px; height: 10px; border-radius: 50%; }
      .label { font-size: 12px; color: #606266; font-weight: 500; }
    }
  }
}

/* 隐藏移动端 ECharts 可能出现的空白 */
:deep(.echarts-tooltip) {
  border-radius: 12px !important;
  box-shadow: 0 10px 20px rgba(0,0,0,0.1) !important;
}

// 动画：卡片入场
.glass-card {
  animation: slideUp 0.6s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes slideUp {
  from { opacity: 0; transform: translateY(30px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>