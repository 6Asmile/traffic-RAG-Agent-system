import { createRouter, createWebHistory } from 'vue-router';
import Login from '../views/Login.vue';
import Chat from '../views/Chat.vue';
import Profile from '../views/Profile.vue';
import Admin from '../views/Admin.vue';
import GraphView from '../views/GraphView.vue';
import Landing from '../views/Landing.vue';

const routes = [
  { 
    path: '/', 
    name: 'Landing', 
    component: Landing,
    meta: { requiresAuth: true } // 必须登录后才能选择功能
  },
  { path: '/login', name: 'Login', component: Login },
  { 
    path: '/chat', 
    name: 'Chat', 
    component: Chat,
    meta: { requiresAuth: true }
  },
  { 
    path: '/profile', 
    name: 'Profile', 
    component: Profile,
    meta: { requiresAuth: true }
  },
   { 
    path: '/admin', 
    name: 'Admin', 
    component: Admin,
    meta: { requiresAuth: true }
  },
  { 
    path: '/graph', 
    name: 'Graph', 
    component: GraphView,
    meta: { requiresAuth: true }
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to, _from, next) => {
  const token = localStorage.getItem('access_token');
  
  if (to.meta.requiresAuth && !token) {
    // 没登录且访问受限页面 -> 去登录
    next('/login');
  } else if (to.path === '/login' && token) {
    // 已登录且访问登录页 -> 去聊天
    next('/chat');
  } else {
    next();
  }
});

export default router;