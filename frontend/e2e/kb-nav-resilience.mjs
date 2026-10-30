/**
 * 回归用例：发版后"旧资源清单 + 分块已删"导致点击导航无反应。
 *
 * 场景：进入 /chat 后，让"知识库"路由的分块请求返回 404（模拟旧标签页仍持有旧哈希），
 * 点击左侧"知识库"。修复前：URL 不变、页面不变（点了没反应）；
 * 修复后：router.onError 捕获 → 整页重载 → 重新拉取资源清单 → 正常进入知识库。
 *
 * 运行：node e2e/kb-nav-resilience.mjs   （需先 npm run build && npx vite preview --port 5181）
 */
import {chromium} from 'playwright'

const BASE = process.env.E2E_BASE || 'http://127.0.0.1:5181'

const KB = {
  id: 'kb_labor',
  name: '劳动法知识库',
  description: '劳动法相关法条',
  embedding_model: 'bge-m3',
  doc_count: 3,
  chunk_count: 120,
  updated_at: '2026-10-27T10:00:00',
}

const browser = await chromium.launch()
const context = await browser.newContext({viewport: {width: 1440, height: 900}, locale: 'zh-CN'})
const logs = []
context.on('console', (m) => logs.push(`${m.type()}: ${m.text()}`))
context.on('pageerror', (e) => logs.push(`pageerror: ${e.message}`))
context.addInitScript(() => {
  localStorage.setItem('lawanswer.token', 'stub-token')
  localStorage.setItem('lawanswer.user', JSON.stringify({id: 1, username: 'stub'}))
})

const page = await context.newPage()

// 只让"第一次"分块请求 404：重载后索引页已是新的，同名分块应能正常取到
let staleHit = 0

await page.route('**/*', async (route) => {
  const u = new URL(route.request().url())
  if (u.pathname.startsWith('/api/')) {
    let body = {}
    if (u.pathname === '/api/kbs') body = [KB]
    else if (/^\/api\/kbs\/[^/]+\/documents$/.test(u.pathname)) body = []
    else if (u.pathname === '/api/sessions') body = []
    else if (u.pathname === '/api/auth/me') body = {id: 1, username: 'stub'}
    return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(body)})
  }
  if (u.pathname.includes('KbManageView-') && staleHit === 0) {
    staleHit += 1
    return route.fulfill({status: 404, contentType: 'text/plain', body: 'Not Found'})
  }
  return route.fallback()
})

await page.goto(`${BASE}/chat`, {waitUntil: 'domcontentloaded'})
await page.waitForSelector('.nav__item')
console.log('起点 URL:', page.url())

await page.locator('.nav__item', {hasText: '知识库'}).first().click()

// 修复后的期望：自愈重载并落在 /kb，知识库卡片可见
await page.waitForSelector('.kb-card', {timeout: 20000})
await page.waitForTimeout(500)

console.log('结束 URL:', page.url())
console.log('分块 404 命中次数:', staleHit)
console.log('知识库卡片数:', await page.locator('.kb-card').count())
console.log('顶栏标题:', await page.locator('.topbar__title').textContent().catch(() => '(无)'))
console.log('重试标记已清理:', !(await page.evaluate(() => sessionStorage.getItem('lawanswer.chunk-retry'))))
console.log('\n--- 控制台 ---')
logs.forEach((l) => console.log(' ', l))

const ok = page.url().endsWith('/kb') && (await page.locator('.kb-card').count()) === 1
await browser.close()
console.log(ok ? '\n[PASS] 分块 404 时能自愈进入知识库' : '\n[FAIL] 仍未恢复')
process.exitCode = ok ? 0 : 1
