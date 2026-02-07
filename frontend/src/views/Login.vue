<template>
  <div class="login-page">
    <div class="bg-glow"></div>
    <div class="login-card">
      <div class="card-header">
        <img src="https://api.dicebear.com/7.x/bottts/svg?seed=Traffic" alt="logo" class="logo" />
        <h1>ITQA 智能交通</h1>
        <p>{{ isLogin ? '欢迎回来，请登录您的账号' : '创建一个新账号以开始提问' }}</p>
      </div>

      <el-form :model="form" label-position="top" class="auth-form">
        <el-form-item label="用户名">
          <el-input v-model="form.username" placeholder="请输入用户名" prefix-icon="User" />
        </el-form-item>
        
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" placeholder="请输入密码" prefix-icon="Lock" show-password @keyup.enter="handleSubmit" />
        </el-form-item>

        <!-- 注册特有的验证码 -->
        <el-form-item label="验证码" v-if="!isLogin">
          <div class="captcha-row">
            <el-input v-model="form.captcha" placeholder="输入验证码" prefix-icon="CircleCheck" />
            <div class="captcha-img" @click="refreshCaptcha" title="点击刷新">
              {{ serverCaptcha || '获取中' }}
            </div>
          </div>
        </el-form-item>

        <div class="form-actions">
          <el-button type="primary" class="submit-btn" :loading="loading" @click="handleSubmit">
            {{ isLogin ? '立即登录' : '确认注册' }}
          </el-button>
          
          <div class="switch-mode">
            <span>{{ isLogin ? '没有账号?' : '已有账号?' }}</span>
            <el-button link type="primary" @click="toggleMode">
              {{ isLogin ? '去注册' : '去登录' }}
            </el-button>
          </div>
        </div>
      </el-form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { User, Lock, CircleCheck } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import request from '../api/request';

const router = useRouter();
const isLogin = ref(true);
const loading = ref(false);
const serverCaptcha = ref('');

const form = ref({
  username: '',
  password: '',
  captcha: ''
});

const refreshCaptcha = async () => {
  try {
    // 假设后端接口返回 { captcha_code: "A1B2" }
    const res = await request.get('/v1/auth/captcha', { params: { session_id: 'reg' } });
    serverCaptcha.value = res.data.captcha_code;
  } catch (e) {
    serverCaptcha.value = "ERROR";
  }
};

const toggleMode = () => {
  isLogin.value = !isLogin.value;
  if (!isLogin.value) refreshCaptcha();
};

const handleSubmit = async () => {
  if (!form.value.username || !form.value.password) {
    ElMessage.warning('请填写完整信息');
    return;
  }
  
  if (!isLogin.value && form.value.captcha.toLowerCase() !== serverCaptcha.value.toLowerCase()) {
    ElMessage.error('验证码错误');
    return;
  }

  loading.value = true;
  const endpoint = isLogin.value ? '/v1/auth/login' : '/v1/auth/register';

  try {
    const res = await request.post(endpoint, {
      username: form.value.username,
      password: form.value.password
    });
    
    if (isLogin.value) {
      localStorage.setItem('access_token', res.data.access_token);
      ElMessage.success('登录成功！');
      router.push('/');
    } else {
      ElMessage.success('注册成功，请登录');
      isLogin.value = true;
    }
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '操作失败');
  } finally {
    loading.value = false;
  }
};

onMounted(() => { if (!isLogin.value) refreshCaptcha(); });
</script>

<style scoped lang="scss">
.login-page {
  height: 100vh; width: 100vw; display: flex; justify-content: center; align-items: center;
  background: #eef2f7; position: relative; overflow: hidden;
}
.bg-glow {
  position: absolute; width: 600px; height: 600px; background: rgba(64, 158, 255, 0.15);
  filter: blur(100px); border-radius: 50%; top: -10%; right: -10%;
}
.login-card {
  width: 420px; padding: 40px; background: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(20px); border-radius: 28px; border: 1px solid rgba(255, 255, 255, 0.5);
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.05); z-index: 1;
  .card-header {
    text-align: center; margin-bottom: 30px;
    .logo { width: 64px; height: 64px; margin-bottom: 12px; }
    h1 { font-size: 26px; color: #303133; margin: 0; }
    p { font-size: 14px; color: #909399; margin-top: 8px; }
  }
}
.captcha-row {
  display: flex; gap: 12px;
  .captcha-img {
    flex-shrink: 0; width: 100px; height: 40px; background: #303133; color: #fff;
    border-radius: 8px; display: flex; justify-content: center; align-items: center;
    font-family: monospace; font-weight: bold; cursor: pointer; letter-spacing: 2px;
  }
}
.submit-btn { width: 100%; height: 46px; border-radius: 12px; margin-top: 10px; font-size: 16px; }
.switch-mode { margin-top: 20px; text-align: center; font-size: 14px; color: #606266; }
:deep(.el-input__wrapper) { border-radius: 10px; }
</style>