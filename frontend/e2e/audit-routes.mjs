/**
 * 线上页面巡检：逐个打开路由，收集
 *   - 最终 URL（是否被守卫重定向）
 *   - #app 是否渲染出内容
 *   - 控制台 error / 未捕获异常
 *   - 4xx/5xx 响应与请求失败（含静态资源 404）
 * 用法：node e2e/audit-routes.mjs [baseUrl]
 */
import {chromium} from 'playwright'
import fs from 'node:fs'

const BASE = process.argv[2] || 'https://<YOUR_DOMAIN>'  // <YOUR_DOMAIN> 换成你的域名
const ROUTES = ['/', '/chat', '/login', '/register', '/kb', '/kb/kb_civil/parse', '/kb/kb_civil/doc/probe', '/docs', '/openapi.json', '/no-such-page']
const SHOT_DIR = 'e2e/__shots'
fs.mkdirSync(SHOT_DIR, {recursive: true})

const browser = await chromium.launch()
const ctx = await browser.newContext({ignoreHTTPSErrors: true, viewport: {width: 1280, height: 860}})

const rows = []
for (const route of ROUTES) {
  const page = await ctx.newPage()
  const errors = []
  const badReqs = []
  const failed = []
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)) })
  page.on('pageerror', (e) => errors.push('pageerror: ' + String(e.message).slice(0, 200)))
  page.on('response', (r) => { if (r.status() >= 400) badReqs.push(`${r.status()} ${r.url().slice(0, 110)}`) })
  page.on('requestfailed', (r) => failed.push(`${r.failure()?.errorText || 'failed'} ${r.url().slice(0, 110)}`))

  let nav = 'ok'
  try {
    await page.goto(BASE + route, {waitUntil: 'domcontentloaded', timeout: 30000})
  } catch (e) {
    nav = 'goto-fail: ' + String(e.message).split('\n')[0].slice(0, 120)
  }
  await page.waitForTimeout(2600)

  const info = await page.evaluate(() => {
    const app = document.getElementById('app')
    const text = (app?.innerText || '').replace(/\s+/g, ' ').trim()
    return {
      title: document.title,
      appChildren: app ? app.children.length : -1,
      textLen: text.length,
      head: text.slice(0, 110),
    }
  }).catch(() => ({title: '-', appChildren: -1, textLen: -1, head: 'evaluate-fail'}))

  const name = route.replace(/[^\w]+/g, '_') || 'root'
  await page.screenshot({path: `${SHOT_DIR}/${name}.png`, fullPage: false}).catch(() => {})
  rows.push({route, nav, finalUrl: page.url().replace(BASE, ''), ...info, errors, badReqs, failed})
  await page.close()
}
await browser.close()

const pad = (s, n) => String(s).padEnd(n)
console.log(pad('路由', 26) + pad('最终URL', 30) + pad('title', 22) + pad('渲染字符', 10) + '异常')
console.log('-'.repeat(120))
for (const r of rows) {
  const issue = r.errors.length + r.badReqs.length + r.failed.length
  console.log(
    pad(r.route, 26) + pad(r.finalUrl || '(同左)', 30) + pad(r.title, 22) +
    pad(r.textLen, 10) + (issue ? `${issue} 条` : '无')
  )
}

console.log('\n===== 明细 =====')
for (const r of rows) {
  if (!r.errors.length && !r.badReqs.length && !r.failed.length && r.nav === 'ok' && r.textLen > 0) continue
  console.log(`\n[${r.route}] nav=${r.nav} final=${r.finalUrl} title=${r.title} chars=${r.textLen}`)
  console.log(`  首屏文字: ${r.head}`)
  for (const e of [...new Set(r.errors)].slice(0, 6)) console.log('  控制台: ' + e)
  for (const b of [...new Set(r.badReqs)].slice(0, 8)) console.log('  响应: ' + b)
  for (const f of [...new Set(r.failed)].slice(0, 8)) console.log('  请求失败: ' + f)
}
