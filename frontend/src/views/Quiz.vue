<template>
  <div class="quiz-wrapper">
    <div class="bg-glow"></div>
    
    <!-- 加载中状态 -->
    <div v-if="loading" class="loading-box glass-card">
      <el-icon class="is-loading" :size="40" color="#409eff"><Loading /></el-icon>
      <p>AI 正在根据法律知识库为您出题...</p>
    </div>

    <!-- 答题卡片 -->
    <div class="glass-card quiz-box" v-else-if="!finished && currentQuestion">
      <header>
        <div class="header-left">
          <el-tag type="primary" effect="dark">每日一练</el-tag>
          <span class="difficulty">难度: ⭐⭐⭐</span>
        </div>
        <span class="progress">进度 {{ currentIndex + 1 }} / {{ questions.length }}</span>
      </header>

      <div class="question-content">
        <h3 class="q-title">{{ currentQuestion.content }}</h3>
        
        <div class="options-list">
          <div 
            v-for="(opt, idx) in currentQuestion.options" 
            :key="idx"
            :class="['option-item', getOptionClass(idx)]"
            @click="selectOption(idx)"
          >
            <span class="opt-label">{{ getOptionLabel(idx) }}.</span>
            <span class="opt-text">{{ opt }}</span>
          </div>
        </div>
      </div>
      
      <!-- 解析区域 (答错或答对后显示) -->
      <transition name="el-fade-in">
        <div v-if="showResult" class="result-box">
          <div class="result-header">
            <el-icon v-if="isCorrect" color="#67C23A" :size="24"><CircleCheckFilled /></el-icon>
            <el-icon v-else color="#F56C6C" :size="24"><CircleCloseFilled /></el-icon>
            <span :class="isCorrect ? 'text-success' : 'text-error'">
              {{ isCorrect ? '回答正确！' : `回答错误，正确答案是 ${currentQuestion.correct_answer}` }}
            </span>
          </div>
          <div class="explanation">
            <strong>💡 解析：</strong>
            {{ currentQuestion.explanation }}
          </div>
          <div class="next-btn-wrapper">
            <el-button type="primary" @click="nextQuestion" round>
              {{ currentIndex === questions.length - 1 ? '查看成绩' : '下一题' }} <el-icon class="el-icon--right"><ArrowRight /></el-icon>
            </el-button>
          </div>
        </div>
      </transition>
    </div>

    <!-- 结算卡片 -->
    <div v-else-if="finished" class="glass-card result-card">
      <div class="score-circle">
        <span class="score-num">{{ score }}</span>
        <span class="score-label">分</span>
      </div>
      <h2>本次练习结束</h2>
      <p>{{ getComment(score) }}</p>
      
      <div class="action-buttons">
        <el-button @click="$router.push('/')">返回主页</el-button>
        <el-button type="primary" @click="restart">再练一次</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { Loading, CircleCheckFilled, CircleCloseFilled, ArrowRight } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import request from '../api/request';

// --- 1. 定义接口类型 (解决 TS 类型报错) ---
interface Question {
  id: number;
  content: string;
  options: string[];
  correct_answer: string;
  explanation: string;
  user_selected?: string; // 前端临时记录用户选了哪个
}

// --- 响应式数据 ---
// 明确指定 ref 的类型为 Question数组，解决 "never" 报错
const questions = ref<Question[]>([]); 
const currentIndex = ref(0);
const showResult = ref(false);
const isCorrect = ref(false);
const score = ref(0);
const finished = ref(false);
const loading = ref(true);

// 计算属性：当前题目
const currentQuestion = computed(() => {
  if (questions.value.length === 0) return null;
  return questions.value[currentIndex.value];
});

// --- 初始化 ---
const loadQuestions = async () => {
  loading.value = true;
  finished.value = false;
  currentIndex.value = 0;
  score.value = 0;
  showResult.value = false;
  questions.value = []; // 清空旧题

  try {
    const res = await request.get('/v1/quiz/daily');
    questions.value = res.data;
  } catch (e) {
    ElMessage.error('题目生成失败，请稍后再试');
  } finally {
    loading.value = false;
  }
};

onMounted(loadQuestions);

// --- 交互逻辑 ---

const getOptionLabel = (idx: number) => String.fromCharCode(65 + idx); // 0->A, 1->B

// 选中选项
const selectOption = async (idx: number) => {
  if (showResult.value || !currentQuestion.value) return; // 防止重复点击
  
  const optionLabel = getOptionLabel(idx);
  
  // 记录用户选择
  currentQuestion.value.user_selected = optionLabel;

  try {
    const res = await request.post('/v1/quiz/submit', {
      question_id: currentQuestion.value.id,
      selected_option: optionLabel
    });
    
    isCorrect.value = res.data.is_correct;
    if (isCorrect.value) score.value += 20; // 假设5题，每题20分
    showResult.value = true;
  } catch (e) {
    ElMessage.error('提交失败');
  }
};

// 下一题
const nextQuestion = () => {
  if (currentIndex.value < questions.value.length - 1) {
    currentIndex.value++;
    showResult.value = false;
    isCorrect.value = false;
  } else {
    finished.value = true;
  }
};

// 重新开始 (修复 restart 缺失报错)
const restart = () => {
  loadQuestions();
};

// 样式逻辑 (修复 opt 未使用报错)
const getOptionClass = (idx: number) => {
  if (!showResult.value || !currentQuestion.value) return 'default';
  
  const label = getOptionLabel(idx);
  const correct = currentQuestion.value.correct_answer;
  const selected = currentQuestion.value.user_selected;

  // 这里的逻辑：
  // 1. 如果是正确答案 -> 绿色
  // 2. 如果是用户选的且选错了 -> 红色
  if (label === correct) return 'correct';
  if (label === selected && label !== correct) return 'wrong';
  
  return 'disabled'; // 其他选项变淡
};

const getComment = (s: number) => {
  if (s == 100) return "太棒了！你是交通法专家！🏆";
  if (s >= 80) return "成绩不错，继续保持！🚗";
  if (s >= 60) return "及格了，有些盲区要注意哦。🚦";
  return "看来还需要多看看法规知识库呀。📚";
};
</script>

<style scoped lang="scss">
.quiz-wrapper {
  height: 100vh; width: 100vw; display: flex; justify-content: center; align-items: center;
  background: #f0f2f5; position: relative; overflow: hidden;
}

.bg-glow {
  position: absolute; width: 600px; height: 600px; background: radial-gradient(circle, rgba(64, 158, 255, 0.1) 0%, transparent 70%);
  top: -100px; right: -100px; z-index: 0;
}

.glass-card {
  width: 90%; max-width: 600px; background: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(20px); border-radius: 24px; padding: 30px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.08); z-index: 1; border: 1px solid rgba(255,255,255,0.8);
}

/* Loading */
.loading-box { text-align: center; color: #666; p { margin-top: 15px; } }

/* 题目样式 */
header {
  display: flex; justify-content: space-between; align-items: center; margin-bottom: 25px;
  .progress { font-size: 14px; color: #999; font-weight: bold; }
}

.q-title { font-size: 18px; color: #333; line-height: 1.6; margin-bottom: 25px; min-height: 60px; }

.option-item {
  padding: 16px 20px; margin-bottom: 12px; border: 2px solid #f0f2f5;
  border-radius: 12px; cursor: pointer; transition: all 0.2s;
  display: flex; align-items: center; gap: 12px; background: #fff;
  
  .opt-label { 
    width: 28px; height: 28px; background: #f0f2f5; color: #666; 
    border-radius: 50%; display: flex; justify-content: center; align-items: center; font-weight: bold;
  }
  .opt-text { flex: 1; font-size: 15px; color: #555; }

  &:hover { border-color: #409eff; background: #ecf5ff; .opt-label { background: #409eff; color: #fff; } }

  &.correct { border-color: #67C23A; background: #f0f9eb; .opt-label { background: #67C23A; color: #fff; } }
  &.wrong { border-color: #F56C6C; background: #fef0f0; .opt-label { background: #F56C6C; color: #fff; } }
  &.disabled { opacity: 0.5; pointer-events: none; }
}

/* 解析区域 */
.result-box {
  margin-top: 25px; padding: 20px; background: #f8f9fa; border-radius: 12px; border: 1px solid #eee;
  .result-header { 
    display: flex; align-items: center; gap: 8px; font-size: 16px; font-weight: bold; margin-bottom: 10px;
    .text-success { color: #67C23A; } .text-error { color: #F56C6C; }
  }
  .explanation { font-size: 14px; color: #666; line-height: 1.6; margin-bottom: 20px; }
  .next-btn-wrapper { text-align: right; }
}

/* 结算页 */
.result-card {
  text-align: center;
  .score-circle {
    width: 120px; height: 120px; border-radius: 50%; border: 6px solid #409eff;
    margin: 0 auto 20px; display: flex; justify-content: center; align-items: center; flex-direction: column;
    .score-num { font-size: 40px; font-weight: 800; color: #409eff; }
    .score-label { font-size: 12px; color: #999; }
  }
  h2 { margin-bottom: 10px; }
  p { color: #666; margin-bottom: 30px; }
  .action-buttons { display: flex; justify-content: center; gap: 20px; }
}
</style>