<template>
  <div class="quiz-wrapper">
    <div class="bg-glow"></div>
    
    <!-- 加载状态 -->
    <div v-if="loading" class="loading-container">
      <div class="loader-content">
        <el-icon class="is-loading" :size="48" color="#fff"><Loading /></el-icon>
        <p>AI 考官正在为您生成专属考题...</p>
      </div>
    </div>

    <!-- 答题卡片容器 -->
    <div class="glass-card quiz-card" v-else-if="!finished && currentQuestion">
      
      <!-- 1. 顶部固定区 -->
      <div class="quiz-header">
        <div class="header-top">
          <!-- 返回按钮 -->
          <div class="left-action" @click="handleExit">
            <el-icon :size="22"><ArrowLeftBold /></el-icon>
          </div>

          <div class="badge-group">
            <span class="tag-badge">每日一练</span>
            <!-- 【核心修改】难度根据题目自带属性动态渲染星星 -->
            <span class="difficulty">难度 {{ '⭐'.repeat(currentQuestion.difficulty || 3) }}</span>
          </div>
          
          <span class="step-indicator">{{ currentIndex + 1 }} / {{ questions.length }}</span>
        </div>
        
        <!-- 进度条 -->
        <div class="progress-track">
          <div class="progress-fill" :style="{ width: `${((currentIndex + 1) / questions.length) * 100}%` }"></div>
        </div>
      </div>

      <!-- 2. 中间滚动区 -->
      <div class="quiz-body custom-scrollbar">
        <div class="question-text">
          <span class="q-type-label" v-if="currentQuestion.content.includes('案例')">案例分析</span>
          {{ currentQuestion.content }}
        </div>
        
        <div class="options-group">
          <div 
            v-for="(opt, idx) in currentQuestion.options" 
            :key="idx"
            :class="['option-card', getOptionClass(idx)]"
            @click="selectOption(idx)"
          >
            <div class="opt-letter">
              <el-icon v-if="isSubmitting && currentQuestion.user_selected === getOptionLabel(idx)" class="is-loading">
                <Loading />
              </el-icon>
              <span v-else>{{ getOptionLabel(idx) }}</span>
            </div>
            <div class="opt-content">{{ opt }}</div>
            
            <div class="status-icon" v-if="showResult">
              <el-icon v-if="getOptionClass(idx) === 'correct'"><CircleCheckFilled /></el-icon>
              <el-icon v-if="getOptionClass(idx) === 'wrong'"><CircleCloseFilled /></el-icon>
            </div>
          </div>
        </div>

        <!-- 3. 解析区 -->
        <transition name="slide-up">
          <div v-if="showResult" class="result-panel">
            <div :class="['result-banner', isCorrect ? 'success' : 'error']">
              <span class="result-title">{{ isCorrect ? '🎉 回答正确' : '⚠️ 回答错误' }}</span>
              <span class="result-sub" v-if="!isCorrect">正确答案：{{ currentQuestion.correct_answer }}</span>
            </div>
            
            <div class="explanation-box">
              <div class="exp-title"><el-icon><Reading /></el-icon> 权威解析</div>
              <div class="exp-text">{{ currentQuestion.explanation }}</div>
            </div>

            <div class="next-action">
              <el-button type="primary" size="large" class="next-btn" @click="nextQuestion" round>
                {{ currentIndex === questions.length - 1 ? '查看成绩单' : '下一题' }} 
                <el-icon class="el-icon--right"><ArrowRight /></el-icon>
              </el-button>
            </div>
          </div>
        </transition>
        
        <div class="safe-area-padding"></div>
      </div>
    </div>

    <!-- 结算卡片 -->
    <div v-else-if="finished" class="glass-card result-card">
      <div class="result-content">
        <div class="score-ring">
          <svg viewBox="0 0 100 100">
            <circle cx="50" cy="50" r="45" stroke="#eee" stroke-width="8" fill="none"/>
            <circle cx="50" cy="50" r="45" stroke="#409eff" stroke-width="8" fill="none" 
              :stroke-dasharray="283" :stroke-dashoffset="283 - (283 * score / 100)" stroke-linecap="round" class="progress-ring"/>
          </svg>
          <div class="score-text">
            <span class="num">{{ score }}</span>
            <span class="unit">分</span>
          </div>
        </div>
        
        <h2>练习完成</h2>
        <p class="comment">{{ getComment(score) }}</p>
        
        <div class="result-actions">
          <el-button class="action-btn home" @click="$router.push('/')" plain round>返回主页</el-button>
          <el-button class="action-btn retry" type="primary" @click="restart" round>再练一次</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { Loading, CircleCheckFilled, CircleCloseFilled, ArrowRight, ArrowLeftBold, Reading } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import request from '../api/request';

const router = useRouter();

interface Question {
  id: number;
  content: string;
  options: string[];
  correct_answer: string;
  explanation: string;
  difficulty: number; // 确保接口里包含难度字段
  user_selected?: string;
}

const questions = ref<Question[]>([]); 
const currentIndex = ref(0);
const showResult = ref(false);
const isCorrect = ref(false);
const score = ref(0);
const finished = ref(false);
const loading = ref(true);
const isSubmitting = ref(false); 

const currentQuestion = computed(() => {
  if (questions.value.length === 0) return null;
  return questions.value[currentIndex.value];
});

const loadQuestions = async () => {
  loading.value = true;
  finished.value = false;
  currentIndex.value = 0;
  score.value = 0;
  showResult.value = false;
  isSubmitting.value = false;
  questions.value =[];

  try {
    const res = await request.get('/v1/quiz/daily');
    questions.value = res.data;
  } catch (e) {
    ElMessage.error('获取题目失败，请检查网络');
    router.push('/'); 
  } finally {
    loading.value = false;
  }
};

onMounted(loadQuestions);

const getOptionLabel = (idx: number) => String.fromCharCode(65 + idx);

const selectOption = async (idx: number) => {
  if (showResult.value || !currentQuestion.value || isSubmitting.value) return; 
  
  const optionLabel = getOptionLabel(idx);
  currentQuestion.value.user_selected = optionLabel;
  isSubmitting.value = true; 

  try {
    const res = await request.post('/v1/quiz/submit', {
      question_id: currentQuestion.value.id,
      selected_option: optionLabel
    });
    
    isCorrect.value = res.data.is_correct;
    
    // 【核心修改】因为改成了 10 题，所以每题分值变为 10 分
    if (isCorrect.value) score.value += 10; 
    
    showResult.value = true;
    
    setTimeout(() => {
      const body = document.querySelector('.quiz-body');
      if (body) body.scrollTo({ top: body.scrollHeight, behavior: 'smooth' });
    }, 100);

  } catch (e) {
    ElMessage.error('提交失败，请重试');
  } finally {
    isSubmitting.value = false; 
  }
};

const nextQuestion = () => {
  if (currentIndex.value < questions.value.length - 1) {
    currentIndex.value++;
    showResult.value = false;
    isCorrect.value = false;
    const body = document.querySelector('.quiz-body');
    if (body) body.scrollTop = 0;
  } else {
    finished.value = true;
  }
};

const restart = () => {
  loadQuestions();
};

const handleExit = () => {
  ElMessageBox.confirm(
    '当前练习进度将不会保存，确定退出吗？',
    '退出练习',
    {
      confirmButtonText: '确定退出',
      cancelButtonText: '继续做题',
      type: 'warning',
    }
  ).then(() => {
    router.push('/');
  }).catch(() => {});
};

const getOptionClass = (idx: number) => {
  if (!showResult.value || !currentQuestion.value) return 'default';
  
  const label = getOptionLabel(idx);
  const correct = currentQuestion.value.correct_answer;
  const selected = currentQuestion.value.user_selected;

  if (label === correct) return 'correct';
  if (label === selected && label !== correct) return 'wrong';
  
  return 'disabled';
};

// 【核心修改】调整 10 道题的评语阈值
const getComment = (s: number) => {
  if (s === 100) return "满分通关！您就是活着的交规词典！🏆";
  if (s >= 80) return "成绩优秀，只有极少数知识盲区！🚗";
  if (s >= 60) return "及格了，但上路前还需多加复习哦。🚦";
  return "马路杀手警告！建议去知识库多看看法条。📚";
};
</script>

<style scoped lang="scss">
/* 保持你之前那份美化后的 CSS，稍微调整 Header 布局以适配返回按钮 */

.quiz-wrapper {
  height: 100vh;
  width: 100vw;
  background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
  display: flex;
  justify-content: center;
  align-items: center;
  position: relative;
  overflow: hidden;
}

.bg-glow {
  position: absolute;
  width: 100%; height: 100%;
  background: radial-gradient(circle at 50% 10%, rgba(64,158,255,0.2), transparent 60%);
  pointer-events: none;
}

.loading-container {
  display: flex; align-items: center; justify-content: center;
  height: 100%; width: 100%;
  .loader-content {
    text-align: center; color: #fff;
    background: rgba(0,0,0,0.3); padding: 30px; border-radius: 20px;
    backdrop-filter: blur(10px);
    p { margin-top: 15px; font-weight: 500; letter-spacing: 1px; }
  }
}

.glass-card {
  background: rgba(255, 255, 255, 0.95);
  box-shadow: 0 10px 40px rgba(0,0,0,0.1);
  border-radius: 24px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.quiz-card {
  width: 600px;
  height: 80vh;
  max-width: 95%;
  
  @media (max-width: 768px) {
    width: 100%;
    height: 100vh;
    border-radius: 0;
  }
}

/* 优化后的 Header */
.quiz-header {
  padding: 15px 24px 0;
  flex-shrink: 0;
  background: #fff;
  z-index: 2;

  .header-top {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 15px;
    position: relative;
  }

  /* 返回按钮 */
  .left-action {
    display: flex; align-items: center; justify-content: center;
    width: 32px; height: 32px; border-radius: 50%;
    background: #f4f4f5; color: #606266;
    cursor: pointer; transition: 0.2s;
    &:active { background: #e9e9eb; transform: scale(0.9); }
  }

  .badge-group {
    display: flex; gap: 8px; align-items: center;
    position: absolute; left: 50%; transform: translateX(-50%); /* 居中 */
    
    .tag-badge { 
      background: #409eff; color: #fff; padding: 2px 8px; border-radius: 6px; 
      font-size: 12px; font-weight: bold;
    }
    .difficulty { font-size: 12px; color: #f7ba2a; }
  }

  .step-indicator { font-size: 14px; font-weight: 800; color: #909399; letter-spacing: 1px; }
  
  .progress-track {
    height: 4px; background: #f0f2f5; border-radius: 2px; overflow: hidden; margin-bottom: 20px;
    .progress-fill { height: 100%; background: #409eff; transition: width 0.3s ease; }
  }
}

.quiz-body {
  flex: 1;
  overflow-y: auto;
  padding: 0 24px 20px;
  -webkit-overflow-scrolling: touch;

  .question-text {
    font-size: 17px; color: #2c3e50; line-height: 1.8; margin-bottom: 25px;
    font-weight: 500;
    .q-type-label {
      display: inline-block; background: #e6f7ff; color: #1890ff; 
      padding: 0 6px; border-radius: 4px; font-size: 12px; margin-right: 6px; vertical-align: 2px;
    }
  }
}

.options-group { display: flex; flex-direction: column; gap: 12px; }

.option-card {
  background: #fff; border: 1.5px solid #ebeef5; border-radius: 12px;
  padding: 16px; display: flex; align-items: flex-start; gap: 12px;
  cursor: pointer; transition: all 0.2s cubic-bezier(0.25, 0.8, 0.25, 1);
  position: relative; overflow: hidden;

  .opt-letter {
    width: 28px; height: 28px; background: #f4f4f5; color: #606266;
    border-radius: 8px; display: flex; align-items: center; justify-content: center;
    font-weight: bold; font-size: 14px; flex-shrink: 0; transition: 0.2s;
  }
  .opt-content { flex: 1; font-size: 15px; color: #555; line-height: 1.5; }

  @media (min-width: 769px) {
    &:hover { border-color: #c6e2ff; transform: translateX(5px); .opt-letter { background: #ecf5ff; color: #409eff; } }
  }
  &:active { transform: scale(0.98); background: #fafafa; }

  &.correct {
    border-color: #67c23a; background: #f0f9eb;
    .opt-letter { background: #67c23a; color: #fff; }
    .opt-content { color: #67c23a; font-weight: 500; }
  }
  &.wrong {
    border-color: #f56c6c; background: #fef0f0;
    .opt-letter { background: #f56c6c; color: #fff; }
    .opt-content { color: #f56c6c; }
  }
  &.disabled { opacity: 0.6; pointer-events: none; filter: grayscale(100%); }

  .status-icon {
    position: absolute; right: 10px; top: 50%; transform: translateY(-50%);
    font-size: 20px;
    .el-icon { vertical-align: middle; }
  }
}

.result-panel {
  margin-top: 30px; border-top: 1px solid #eee; padding-top: 20px;
  animation: slideUp 0.4s ease-out;

  .result-banner {
    padding: 12px 16px; border-radius: 8px; margin-bottom: 20px;
    display: flex; justify-content: space-between; align-items: center;
    &.success { background: #f0f9eb; color: #67c23a; }
    &.error { background: #fef0f0; color: #f56c6c; flex-direction: column; align-items: flex-start; gap: 5px;}
    .result-title { font-weight: 800; font-size: 16px; }
    .result-sub { font-size: 13px; opacity: 0.9; }
  }

  .explanation-box {
    background: #f8f9fa; border-radius: 12px; padding: 15px;
    .exp-title { display: flex; align-items: center; gap: 5px; font-weight: bold; color: #303133; margin-bottom: 8px; }
    .exp-text { font-size: 14px; color: #606266; line-height: 1.6; text-align: justify; }
  }

  .next-action { margin-top: 20px; text-align: center; }
  .next-btn { width: 100%; box-shadow: 0 4px 12px rgba(64,158,255,0.3); }
}

.safe-area-padding { height: 40px; }

.result-card {
  width: 400px; padding: 40px; text-align: center;
  
  .score-ring {
    position: relative; width: 140px; height: 140px; margin: 0 auto 25px;
    svg { transform: rotate(-90deg); width: 100%; height: 100%; }
    .progress-ring { transition: stroke-dashoffset 1s ease-in-out; }
    .score-text {
      position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
      display: flex; align-items: baseline;
      .num { font-size: 48px; font-weight: 800; color: #2c3e50; }
      .unit { font-size: 16px; color: #999; margin-left: 2px; }
    }
  }

  h2 { font-size: 22px; color: #333; margin-bottom: 10px; }
  .comment { color: #666; margin-bottom: 30px; font-size: 14px; }

  .result-actions {
    display: flex; gap: 15px;
    .action-btn { flex: 1; height: 44px; font-size: 15px; }
  }
}

@keyframes slideUp {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}

.custom-scrollbar::-webkit-scrollbar { width: 4px; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #dcdfe6; border-radius: 4px; }
</style>