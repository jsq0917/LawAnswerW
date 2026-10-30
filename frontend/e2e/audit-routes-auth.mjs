/**
 * 登录态页面巡检：注入 token 后再打开各页面，抓控制台错误 / 4xx-5xx / 白屏。
 * 用法：node e2e/audit-routes-auth.mjs <baseUrl> <tokenFile>
 */
import {chromium} from 'playwright'
import fs from 'node:fs'

const BASE = process.argv[2] || 'https://<YOUR_DOMAIN>'  // <YOUR_DOMAIN> 换成你的域名
const TOKEN = fs.readFileSync(process.argv[3] || '/tmp/probe-token.txt', 'utf8').trim()
const SHOT_DIR = 'e2e/__shots'
fs.mkdirSync(SHOT_DIR, {recursive: true})

const ROUTES = [
  ['/chat', 'chat'],
  ['/kb', 'kb'],
  ['/kb/kb_civil/parse', 'parse'],
  ['/kb/kb_civil/doc/Y6GDZA3Z3AVQW87M01M5YAKTA9', 'preview'],
  ['/kb/__no_such_kb__', 'kb-404'],
  ['/docs', 'docs'],
  ['/openapi.json', 'openapi'],
]

const browser = await chromium.launch()
const ctx = await browser.newContext({ignoreHTTPSErrors: true, viewport: {width: 1360, height: 900}})
await ctx.addInitScript(([token]) => {
  localStorage.setItem('lawanswer.token', token)
  localStorage.setItem('lawanswer.user', JSON.stringify({id: 1, username: 'admin'}))
}, [TOKEN])

const rows = []
for (const [route, shot] of ROUTES) {
  const page = await ctx.newPage()
  const errors = []
  const bad = []
  const failed = []
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 260)) })
  page.on('pageerror', (e) => errors.push('pageerror: ' + String(e.message).slice(0, 260)))
  page.on('response', (r) => { if (r.status() >= 400) bad.push(`${r.status()} ${r.request().method()} ${r.url().replace(BASE, '').slice(0, 110)}`) })
  page.on('requestfailed', (r) => failed.push(`${r.failure()?.errorText || 'failed'} ${r.url().replace(BASE, '').slice(0, 110)}`))

  let nav = 'ok'
  try {
    await page.goto(BASE + route, {waitUntil: 'domcontentloaded', timeout: 30000})
  } catch (e) {
    nav = 'goto-fail: ' + String(e.message).split('\n')[0].slice(0, 120)
  }
  await page.waitForTimeout(3200)

  const info = await page.evaluate(() => {
    const app = document.getElementById('app')
    const t = (app?.innerText || '').replace(/\n{2,}/g, '\n').trim()
    return {title: document.title, children: app ? app.children.length : -1, len: t.length, head: t.slice(0, 140).replace(/\n/g, ' | ')}
  }).catch(() => ({title: '-', children: -1, len: -1, head: 'evaluate-fail'}))

  await page.screenshot({path: `${SHOT_DIR}/auth-${shot}.png`, fullPage: false}).catch(() => {})
  rows.push({route, final: page.url().replace(BASE, ''), nav, ...info, errors, bad, failed})
  await page.close()
}
await browser.close()

const pad = (s, n) => String(s).padEnd(n)
console.log(pad('路由', 46) + pad('最终', 16) + pad('title', 22) + pad('字符', 8) + '异常')
console.log('-'.repeat(118))
for (const r of rows) {
  const n = r.errors.length + r.bad.length + r.failed.length
  console.log(pad(r.route, 46) + pad(r.final, 16) + pad(r.title, 22) + pad(r.len, 8) + (n ? `${n} 条` : '无'))
}
console.log('\n===== 明细 =====')
for (const r of rows) {
  if (!r.errors.length && !r.bad.length && !r.failed.length && r.len > 0) continue
  console.log(`\n[${r.route}] nav=${r.nav} final=${r.final} chars=${r.len}`)
  console.log(`  首屏: ${r.head}`)
  for (const e of [...new Set(r.errors)].slice(0, 6)) console.log('  控制台: ' + e)
  for (const b of [...new Set(r.bad)].slice(0, 10)) console.log('  响应: ' + b)
  for (const f of [...new Set(r.failed)].slice(0, 6)) console.log('  失败: ' + f)
}
