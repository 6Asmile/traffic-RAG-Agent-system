<template>
  <div class="profile-page">
    <div class="profile-card">
      <div class="p-header">
        <el-button :icon="ArrowLeft" circle @click="$router.back()" />
        <h2>个人账号设置</h2>
      </div>

      <div class="p-body">
        <div class="avatar-upload-box">
          <el-upload
            class="avatar-uploader"
            action="/api/v1/chat/upload_avatar"
            :headers="authHeader"
            :show-file-list="false"
            :on-success="handleAvatarSuccess"
            :before-upload="beforeAvatarUpload"
          >
            <div class="avatar-wrapper">
              <img v-if="user.avatar" :src="fullAvatarUrl" class="avatar-img" />
              <el-avatar v-else :size="120" icon="UserFilled" />
              <div class="hover-mask"><el-icon><Camera /></el-icon></div>
            </div>
          </el-upload>
          <p class="hint">点击更换头像，仅支持 JPG/PNG</p>
        </div>

        <el-descriptions :column="1" border class="user-info-table">
          <el-descriptions-item label="账户名称">{{ user.username }}</el-descriptions-item>
          <el-descriptions-item label="系统角色">
            <el-tag :type="user.role === 'admin' ? 'danger' : 'success'">
              {{ user.role === 'admin' ? '管理员' : '标准用户' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="注册时间">{{ user.created_at || '2025-01-01' }}</el-descriptions-item>
        </el-descriptions>
        
        <div class="admin-entry" v-if="user.role === 'admin'">
  <el-divider>管理员操作</el-divider>
  <el-button type="warning" @click="$router.push('/admin')" :icon="Tools">
    进入知识库管理后台
  </el-button>
</div>

        <div class="logout-section">
          <el-button type="danger" plain :icon="SwitchButton" @click="handleLogout">退出登录</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ArrowLeft, Camera, SwitchButton, Tools } from '@element-plus/icons-vue';
import { ref, onMounted, computed } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import request from '../api/request';

const router = useRouter();
const user = ref({ username: '', avatar: '', role: '', created_at: '' });

const authHeader = { Authorization: `Bearer ${localStorage.getItem('access_token')}` };

const fullAvatarUrl = computed(() => {
  if (!user.value.avatar) return '';
  return `http://localhost:8000${user.value.avatar}?t=${Date.now()}`;
});

const fetchUser = async () => {
  try {
    const res = await request.get('/v1/chat/me');
    user.value = res.data;
  } catch (e) { ElMessage.error('无法加载用户信息'); }
};

const beforeAvatarUpload = (rawFile: any) => {
  if (!['image/jpeg', 'image/png'].includes(rawFile.type)) {
    ElMessage.error('只能上传图片格式!');
    return false;
  }
  return true;
};

const handleAvatarSuccess = (res: any) => {
  user.value.avatar = res.avatar_url;
  ElMessage.success('头像已更新');
};

const handleLogout = () => {
  localStorage.removeItem('access_token');
  // 如果你有存储用户信息，也一并清理
  localStorage.clear(); 
  router.push('/login');
};

onMounted(fetchUser);
</script>

<style scoped lang="scss">
/* 增加一点样式 */
.admin-entry {
  margin-top: 20px;
  text-align: center;
}
.profile-page {
  height: 100vh; width: 100vw; display: flex; justify-content: center; align-items: center;
  background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
}
.profile-card {
  width: 550px; background: rgba(255, 255, 255, 0.85); backdrop-filter: blur(20px);
  border-radius: 30px; padding: 40px; box-shadow: 0 25px 50px rgba(0,0,0,0.1);
}
.p-header { display: flex; align-items: center; gap: 20px; margin-bottom: 30px; h2 { margin: 0; } }
.avatar-upload-box {
  text-align: center; margin-bottom: 40px;
  .avatar-wrapper {
    width: 120px; height: 120px; border-radius: 50%; overflow: hidden;
    margin: 0 auto; border: 4px solid #fff; box-shadow: 0 5px 15px rgba(0,0,0,0.1);
    position: relative; cursor: pointer;
    .avatar-img { width: 100%; height: 100%; object-fit: cover; }
    .hover-mask {
      position: absolute; top: 0; left: 0; width: 100%; height: 100%;
      background: rgba(0,0,0,0.4); color: #fff; display: flex; justify-content: center;
      align-items: center; font-size: 24px; opacity: 0; transition: 0.3s;
    }
    &:hover .hover-mask { opacity: 1; }
  }
  .hint { font-size: 13px; color: #999; margin-top: 10px; }
}
.logout-section { text-align: center; margin-top: 40px; }
</style>