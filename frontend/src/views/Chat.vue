<template>
  <div class="glass-provider">
    <!-- 背景光效 -->
    <div class="bg-glow-1"></div>
    <div class="bg-glow-2"></div>

    <div class="app-container">
      <!-- 1. 侧边栏：与主界面无缝衔接 -->
      <aside class="sidebar" :class="{ 'mobile-hidden': !mobileMenuVisible }">
        <div class="sidebar-header">
          <el-button class="new-chat-btn" type="primary" @click="createNewChat" :icon="Plus">
            开启新对话
          </el-button>
        </div>
        
        <div class="session-manager custom-scrollbar">
          <div class="list-label">最近对话记录</div>
          <transition-group name="list">
            <div 
              v-for="s in sessions" 
              :key="s.id" 
              :class="['session-card', currentSessionId === s.id ? 'active' : '']" 
              @click="switchSession(s.id)"
            >
              <el-icon class="msg-icon"><ChatLineRound /></el-icon>
              <div class="session-info">
                <span class="title">{{ s.title }}</span>
                <span class="time">{{ formatTime(s.updated_at) }}</span>
              </div>
              <el-icon class="delete-btn" @click.stop="deleteSession(s.id)"><Delete /></el-icon>
            </div>
          </transition-group>
        </div>

        <div class="sidebar-footer" @click="$router.push('/profile')">
          <div class="user-profile-card">
            <div class="avatar-wrapper">
              <el-avatar :size="38" :src="fullAvatarUrl" />
              <span class="online-badge"></span>
            </div>
            <div class="user-meta">
              <span class="username">{{ currentUser.username || 'Asmile' }}</span>
              <div class="status-wrapper">
                <span class="pulse-dot"></span>
                <span class="status-text">在线 · 交通专家</span>
              </div>
            </div>
            <el-icon class="settings-icon"><Setting /></el-icon>
          </div>
        </div>
      </aside>

      <!-- 2. 移动端抽屉 -->
      <el-drawer v-model="mobileMenuVisible" direction="ltr" size="280px" :with-header="false">
        <div class="mobile-sidebar-content">
          <div class="sidebar-header">
            <el-button class="new-chat-btn" type="primary" @click="createNewChatAndClose" :icon="Plus">开启新对话</el-button>
          </div>
          <div class="session-manager custom-scrollbar">
            <div class="list-label">最近对话记录</div>
            <div v-for="s in sessions" :key="s.id" :class="['session-card', currentSessionId === s.id ? 'active' : '']" @click="switchSessionAndClose(s.id)">
              <el-icon class="msg-icon"><ChatLineRound /></el-icon>
              <div class="session-info">
                <span class="title">{{ s.title }}</span>
                <span class="time">{{ formatTime(s.updated_at) }}</span>
              </div>
              <el-icon class="delete-btn" @click.stop="deleteSession(s.id)"><Delete /></el-icon>
            </div>
          </div>
          <div class="sidebar-footer" @click="$router.push('/profile')">
            <div class="user-profile-card">
              <div class="avatar-wrapper">
                <el-avatar :size="38" :src="fullAvatarUrl" />
                <span class="online-badge"></span>
              </div>
              <div class="user-meta">
                <span class="username">{{ currentUser.username || 'Asmile' }}</span>
                <div class="status-wrapper">
                  <span class="pulse-dot"></span>
                  <span class="status-text">在线</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </el-drawer>

      <!-- 3. 主对话区 -->
      <main class="chat-main">
        <header class="main-header">
          <div class="header-left">
            <el-button class="mobile-menu-btn" :icon="Menu" circle @click="mobileMenuVisible = true" />
            <div class="session-display">
              <span class="label">当前会话</span>
              <h3 class="title">{{ currentSessionTitle }}</h3>
            </div>
          </div>
          <div class="header-right">
             <el-button circle @click="$router.push('/')" title="返回首页">
                <el-icon><HomeFilled /></el-icon>
             </el-button>
            <el-button circle :icon="Refresh" @click="fetchHistory(currentSessionId)" />
          </div>
        </header>

        <div class="message-wall custom-scrollbar" ref="messageBox">
          <!-- 欢迎页 -->
          <div v-if="chatHistory.length === 0" class="welcome-hero">
            <div class="hero-content">
              <div class="icon-circle">🚗</div>
              <h1>您好，我是交通法助手</h1>
              <div class="suggestion-grid">
                <div class="suggest-card" @click="quickStart('饮酒驾驶如何处罚？')">🍺 酒驾处罚标准</div>
                <div class="suggest-card" @click="quickStart('交通事故处理流程')">🚑 事故处理流程</div>
                <div class="suggest-card" @click="quickStart('从南京南站到夫子庙怎么走')">🗺️ 路径规划</div>
              </div>
            </div>
          </div>

          <!-- 对话消息 -->
          <div v-for="(msg, index) in chatHistory" :key="index" :class="['msg-row', msg.role === 'user' ? 'is-user' : 'is-ai']">
            <div class="msg-bubble">
              
              <!-- 🌟 核心优化：DeepSeek 风格的思维折叠区 -->
              <div v-if="msg.role === 'ai' && msg.thinking && msg.thinking.length > 0" class="thinking-box">
                <details :open="loading && index === chatHistory.length - 1">
                  <summary class="think-summary">
                    <div class="think-header">
                      <el-icon class="is-loading" v-if="loading && index === chatHistory.length - 1"><Loading /></el-icon>
                      <el-icon v-else><Check /></el-icon>
                      <span>{{ loading && index === chatHistory.length - 1 ? '深度思考中...' : '已完成思考' }}</span>
                    </div>
                  </summary>
                  <ul class="thinking-list">
                    <li v-for="(step, i) in msg.thinking" :key="i">
                      <span class="step-dot"></span> {{ step }}
                    </li>
                  </ul>
                </details>
              </div>

              <!-- 正式回答区 -->
              <div class="markdown-body" v-html="renderMarkdown(msg.content)"></div>
              
              <div v-if="msg.role === 'ai'" class="ai-footer">
                <div class="actions-left">
                   <el-button type="primary" link :icon="VideoPlay" @click="speak(msg.content)">朗读</el-button>
                   <!-- 点赞/点踩 -->
                    <el-button 
                        link 
                        :type="msg.is_helpful === true ? 'success' : 'default'"
                        :icon="CaretTop" 
                        @click="handleFeedback(msg, true)"
                    >
                        有用
                    </el-button>
                    
                    <el-button 
                        link 
                        :type="msg.is_helpful === false ? 'danger' : 'default'"
                        :icon="CaretBottom" 
                        @click="handleFeedback(msg, false)"
                    >
                        没用
                    </el-button>
                </div>
                
                <div v-if="msg.sources?.length" class="source-tag">
                  <el-popover 
                    placement="top-start" 
                    title="法律依据原文" 
                    :width="450" 
                    trigger="click"
                    popper-class="legal-source-popper"
                  >
                    <template #reference>
                      <span class="src-link"><el-icon><Document /></el-icon> 引用依据</span>
                    </template>
                    <div class="source-scroll-container">
                      <div v-for="(s, i) in msg.sources" :key="i" class="src-item-card">
                        <div class="src-label">{{ s.title || s.label || `依据 ${i + 1}` }}</div>
                        <div class="src-text markdown-body" v-html="renderMarkdown(s.content)"></div>
                      </div>
                    </div>
                  </el-popover>
                </div>
              </div>
            </div>
          </div>
          
          <div v-if="loading && ((chatHistory[chatHistory.length - 1]?.thinking?.length ?? 0) === 0)" class="msg-row is-ai">
            <div class="msg-bubble loading-bubble">
              <div class="typing-dots"><span></span><span></span><span></span></div>
            </div>
          </div>
        </div>

        <!-- 4. 输入框与模式选择 -->
        <footer class="input-area-container">
          <!-- 🌟 核心优化：双引擎切换胶囊 -->
          <div class="mode-selector-wrapper">
            <el-radio-group v-model="chatMode" size="small" class="custom-mode-radio">
              <el-radio-button label="fast"><el-icon><Lightning /></el-icon> 极速模式</el-radio-button>
              <el-radio-button label="expert"><el-icon><Cpu /></el-icon> 专家模式</el-radio-button>
            </el-radio-group>
          </div>

          <div class="input-pill">
            <el-button 
              :type="isRecording ? 'danger' : 'default'" 
              circle 
              :icon="isRecording ? Mic : Microphone" 
              @click="toggleRecognition"
              class="voice-btn"
            />
            <el-input 
              v-model="inputQuery" 
              placeholder="输入交通法相关问题..." 
              type="textarea" 
              :autosize="{ minRows: 1, maxRows: 5 }" 
              resize="none" 
              @keyup.enter.prevent="handleSend" 
            />
            <el-button type="primary" circle :icon="Top" @click="handleSend" :loading="loading" class="send-btn" />
          </div>
          <div class="footer-copy">AI 生成内容仅供参考 · 数据实时路网联网</div>
        </footer>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, nextTick, computed, reactive, onUnmounted } from 'vue';
// 新增引入了 Check, Lightning, Cpu 等图标
import { Plus, ChatLineRound, Delete, Refresh, Document, Top, Menu, Microphone, Mic, VideoPlay, Setting, CaretTop, CaretBottom, HomeFilled, Loading, Check, Lightning, Cpu } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import request from '../api/request';
import MarkdownIt from 'markdown-it';
import { API_BASE_URL, STATIC_BASE_URL } from '../api/config';
import { Capacitor } from '@capacitor/core';
import { TextToSpeech } from '@capacitor-community/text-to-speech';

// --- 类型定义 ---
interface SessionItem { id: string; title: string; updated_at?: string; }
interface SourceItem {
  type?: string;
  title?: string;
  label?: string;
  content: string;
}
interface Message { 
  id?: number;
  role: 'user' | 'ai'; 
  content: string; 
  sources?: SourceItem[]; 
  thinking?: string[]; // 🌟 新增思考过程数组
  is_helpful?: boolean | null;
}

const sessions = ref<SessionItem[]>([]);
const currentSessionId = ref('');
const chatHistory = ref<Message[]>([]);
const inputQuery = ref('');
const loading = ref(false);
const mobileMenuVisible = ref(false);
const messageBox = ref<HTMLElement | null>(null);
const currentUser = ref({ username: '', avatar: '' });

// 🌟 新增模式选择器状态（默认专家模式）
const chatMode = ref('expert');

const md = new MarkdownIt({ html: true, linkify: true });

const fullAvatarUrl = computed(() => {
  if (!currentUser.value.avatar) return 'https://api.dicebear.com/7.x/avataaars/svg?seed=Felix';
  return `${STATIC_BASE_URL}${currentUser.value.avatar}?t=${Date.now()}`;
});
const currentSessionTitle = computed(() => sessions.value.find(i => i.id === currentSessionId.value)?.title || '新对话');

const safeParseJsonArray = (raw: unknown): any[] => {
  if (Array.isArray(raw)) return raw;
  if (typeof raw !== 'string' || !raw.trim()) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

const normalizeSources = (raw: unknown): SourceItem[] => {
  if (!Array.isArray(raw)) return [];
  const result: SourceItem[] = [];
  raw.forEach((item: unknown, index: number) => {
    if (typeof item === 'string') {
      result.push({
        type: 'law',
        title: `依据 ${index + 1}`,
        label: `检索片段 ${index + 1}`,
        content: item,
      });
      return;
    }
    if (item && typeof item === 'object') {
      const content = typeof (item as any).content === 'string' ? (item as any).content : '';
      if (!content) return;
      result.push({
        type: typeof (item as any).type === 'string' ? (item as any).type : 'law',
        title: typeof (item as any).title === 'string' ? (item as any).title : `依据 ${index + 1}`,
        label: typeof (item as any).label === 'string' ? (item as any).label : `检索片段 ${index + 1}`,
        content,
      });
    }
  });
  return result;
};

// --- ASR 逻辑 ---
const isRecording = ref(false);
let webRecognition: any = null;
const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
let recognition: any = null;
if (SpeechRecognition) {
  recognition = new SpeechRecognition();
  recognition.lang = 'zh-CN';
  recognition.onresult = (event: any) => { inputQuery.value = event.results[0][0].transcript; };
  recognition.onend = () => { isRecording.value = false; };
}

const toggleRecognition = async () => {
  if (isRecording.value) {
    if (Capacitor.isNativePlatform()) await SpeechRecognition.stop();
    else if (webRecognition) webRecognition.stop();
    isRecording.value = false;
  } else {
    inputQuery.value = ''; 
    isRecording.value = true;
    if (Capacitor.isNativePlatform()) {
      try {
        const hasPermission = await SpeechRecognition.checkPermissions();
        if (hasPermission.speechRecognition !== 'granted') await SpeechRecognition.requestPermissions();
        await SpeechRecognition.start({ language: "zh-CN", maxResults: 1, prompt: "请说话...", partialResults: true, popup: false });
        SpeechRecognition.addListener('partialResults', (data: any) => {
          if (data.matches && data.matches.length > 0) inputQuery.value = data.matches[0];
        });
      } catch (e) {
        ElMessage.error('启动录音失败: ' + JSON.stringify(e));
        isRecording.value = false;
      }
    } else {
      if (webRecognition) webRecognition.start();
      else { ElMessage.warning('当前浏览器不支持语音识别'); isRecording.value = false; }
    }
  }
};

onMounted(async () => {
  try {
    const [userRes, sessRes] = await Promise.all([request.get('/v1/chat/me'), request.get('/v1/chat/sessions')]);
    currentUser.value = userRes.data;
    sessions.value = sessRes.data;
    
    const firstSession = sessions.value[0];
    if (firstSession) await switchSession(firstSession.id); 
    else createNewChat();

    if (Capacitor.isNativePlatform()) {
      try { await SpeechRecognition.requestPermissions(); } catch (e) {}
    } else {
      const WebSpeech = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
      if (WebSpeech) {
        webRecognition = new WebSpeech();
        webRecognition.lang = 'zh-CN';
        webRecognition.interimResults = true;
        webRecognition.onresult = (event: any) => { inputQuery.value = event.results[0][0].transcript; };
        webRecognition.onend = () => { isRecording.value = false; };
      }
    }
  } catch (e) {}
});

onUnmounted(() => {
  if (Capacitor.isNativePlatform()) SpeechRecognition.removeAllListeners();
});

const fetchSessions = async () => { sessions.value = (await request.get('/v1/chat/sessions')).data; };
const fetchHistory = async (id: string) => {
  const res = await request.get(`/v1/chat/history/${id}`);
  chatHistory.value = res.data.map((m: any) => ({
    id: m.id,
    is_helpful: m.is_helpful,
    role: m.role as 'user'|'ai', 
    content: m.content, 
    sources: normalizeSources(safeParseJsonArray(m.sources)),
    thinking:[] // 历史记录不显示思考过程
  }));
  await scrollToBottom();
};
const switchSession = async (id: string) => { currentSessionId.value = id; await fetchHistory(id); };
const switchSessionAndClose = async (id: string) => { await switchSession(id); mobileMenuVisible.value = false; };
const createNewChat = () => { currentSessionId.value = `session_${Math.random().toString(36).substr(2, 9)}`; chatHistory.value =[]; };
const createNewChatAndClose = () => { createNewChat(); mobileMenuVisible.value = false; };

// --- 核心流式逻辑 ---
const handleSend = async () => {
  if (!inputQuery.value.trim() || loading.value) return;

  const question = inputQuery.value.trim();
  const sid = currentSessionId.value;

  chatHistory.value.push({ role: 'user', content: question });
  inputQuery.value = '';
  loading.value = true;

  // 初始化带有 thinking 数组的 AI 响应对象
  const aiMsg = reactive<Message>({ role: 'ai', content: '', sources: [], thinking:[] });
  chatHistory.value.push(aiMsg);
  await scrollToBottom();

  try {
    // 🌟 核心优化：动态切换后端接口
    const endpoint = chatMode.value === 'expert' ? '/v1/agentic/expert_stream' : '/v1/chat/ask_stream';
    const streamUrl = `${API_BASE_URL}${endpoint}`;
    
    const response = await fetch(streamUrl, {
      method: 'POST',
      headers: { 
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${localStorage.getItem('access_token')}` 
      },
      body: JSON.stringify({ question, session_id: sid })
    });

    if (!response.ok) {
      throw new Error(`请求失败: ${response.status}`);
    }
    
    if (!response.body) throw new Error("No Body");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let lines = buffer.split("\n\n");
      buffer = lines.pop() || "";
      
      for (const line of lines) {
        if (!line.startsWith("data:")) continue;
        const jsonStr = line.replace(/^data:\s*/, "");
        try {
            const payload = JSON.parse(jsonStr);
            
            if (payload.type === 'sources') {
              aiMsg.sources = normalizeSources(payload.data);
            } else if (payload.type === 'content') {
                const text = payload.data;
                // 🌟 核心黑科技：剥离提取中间过程（思考状态）
                // 匹配如: "\n\n> ⚙️ xxxx\n\n" 或 "🔄 **xxxx**\n\n" 这种后端发来的系统通知
                if (text.match(/[\s\n]*(> [⚙️🔄📚🗺️🛡️]|🔄 \*\*正在)/)) {
                    let cleanText = text.replace(/[\n>*\#]/g, '').trim();
                    if (cleanText) {
                        if (!aiMsg.thinking) aiMsg.thinking =[];
                        if (!aiMsg.thinking.includes(cleanText)) {
                            aiMsg.thinking.push(cleanText);
                            if (messageBox.value) messageBox.value.scrollTop = messageBox.value.scrollHeight;
                        }
                    }
                    continue; // 拦截成功，不再丢给正文 content
                }
                
                // 正文或地图 Widget
                aiMsg.content += text;
                if (messageBox.value) messageBox.value.scrollTop = messageBox.value.scrollHeight;

            } else if (payload.type === 'done') {
                if (payload.message_id) aiMsg.id = payload.message_id;
            }
        } catch(e) {}
      }
      await nextTick();
    }
    await fetchSessions();
  } catch (e: any) { 
    aiMsg.content += `\n⚠️ ${e?.message || '连接中断'}`; 
  } finally { 
    loading.value = false; 
    await scrollToBottom(); 
  }
};

const handleFeedback = async (msg: Message, helpful: boolean) => {
  if (!msg.id) return ElMessage.warning('请稍等...');
  try {
    await request.post('/v1/chat/feedback', { message_id: msg.id, is_helpful: helpful });
    msg.is_helpful = helpful;
    ElMessage.success('感谢反馈');
  } catch (e) { ElMessage.error('失败'); }
};

const speak = async (text: string) => {
  const cleanText = text.replace(/[#*`>]/g, '').replace(/\[依据\d+\]/g, '');
  if (Capacitor.isNativePlatform()) {
    try {
      await TextToSpeech.stop(); 
      await TextToSpeech.speak({ text: cleanText, lang: 'zh-CN', rate: 1.0, pitch: 1.0, volume: 1.0, category: 'ambient' });
    } catch (e) { ElMessage.error('语音朗读失败'); }
  } else {
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(cleanText);
    utterance.lang = 'zh-CN';
    window.speechSynthesis.speak(utterance);
  }
};

const formatTime = (t?: string) => t ? new Date(t).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'}) : '';
const renderMarkdown = (t: string) => md.render(t);
const scrollToBottom = async () => { await nextTick(); if (messageBox.value) messageBox.value.scrollTop = messageBox.value.scrollHeight; };
const quickStart = (t: string) => { inputQuery.value = t; handleSend(); };
const deleteSession = async (id: string) => {
  try {
    await ElMessageBox.confirm('确定删除吗？');
    await request.delete(`/v1/chat/session/${id}`);
    await fetchSessions();
    if (currentSessionId.value === id) createNewChat();
  } catch (e) {}
};
</script>

<style scoped lang="scss">
/* --- 布局容器 --- */
.glass-provider { height: 100vh; width: 100vw; background: #f0f2f5; display: flex; justify-content: center; align-items: center; position: relative; overflow: hidden; }
.bg-glow-1 { position: absolute; top: -10%; left: -10%; width: 40%; height: 40%; background: radial-gradient(circle, rgba(64, 158, 255, 0.1) 0%, transparent 70%); filter: blur(60px); }
.bg-glow-2 { position: absolute; bottom: -10%; right: -10%; width: 40%; height: 40%; background: radial-gradient(circle, rgba(103, 194, 58, 0.08) 0%, transparent 70%); filter: blur(60px); }

.app-container {
  width: 98%; height: 96%; background: rgba(255, 255, 255, 0.7); backdrop-filter: blur(25px);
  border-radius: 20px; display: flex; box-shadow: 0 10px 40px rgba(0,0,0,0.05); overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.4);
  @media (max-width: 768px) { width: 100%; height: 100%; border-radius: 0; }
}

/* --- 侧边栏 --- */
.sidebar, .mobile-sidebar-content {
  width: 280px; background: rgba(255, 255, 255, 0.45); border-right: 1px solid rgba(0,0,0,0.06);
  display: flex; flex-direction: column; padding: 20px 12px; height: 100%; box-sizing: border-box;
}

.sidebar { @media (max-width: 768px) { display: none; } }
.mobile-sidebar-content { width: 100%; background: #fff; border-right: none; }

.new-chat-btn {
  width: 100%; height: 44px; border-radius: 10px; font-weight: 600;
  background: #409eff; color: white; border: none; margin-bottom: 20px;
  display: flex; align-items: center; justify-content: center; gap: 8px;
}

.session-manager {
  flex: 1; overflow-y: auto;
  .list-label { font-size: 11px; color: #999; margin-bottom: 10px; padding-left: 8px; }
}

.session-card {
  padding: 10px 12px; margin-bottom: 6px; border-radius: 10px; display: flex; align-items: center; gap: 10px;
  cursor: pointer; transition: 0.2s; position: relative;
  &:hover { background: rgba(0,0,0,0.04); .delete-btn { opacity: 1; } }
  &.active { background: #fff; box-shadow: 0 4px 10px rgba(0,0,0,0.04); color: #409eff; }
  .session-info { flex: 1; overflow: hidden; .title { font-size: 13.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block; } .time { font-size: 10px; opacity: 0.5; } }
  .delete-btn { opacity: 0; font-size: 14px; color: #f56c6c; transition: 0.2s; &:hover { color: red; } }
}

.sidebar-footer {
  margin-top: auto; padding-top: 15px; border-top: 1px solid rgba(0,0,0,0.05);
  .user-profile-card {
    background: #fff; padding: 10px; border-radius: 12px; display: flex; align-items: center; gap: 10px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.03); cursor: pointer;
    .avatar-wrapper { position: relative; .online-badge { position: absolute; bottom: 0; right: 0; width: 9px; height: 9px; background: #67C23A; border: 2px solid #fff; border-radius: 50%; } }
    .user-meta { flex: 1; .username { font-size: 13px; font-weight: bold; color: #333; display: block; } .status-wrapper { display: flex; align-items: center; gap: 4px; .status-text { font-size: 10px; color: #999; } .pulse-dot { width: 5px; height: 5px; background: #67C23A; border-radius: 50%; animation: pulse 2s infinite; } } }
    .settings-icon { opacity: 0.5; }
  }
}

/* --- 主对话区 --- */
.chat-main { flex: 1; display: flex; flex-direction: column; background: #fff; position: relative; }
.main-header {
  padding: 12px 20px; border-bottom: 1px solid rgba(0,0,0,0.04);
  display: flex; justify-content: space-between; align-items: center;
  .header-left { display: flex; align-items: center; gap: 15px; }
  .mobile-menu-btn { display: none; @media (max-width: 768px) { display: inline-flex; } }
  .session-display { .label { font-size: 9px; color: #999; display: block; } .title { font-size: 16px; margin: 0; color: #333; font-weight: bold; } }
}

.message-wall { flex: 1; padding: 20px 15% 150px; overflow-y: auto; @media (max-width: 768px) { padding: 15px 10px 130px; } }

.msg-row {
  display: flex; margin-bottom: 20px;
  &.is-user { justify-content: flex-end; .msg-bubble { background: #409eff; color: #fff; border-radius: 16px 16px 2px 16px; } }
  &.is-ai { justify-content: flex-start; .msg-bubble { background: #f4f6f8; color: #333; border-radius: 16px 16px 16px 2px; } }
  .msg-bubble { max-width: 88%; padding: 12px 16px; font-size: 14.5px; line-height: 1.6; }
}

/* 🌟 DeepSeek 风格思考折叠区 */
.thinking-box {
  margin-bottom: 12px;
  details {
    background: #ffffff; border: 1px solid #e4e7ed; border-radius: 8px; padding: 8px 12px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.02); transition: all 0.3s ease;
  }
  details[open] { background: #fafafa; padding-bottom: 12px; }
  
  .think-summary {
    list-style: none; cursor: pointer; outline: none;
    &::-webkit-details-marker { display: none; }
    
    .think-header {
      display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: bold; color: #909399;
      transition: color 0.3s;
      .el-icon { font-size: 16px; }
    }
    &:hover .think-header { color: #409eff; }
  }

  .thinking-list {
    list-style: none; padding: 10px 0 0 0; margin: 0; border-top: 1px dashed #ebeef5; margin-top: 8px;
    li {
      font-size: 12.5px; color: #606266; margin-bottom: 6px; display: flex; align-items: flex-start; gap: 8px;
      .step-dot {
        margin-top: 6px; width: 4px; height: 4px; border-radius: 50%; background: #c0c4cc; flex-shrink: 0;
      }
    }
  }
}

.ai-footer {
  margin-top: 10px; padding-top: 8px; border-top: 1px solid rgba(0,0,0,0.05);
  display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;
  .actions-left { display: flex; gap: 10px; }
  .src-link { font-size: 11px; color: #409eff; cursor: pointer; display: flex; align-items: center; gap: 3px; &:hover { text-decoration: underline; } }
}

/* --- 输入框区域 --- */
.input-area-container {
  position: absolute; bottom: 20px; left: 15%; right: 15%; display: flex; flex-direction: column; align-items: center;
  @media (max-width: 768px) { left: 10px; right: 10px; bottom: 10px; }
  
  /* 🌟 模式选择器 */
  .mode-selector-wrapper {
    margin-bottom: 10px; align-self: flex-start;
  }
  /* 调整 Radio 胶囊样式 */
  :deep(.custom-mode-radio) {
    .el-radio-button__inner { 
      border-radius: 20px !important; border: none; background: #fff; box-shadow: 0 2px 8px rgba(0,0,0,0.05);
      padding: 6px 16px; font-size: 12px; margin-right: 8px; color: #606266;
      display: flex; align-items: center; gap: 4px;
    }
    .el-radio-button.is-active .el-radio-button__inner {
      background: #409eff; color: #fff; box-shadow: 0 4px 12px rgba(64,158,255,0.3);
    }
  }

  .input-pill {
    width: 100%; background: #fff; border-radius: 24px; padding: 6px 12px; display: flex; align-items: center; gap: 8px;
    box-shadow: 0 6px 24px rgba(0,0,0,0.08); border: 1px solid #ebeef5;
    :deep(.el-textarea__inner) { border: none; box-shadow: none; padding: 8px; font-size: 14px; background: transparent; }
    .voice-btn { transition: 0.3s; &:hover { background: #f0f0f0; } }
  }
  .footer-copy { text-align: center; font-size: 10px; color: #ccc; margin-top: 8px; }
}

/* --- 欢迎页 --- */
.welcome-hero {
  height: 80%; display: flex; justify-content: center; align-items: center; text-align: center;
  .icon-circle { font-size: 50px; margin-bottom: 15px; }
  h1 { font-size: 24px; color: #333; margin-bottom: 20px; }
  .suggestion-grid { display: flex; gap: 12px; flex-wrap: wrap; justify-content: center; .suggest-card { background: #f9f9f9; padding: 12px 20px; border-radius: 12px; cursor: pointer; font-size: 13px; border: 1px solid #eee; &:hover { border-color: #409eff; color: #409eff; transform: translateY(-2px); } } }
}

/* --- 滚动条修复 --- */
.source-scroll-container {
  max-height: 450px; overflow-y: auto !important; padding-right: 8px;
  &::-webkit-scrollbar { width: 5px; display: block !important; }
  &::-webkit-scrollbar-thumb { background: #ddd; border-radius: 10px; }
  .src-item-card {
    background: #f8f9fa; padding: 15px; border-radius: 10px; margin-bottom: 12px; border: 1px solid #e4e7ed; box-shadow: 0 2px 8px rgba(0,0,0,0.02);
    .src-label { color: #409eff; font-weight: 800; font-size: 13px; margin-bottom: 8px; border-bottom: 1px solid #eee; padding-bottom: 5px; }
    .src-text { font-size: 12.5px; color: #555; line-height: 1.6;
      :deep(p) { margin-bottom: 6px; }
      :deep(h1), :deep(h2), :deep(h3), :deep(h4) { font-size: 13.5px; font-weight: bold; margin: 8px 0 4px; color: #333; }
      :deep(ul), :deep(ol) { padding-left: 18px; margin-bottom: 6px; }
      :deep(li) { margin-bottom: 3px; }
      :deep(br) { content: ""; display: block; margin-top: 2px; }
      :deep(table) { width: 100%; border-collapse: collapse; margin-bottom: 6px; th, td { border: 1px solid #ddd; padding: 4px 8px; text-align: left; } th { background-color: #f0f2f5; } }
    }
  }
}

.custom-scrollbar::-webkit-scrollbar { width: 4px; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #eee; border-radius: 4px; }
.typing-dots { span { width: 6px; height: 6px; background: #909399; border-radius: 50%; display: inline-block; margin: 0 2px; animation: blink 1.4s infinite; } span:nth-child(2) { animation-delay: 0.2s; } span:nth-child(3) { animation-delay: 0.4s; } }
@keyframes pulse { 0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(103, 194, 58, 0.7); } 70% { transform: scale(1); box-shadow: 0 0 0 6px rgba(103, 194, 58, 0); } 100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(103, 194, 58, 0); } }
@keyframes blink { 0% { opacity: 0.2; } 20% { opacity: 1; } 100% { opacity: 0.2; } }
:deep(.el-drawer__body) { padding: 0; }
</style>

<style lang="scss">
.legal-source-popper { padding: 15px !important; border-radius: 15px !important; box-shadow: 0 10px 30px rgba(0,0,0,0.15) !important; }
.markdown-body {
  font-size: 14px;
  line-height: 1.6;
  color: #333;
  
  /* 🌟 核心修复：表格样式美化 */
  :deep(table) {
    width: 100%;
    border-collapse: collapse;
    margin: 12px 0;
    font-size: 13px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
    border-radius: 8px;
    overflow: hidden;
  }
  
  :deep(th) {
    background-color: #f5f7fa;
    color: #606266;
    font-weight: 600;
    padding: 10px 12px;
    border: 1px solid #ebeef5;
    text-align: left;
  }
  
  :deep(td) {
    padding: 8px 12px;
    border: 1px solid #ebeef5;
    color: #555;
  }
  
  :deep(tr:nth-child(even)) {
    background-color: #fafafa;
  }
  
  :deep(tr:hover) {
    background-color: #f0f7ff;
  }
  
  /* 防止表格被内容撑爆屏幕 */
  :deep(table) {
    display: block;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
}
</style>
