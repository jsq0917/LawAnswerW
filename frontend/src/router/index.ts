/** 路由与守卫（§4.2 P1：未登录访问任何页 → /login?redirect=...）。 */
import {createRouter, createWebHistory} from 'vue-router'
import {ElMessage} from 'element-plus'

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

/* ---------------------------------------------------------------------------
 * 懒加载分块失败自愈
 * ---------------------------------------------------------------------------
 * 除登录/注册/对话页外，各页面都是 `() => import(...)` 的懒加载路由，产物文件名
 * 带内容哈希（如 KbManageView-D1VDNjMa.js）。发版重建后哈希全变，旧文件被新构建
 * 覆盖；此时若浏览器仍持有**旧的资源清单**（旧标签页没关、index.html 被中间层缓存），
 * 点击一个尚未访问过的路由（例如"知识库"）就会去取已经不存在的旧哈希分块：
 *
 *   1. 动态 import 直接 reject；
 *   2. vue-router 中止这次导航 —— URL 不变、当前页面不变；
 *   3. router-link 的点击处理器吞掉了这个 reject，用户侧没有任何提示。
 *
 * 表现就是"按钮点了没反应"，且只有控制台里能看到 404 / Failed to fetch
 * dynamically imported module。这里把它兜住：整页重载到目标路由，重新拉取
 * index.html 与最新的资源清单；同一目标只自动重试一次，避免死循环。
 * ------------------------------------------------------------------------- */
const CHUNK_RETRY_KEY = 'lawanswer.chunk-retry'
const CHUNK_ERROR_RE =
  /Failed to fetch dynamically imported module|Importing a module script failed|Unable to preload CSS|error loading dynamically imported module|Loading chunk \d+ failed/i

/** 判定是否为"分块取不到"这类错误（其余导航错误不在这里兜）。 */
function isChunkLoadError(error: unknown): boolean {
  const msg = error instanceof Error ? error.message : String(error ?? '')
  return CHUNK_ERROR_RE.test(msg)
}

router.onError((error, to) => {
  if (!isChunkLoadError(error)) return
  const target = to?.fullPath || window.location.pathname + window.location.search
  if (sessionStorage.getItem(CHUNK_RETRY_KEY) === target) {
    // 重载过一次仍然失败：说明不是缓存问题，别再自动刷新，明确告知用户
    sessionStorage.removeItem(CHUNK_RETRY_KEY)
    console.error('[router] 分块加载失败，自动重载无效：', error)
    ElMessage.error('页面资源已更新，请按 Ctrl+F5 强制刷新后重试')
    return
  }
  sessionStorage.setItem(CHUNK_RETRY_KEY, target)
  // 必须整页重载（而不是 router.replace），否则拿不到新的资源清单
  window.location.assign(target)
})

router.afterEach((to) => {
  // 目标页成功打开 → 清掉标记，下次发版还能再自愈一次
  if (sessionStorage.getItem(CHUNK_RETRY_KEY) === to.fullPath) {
    sessionStorage.removeItem(CHUNK_RETRY_KEY)
  }
})

export default router
