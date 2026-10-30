/**
 * 移动端显示巡检：390x844 下打开各页面，检测横向溢出与关键区块是否可见。
 * 用法：node e2e/audit-mobile.mjs <baseUrl> <tokenFile|-->
 */
import {chromium} from 'playwright'
import fs from 'node:fs'

const BASE = process.argv[2] || 'https://<YOUR_DOMAIN>'  // <YOUR_DOMAIN> 换成你的域名
const tokenArg = process.argv[3] || '--'
const TOKEN = tokenArg === '--' ? '' : fs.readFileSync(tokenArg, 'utf8').trim()
const SHOT_DIR = 'e2e/__shots'
fs.mkdirSync(SHOT_DIR, {recursive: true})

const ROUTES = [['/login', 'm-login'], ['/register', 'm-register'], ['/chat', 'm-chat'], ['/kb', 'm-kb']]

const browser = await chromium.launch()
const ctx = await browser.newContext({
  ignoreHTTPSErrors: true,
  viewport: {width: 390, height: 844},
  deviceScaleFactor: 2,
  isMobile: true,
  hasTouch: true,
})
if (TOKEN) {
  await ctx.addInitScript(([t]) => {
    localStorage.setItem('lawanswer.token', t)
    localStorage.setItem('lawanswer.user', JSON.stringify({id: 1, username: 'admin'}))
  }, [TOKEN])
}

for (const [route, shot] of ROUTES) {
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e.message).slice(0, 160)))
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 160)) })
  await page.goto(BASE + route, {waitUntil: 'domcontentloaded', timeout: 30000})
  await page.waitForTimeout(2600)
  const m = await page.evaluate(() => {
    const de = document.documentElement
    const over = []
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect()
      if (r.width > 0 && r.right > window.innerWidth + 1.5) {
        over.push(`${el.tagName.toLowerCase()}.${(el.className || '').toString().split(' ')[0]} right=${Math.round(r.right)}`)
      }
    }
    return {
      url: location.pathname,
      scrollW: de.scrollWidth,
      innerW: window.innerWidth,
      overflow: [...new Set(over)].slice(0, 5),
      text: (document.getElementById('app')?.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 90),
    }
  })
  await page.screenshot({path: `${SHOT_DIR}/${shot}.png`})
  console.log(`${route}  →  ${m.url}  scrollW=${m.scrollW}/${m.innerW}  溢出=${m.overflow.length}${m.overflow.length ? ' ' + m.overflow.join(' ; ') : ''}`)
  console.log(`   文本: ${m.text}`)
  if (errors.length) console.log(`   异常: ${[...new Set(errors)].slice(0, 3).join(' | ')}`)
  await page.close()
}
await browser.close()
