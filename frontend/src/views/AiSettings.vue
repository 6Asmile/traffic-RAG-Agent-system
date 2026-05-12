<template>
  <div class="glass-provider">
    <div class="bg-glow"></div>

    <div class="app-container">
      <!-- 顶部导航 -->
      <header class="page-header">
        <el-button :icon="ArrowLeft" circle @click="$router.back()" />
        <div class="header-title">
          <h2>AI 引擎配置中心</h2>
          <p>Bring Your Own Key (BYOK)</p>
        </div>
        <el-button type="primary" :loading="saving" round @click="saveSettings">保存</el-button>
      </header>

      <div class="settings-content custom-scrollbar">
        <div class="tip-banner">
          <el-icon><InfoFilled /></el-icon>
          <span>提示：您可以为不同场景选择专属的 AI 模型。API Key 若留空，则默认消耗系统公共额度。</span>
        </div>

        <!-- 1. 文本大模型设置 -->
        <section class="config-section">
          <div class="section-title">
            <div class="icon-box llm-icon"><el-icon><ChatDotRound /></el-icon></div>
            <h3>文本对话大模型 (LLM)</h3>
          </div>
          <div class="glass-card">
            <div class="form-group">
              <label>首选模型提供商</label>
              <div class="model-grid">
                <div 
                  v-for="m in llmModels" :key="m.id" 
                  :class="['model-card', form.llm_model === m.id ? 'active' : '']"
                  @click="form.llm_model = m.id"
                >
                  <img :src="m.logo" class="m-logo" />
                  <span class="m-name">{{ m.name }}</span>
                </div>
              </div>
            </div>
            <div class="form-group">
              <label>个人 API Key (可选)</label>
              <el-input 
                v-model="form.llm_key" 
                type="password" 
                show-password 
                placeholder="sk-... (留空则使用系统默认)" 
                class="modern-input"
              />
            </div>
          </div>
        </section>

        <!-- 2. 向量大模型设置 -->
        <section class="config-section">
          <div class="section-title">
            <div class="icon-box embed-icon"><el-icon><DataLine /></el-icon></div>
            <h3>向量检索模型 (Embedding)</h3>
          </div>
          <div class="glass-card">
            <div class="form-group">
              <label>向量化模型选择</label>
              <el-select v-model="form.embed_model" class="modern-select" style="width: 100%">
                <el-option label="阿里通义 Text-Embedding-V4" value="text-embedding-v4" />
                <el-option label="OpenAI text-embedding-3-small" value="text-embedding-3-small" />
              </el-select>
            </div>
            <div class="form-group">
              <label>个人 API Key (可选)</label>
              <el-input v-model="form.embed_key" type="password" show-password placeholder="留空则使用系统默认" class="modern-input"/>
            </div>
          </div>
        </section>

        <!-- 3. 多模态视觉大模型设置 -->
        <section class="config-section">
          <div class="section-title">
            <div class="icon-box vision-icon"><el-icon><Picture /></el-icon></div>
            <h3>多模态视觉模型 (Vision)</h3>
            <el-tag size="small" type="warning" round>即将开放</el-tag>
          </div>
          <div class="glass-card disabled-card">
            <div class="form-group">
              <label>图像分析模型</label>
              <el-select v-model="form.vision_model" class="modern-select" style="width: 100%">
                <el-option label="阿里 Qwen-VL-Max" value="qwen-vl-max" />
                <el-option label="OpenAI GPT-4o" value="gpt-4o" />
              </el-select>
            </div>
            <div class="form-group">
              <label>个人 API Key (可选)</label>
              <el-input v-model="form.vision_key" type="password" show-password placeholder="留空则使用系统默认" class="modern-input"/>
            </div>
          </div>
        </section>

        <div class="bottom-spacer"></div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { ArrowLeft, InfoFilled, ChatDotRound, DataLine, Picture } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import request from '../api/request';

const saving = ref(false);

// 替换成了网络上稳定可用的Logo图片地址
const llmModels =[
  { 
    id: 'deepseek-chat', 
    name: 'DeepSeek V3', 
    logo: 'https://uxwing.com/wp-content/themes/uxwing/download/brands-and-social-media/deepseek-logo-icon.png' 
  },
  { 
    id: 'qwen-max', 
    name: '通义千问 Max', 
    logo: 'https://upload.wikimedia.org/wikipedia/commons/6/69/Qwen_logo.svg' 
  },
  { 
    id: 'gpt-4o', 
    name: 'GPT-4o', 
    logo: 'https://upload.wikimedia.org/wikipedia/commons/0/04/ChatGPT_logo.svg' 
  },
  { 
    id: 'mimo-v2.5-pro', 
    name: 'MiMo V2.5 Pro', 
    logo: 'https://img.icons8.com/3d-fluency/94/artificial-intelligence.png' 
  },
];

const form = ref({
  llm_model: 'deepseek-chat',
  llm_key: '',
  embed_model: 'text-embedding-v4',
  embed_key: '',
  vision_model: 'qwen-vl-max',
  vision_key: ''
});

onMounted(async () => {
  try {
    const res = await request.get('/v1/chat/ai_settings');
    form.value = res.data;
  } catch (e) {
    ElMessage.error('加载配置失败');
  }
});

const saveSettings = async () => {
  saving.value = true;
  try {
    await request.post('/v1/chat/ai_settings', form.value);
    ElMessage.success('配置已保存，将在下次对话中生效');
  } catch (e) {
    ElMessage.error('保存失败');
  } finally {
    saving.value = false;
  }
};
</script>

<style scoped lang="scss">
.glass-provider {
  height: 100vh; width: 100vw; background: #f0f2f5; display: flex; justify-content: center; align-items: center; position: relative; overflow: hidden;
}

.bg-glow { position: absolute; width: 600px; height: 600px; background: radial-gradient(circle, rgba(64, 158, 255, 0.15) 0%, transparent 70%); filter: blur(80px); top: -100px; right: -100px; z-index: 0; }

.app-container {
  width: 95%; max-width: 800px; height: 95%; background: rgba(255, 255, 255, 0.75); backdrop-filter: blur(20px);
  border-radius: 24px; border: 1px solid rgba(255, 255, 255, 0.6); display: flex; flex-direction: column; box-shadow: 0 20px 50px rgba(0,0,0,0.05); z-index: 1; overflow: hidden;
  
  @media (max-width: 768px) { width: 100%; height: 100%; border-radius: 0; }
}

.page-header {
  padding: 20px 24px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(0,0,0,0.05);
  .header-title { text-align: center; h2 { margin: 0; font-size: 18px; color: #333; } p { margin: 2px 0 0; font-size: 11px; color: #999; } }
}

.settings-content {
  flex: 1; padding: 24px; overflow-y: auto;
  @media (max-width: 768px) { padding: 16px; }
}

.tip-banner {
  display: flex; align-items: flex-start; gap: 8px; padding: 12px 16px; background: #e6f1fc; color: #409eff; border-radius: 12px; font-size: 13px; line-height: 1.5; margin-bottom: 30px;
}

.config-section {
  margin-bottom: 35px;
  .section-title { display: flex; align-items: center; gap: 10px; margin-bottom: 15px; 
    h3 { margin: 0; font-size: 16px; color: #2c3e50; font-weight: bold; flex: 1;}
    .icon-box { width: 32px; height: 32px; border-radius: 10px; display: flex; justify-content: center; align-items: center; color: white; }
    .llm-icon { background: linear-gradient(135deg, #409eff, #3a8ee6); }
    .embed-icon { background: linear-gradient(135deg, #67c23a, #5daf34); }
    .vision-icon { background: linear-gradient(135deg, #e6a23c, #cf9236); }
  }
}

.glass-card {
  background: rgba(255, 255, 255, 0.6); border: 1px solid rgba(255,255,255,0.8); border-radius: 16px; padding: 20px; box-shadow: 0 4px 15px rgba(0,0,0,0.02);
  &.disabled-card { opacity: 0.6; pointer-events: none; filter: grayscale(100%); }
}

.form-group {
  margin-bottom: 20px; &:last-child { margin-bottom: 0; }
  label { display: block; font-size: 13px; font-weight: bold; color: #555; margin-bottom: 10px; }
}

/* 移动端适配的模型选择网格 */
.model-grid {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px;
  @media (max-width: 600px) { grid-template-columns: 1fr; } /* 手机端单列排布 */
  
  .model-card {
    background: #fff; border: 2px solid #eee; border-radius: 12px; padding: 15px 10px; text-align: center; cursor: pointer; transition: 0.3s;
    display: flex; flex-direction: column; align-items: center; gap: 8px;
    &:hover { border-color: #c6e2ff; transform: translateY(-2px); }
    &.active { border-color: #409eff; background: #ecf5ff; box-shadow: 0 4px 12px rgba(64,158,255,0.15); .m-name { color: #409eff; font-weight: bold; } }
    .m-logo { width: 36px; height: 36px; object-fit: contain; }
    .m-name { font-size: 13px; color: #666; transition: 0.3s; }
  }
}

:deep(.modern-input .el-input__wrapper) { background: #fff; border-radius: 10px; box-shadow: 0 2px 6px rgba(0,0,0,0.02); padding: 6px 15px; }
:deep(.modern-select .el-input__wrapper) { background: #fff; border-radius: 10px; box-shadow: 0 2px 6px rgba(0,0,0,0.02); }

.bottom-spacer { height: 40px; }
.custom-scrollbar::-webkit-scrollbar { width: 5px; } .custom-scrollbar::-webkit-scrollbar-thumb { background: #ddd; border-radius: 10px; }
</style>