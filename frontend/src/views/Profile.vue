<template>
  <div class="profile-wrapper">
    <div class="bg-blob blob-1"></div>
    <div class="bg-blob blob-2"></div>

    <div class="profile-container">
      <el-row :gutter="24">
        <!-- 左侧：个人名片卡 -->
        <el-col :xs="24" :sm="9" :md="8">
          <div class="glass-card profile-side">
            <div class="avatar-container">
              <el-upload
                class="avatar-uploader"
                action="/api/v1/chat/upload_avatar"
                :headers="authHeader"
                :show-file-list="false"
                :on-success="handleAvatarSuccess"
              >
                <div class="avatar-ring">
                  <img v-if="user.avatar" :src="fullAvatarUrl" class="avatar-img" />
                  <el-avatar v-else :size="120" icon="UserFilled" />
                  <div class="upload-overlay">
                    <el-icon><Camera /></el-icon>
                    <span>更换头像</span>
                  </div>
                </div>
              </el-upload>
            </div>
            <div class="user-basic-info">
              <div class="name-edit-row">
                <h2 class="username">{{ user.username }}</h2>
                <el-button :icon="EditPen" link @click="showEditName" />
              </div>
              <el-tag :type="user.role === 'admin' ? 'danger' : 'success'" round effect="dark">
                {{ user.role === 'admin' ? '首席管理员' : '交通法专家' }}
              </el-tag>
            </div>
            <el-divider />
            <div class="user-stats">
              <div class="stat-item">
                <span class="value">{{ stats.join_days }}</span>
                <span class="label">加入天数</span>
              </div>
              <div class="stat-item">
                <span class="value">{{ stats.query_count }}</span>
                <span class="label">咨询次数</span>
              </div>
            </div>
            <div class="side-actions">
              <el-button type="danger" plain :icon="SwitchButton" @click="handleLogout" class="logout-btn">
                退出登录
              </el-button>
            </div>
          </div>
        </el-col>

        <!-- 右侧：详细设置与功能区 -->
        <el-col :xs="24" :sm="15" :md="16">
          <div class="main-content">
            <div class="glass-card header-card">
              <!-- el-page-header 默认自带图标，不需要额外传入 ArrowLeft -->
              <el-page-header @back="$router.push('/')" title="返回首页">
  <template #content><span class="header-title">账户设置中心</span></template>
</el-page-header>
            </div>

            <!-- 基本资料卡片 -->
            <div class="glass-card details-card">
              <div class="card-title"><el-icon><User /></el-icon> 基本资料</div>
              <div class="info-grid">
                <div class="info-item">
                  <span class="label">登录账号</span>
                  <span class="content">{{ user.username }}</span>
                </div>
                <div class="info-item">
                  <span class="label">注册日期</span>
                  <span class="content">{{ user.created_at || '2026-02-01' }}</span>
                </div>
                <div class="info-item">
                  <span class="label">账户状态</span>
                  <span class="content status-ok">● 正常运行中</span>
                </div>
              </div>
            </div>

            <!-- 管理员控制台 -->
            <div class="glass-card tool-card" v-if="user.role === 'admin'">
              <div class="card-title"><el-icon><Monitor /></el-icon> 管理员控制台</div>
              <p class="tool-desc">管理知识库、查看 AI 聚类分析报告及法律逻辑图谱。</p>
              <div class="tool-btns">
                <el-button type="warning" :icon="Tools" @click="$router.push('/admin')" round>管理后台</el-button>
                <el-button type="success" :icon="Share" @click="$router.push('/graph')" round>知识图谱</el-button>
              </div>
            </div>

            <!-- 账号安全 -->
            <div class="glass-card security-card">
              <div class="card-title"><el-icon><Lock /></el-icon> 账号安全</div>
              <div class="security-item">
                <div class="sec-left">
                  <span class="sec-label">登录密码</span>
                  <span class="sec-desc">建议定期更换复杂密码以保障安全</span>
                </div>
                <el-button type="primary" link @click="showPwdDialog = true">修改密码</el-button>
              </div>
            </div>
          </div>
        </el-col>
      </el-row>
    </div>

    <!-- 修改密码弹窗 -->
    <el-dialog v-model="showPwdDialog" title="安全中心 - 修改密码" width="400px" append-to-body round>
      <el-form :model="pwdForm" label-position="top">
        <el-form-item label="旧密码"><el-input v-model="pwdForm.old" type="password" show-password /></el-form-item>
        <el-form-item label="新密码"><el-input v-model="pwdForm.new" type="password" show-password /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showPwdDialog = false">取消</el-button>
        <el-button type="primary" @click="handleUpdatePwd">确认修改</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { useRouter } from 'vue-router';
// 移除了未使用的 ArrowLeft
import { 
  Camera, SwitchButton, Tools, Share, 
  User, Monitor, Lock, EditPen 
} from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import request from '../api/request';

const router = useRouter();
const user = ref({ username: '', avatar: '', role: '', created_at: '' });
const stats = ref({ join_days: 0, query_count: 0 });

const authHeader = { Authorization: `Bearer ${localStorage.getItem('access_token')}` };

// 头像路径处理
const fullAvatarUrl = computed(() => {
  if (!user.value.avatar) return 'https://api.dicebear.com/7.x/avataaars/svg?seed=Felix';
  return `${window.location.origin}${user.value.avatar}?t=${Date.now()}`;
});

// 初始化数据
const initData = async () => {
  try {
    const [uRes, sRes] = await Promise.all([
      request.get('/v1/chat/me'),
      request.get('/v1/chat/stats')
    ]);
    user.value = uRes.data;
    stats.value = sRes.data;
  } catch (e) { 
    console.error(e);
    ElMessage.error('个人资料加载失败'); 
  }
};

// 修改用户名
const showEditName = async () => {
  try {
    // 修复点：通过 as any 或者明确的接口定义来处理 MessageBox 的返回值
    const result = await ElMessageBox.prompt('请输入新的用户名', '修改资料', {
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValue: user.value.username
    }) as { value: string; action: string };

    if (result.value) {
      await request.put('/v1/chat/update_me', null, { params: { new_name: result.value } });
      user.value.username = result.value;
      ElMessage.success('用户名修改成功');
    }
  } catch (e) {
    // 用户取消输入
  }
};

// 修改密码逻辑
const showPwdDialog = ref(false);
const pwdForm = ref({ old: '', new: '' });

const handleUpdatePwd = async () => {
  if (!pwdForm.value.old || !pwdForm.value.new) {
    ElMessage.warning('请填写完整的旧密码和新密码');
    return;
  }
  try {
    await request.post('/v1/chat/change_password', null, {
      params: { old_pwd: pwdForm.value.old, new_pwd: pwdForm.value.new }
    });
    ElMessage.success('密码修改成功，请重新登录');
    handleLogout();
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '旧密码校验失败');
  }
};

const handleAvatarSuccess = (res: any) => {
  user.value.avatar = res.avatar_url;
  ElMessage.success('头像上传成功');
  // 强制刷新页面数据
  initData();
};

const handleLogout = () => {
  localStorage.clear();
  router.push('/login');
};

onMounted(initData);
</script>

<style scoped lang="scss">
.profile-wrapper {
  min-height: 100vh; width: 100vw; background: #f4f7f9;
  display: flex; justify-content: center; align-items: flex-start;
  position: relative; overflow-x: hidden; padding: 60px 20px;
}

/* 背景气泡动画 */
.bg-blob { position: absolute; border-radius: 50%; filter: blur(80px); z-index: 0; }
.blob-1 { width: 500px; height: 500px; background: rgba(64, 158, 255, 0.12); top: -100px; left: -100px; }
.blob-2 { width: 400px; height: 400px; background: rgba(103, 194, 58, 0.08); bottom: -100px; right: -100px; }

.profile-container { width: 100%; max-width: 1000px; z-index: 1; }

.glass-card {
  background: rgba(255, 255, 255, 0.85); backdrop-filter: blur(20px);
  border: 1px solid rgba(255, 255, 255, 0.6); border-radius: 24px;
  padding: 30px; margin-bottom: 20px; box-shadow: 0 15px 35px rgba(0, 0, 0, 0.05);
  transition: transform 0.3s ease;
  &:hover { transform: translateY(-5px); }
}

.profile-side {
  text-align: center;
  .avatar-ring {
    width: 130px; height: 130px; margin: 0 auto 20px; border-radius: 50%;
    padding: 4px; border: 3px solid #409eff; position: relative; overflow: hidden;
    .avatar-img { width: 100%; height: 100%; border-radius: 50%; object-fit: cover; }
    .upload-overlay {
      position: absolute; top: 0; left: 0; width: 100%; height: 100%;
      background: rgba(0,0,0,0.5); color: #fff; display: flex; flex-direction: column;
      justify-content: center; align-items: center; opacity: 0; transition: 0.3s;
    }
    &:hover .upload-overlay { opacity: 1; }
  }
  .name-edit-row {
    display: flex; align-items: center; justify-content: center; gap: 8px; margin-bottom: 5px;
    .username { font-size: 22px; margin: 0; color: #333; }
  }
  .user-stats {
    display: flex; justify-content: space-around; margin: 25px 0;
    .stat-item {
      display: flex; flex-direction: column;
      .value { font-size: 22px; font-weight: 800; color: #409eff; }
      .label { font-size: 11px; color: #999; }
    }
  }
  .logout-btn { width: 100%; border-radius: 12px; margin-top: 10px; }
}

.main-content {
  .header-card { padding: 15px 25px; }
  .card-title { font-size: 16px; font-weight: bold; margin-bottom: 20px; display: flex; align-items: center; gap: 10px; }
  .info-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 20px;
    @media (max-width: 500px) { grid-template-columns: 1fr; }
    .info-item { display: flex; flex-direction: column; .label { font-size: 12px; color: #999; } .content { font-weight: 600; color: #444; } .status-ok { color: #67C23A; } }
  }
}

.tool-card {
  background: linear-gradient(135deg, rgba(64,158,255,0.05) 0%, rgba(255,255,255,0.8) 100%);
  .tool-desc { font-size: 13px; color: #777; margin-bottom: 15px; }
  .tool-btns { display: flex; gap: 12px; flex-wrap: wrap; }
}

.security-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px; background: #f9fafc; border-radius: 12px;
  .sec-label { font-size: 14px; font-weight: 600; }
  .sec-desc { font-size: 11px; color: #aaa; display: block; }
}
</style>