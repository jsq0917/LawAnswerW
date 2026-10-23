/** 路由与守卫（§4.2 P1：未登录访问任何页 → /login?redirect=...）。 */
import {createRouter, createWebHistory} from 'vue-router'

import {useAuthStore} from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {path: '/', redirect: '/chat'},
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: {public: true, title: '登录'},
    },
    {
      path: '/register',
      name: 'register',
      component: () => import('@/views/RegisterView.vue'),
      meta: {public: true, title: '注册'},
    },
    {
      path: '/chat',
      name: 'chat',
      component: () => import('@/views/ChatView.vue'),
      meta: {title: '对话'},
    },
    {
      path: '/kb',
      name: 'kb',
      component: () => import('@/views/KbManageView.vue'),
      meta: {title: '知识库'},
    },
    {
      path: '/kb/:kbId/parse',
      name: 'kb-parse',
      component: () => import('@/views/KbParseView.vue'),
      meta: {title: '知识库解析'},
    },
    {
      path: '/kb/:kbId/doc/:docId',
      name: 'kb-preview',
      component: () => import('@/views/KbPreviewView.vue'),
      meta: {title: '文档预览'},
    },
    {path: '/:pathMatch(.*)*', redirect: '/chat'},
  ],
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  document.title = to.meta.title ? `${to.meta.title} · LawAnswer` : 'LawAnswer'
  if (to.meta.public) {
    // 已登录再访问登录/注册 → 直接进对话页
    if (auth.isLoggedIn && (to.name === 'login' || to.name === 'register')) {
      return {name: 'chat'}
    }
    return true
  }
  if (!auth.isLoggedIn) {
    return {name: 'login', query: {redirect: to.fullPath}}
  }
  return true
})

export default router
