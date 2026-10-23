/**
 * 阶段 6 浏览器走查：注册 → 登录 → 对话（流式）→ 知识库 → 解析上传 → 预览/编辑 → 删除。
 *
 * 前置：
 *   - 后端 http://127.0.0.1:8101（uv run python -m app.server）
 *   - 前端 http://localhost:5180（npx vite --port 5180 --strictPort）
 * 运行：node docs/e2e/walkthrough.mjs
 * 产物：docs/screenshots/*.png + 控制台错误清单
 */
import {chromium} from 'playwright'
import {mkdirSync, readFileSync, writeFileSync} from 'node:fs'
import {resolve} from 'node:path'

const BASE = process.env.E2E_BASE || 'http://localhost:5180'
const ROOT = resolve(import.meta.dirname, '../..')
const SHOTS = resolve(ROOT, 'docs/screenshots')
const SAMPLE_TEMPLATE = resolve(ROOT, 'docs/e2e/sample.md')
const SAMPLE = resolve(ROOT, `docs/e2e/sample-${Date.now().toString(36)}.md`)
const USER = `e2e_${Date.now().toString(36)}`
const PASS = 'secret123'

mkdirSync(SHOTS, {recursive: true})
// 每次走查生成唯一样例：内容变了才不会命中"内容未变 → 跳过"分支
writeFileSync(
  SAMPLE,
  readFileSync(SAMPLE_TEMPLATE, 'utf-8').replace('端到端走查样例法规', `端到端走查样例法规 ${Date.now()}`),
  'utf-8',
)

const consoleErrors = []
const steps = []

function log(step, detail = '') {
  steps.push({step, detail, at: new Date().toISOString()})
  console.log(`[step] ${step}${detail ? ` — ${detail}` : ''}`)
}

/**
 * 从页面上的验证码 SVG 里解析出 4 位数字。
 * 走查脚本需要"自己看图"，所以不做 OCR——后端 SVG 里每个字符就是一个
 * <text>…</text> 节点，直接解 base64 取文本即可（也顺带验证了出题链路）。
 */
async function captcha(page) {
  const img = page.locator('.captcha__img img')
  await img.waitFor({state: 'visible', timeout: 15000})
  const src = await img.getAttribute('src')
  if (!src || !src.includes('base64,')) throw new Error(`验证码图片未就绪: ${src?.slice(0, 40)}`)
  const svg = Buffer.from(src.split('base64,')[1], 'base64').toString('utf-8')
  const code = [...svg.matchAll(/>([0-9])<\/text>/g)].map((m) => m[1]).join('')
  if (!/^\d{4}$/.test(code)) throw new Error(`验证码解析失败: ${JSON.stringify(code)}`)
  return {code}
}

async function shot(page, name) {
  await page.screenshot({path: resolve(SHOTS, `${name}.png`), fullPage: false})
  log('截图', `${name}.png`)
}

async function waitSendable(page) {
  // 注意 waitForFunction 第二参是 arg，options 必须放第三位
  await page.waitForFunction(
    () => {
      const btn = document.querySelector('.composer__actions .btn')
      return btn && btn.textContent && btn.textContent.includes('发送')
    },
    null,
    {timeout: 300000},
  )
}

/** 发一个问题：等流真正开始（助手气泡数增加）再等它结束 */
async function ask(page, text, minAssistant) {
  const before = await page.locator('.bubble--assistant').count()
  await page.fill('.composer__input', text)
  await page.keyboard.press('Enter')
  await page.waitForFunction(
    ({n}) => document.querySelectorAll('.bubble--assistant').length >= n,
    {n: before + minAssistant},
    {timeout: 120000},
  )
  await waitSendable(page)
  await page.waitForTimeout(400)
}

const browser = await chromium.launch()
const context = await browser.newContext({viewport: {width: 1440, height: 900}, locale: 'zh-CN'})
const page = await context.newPage()
page.on('console', (msg) => {
  if (msg.type() === 'error') consoleErrors.push(`console: ${msg.text()}`)
})
page.on('pageerror', (err) => consoleErrors.push(`pageerror: ${err.message}`))

try {
  /* ---------- P2 注册 ---------- */
  await page.goto(`${BASE}/register`)
  await page.waitForSelector('#reg-username')
  log('打开注册页')
  await shot(page, '06-register')

  const capReg = await captcha(page)
  await page.fill('#reg-username', USER)
  await page.fill('#reg-password', PASS)
  await page.fill('#reg-confirm', PASS)
  await page.locator('.captcha input').fill(capReg.code)
  await page.click('button[type=submit]')
  await page.waitForURL(/\/login/, {timeout: 20000})
  log('注册成功，跳转登录', USER)

  /* ---------- P1 登录 ---------- */
  await page.waitForSelector('#login-username')
  const prefill = await page.inputValue('#login-username')
  log('登录页账号回填', prefill)
  const capLogin = await captcha(page)
  await page.fill('#login-password', PASS)
  await page.locator('.captcha input').fill(capLogin.code)
  await shot(page, '01-login')
  await page.click('button[type=submit]')
  await page.waitForURL(/\/chat/, {timeout: 20000})
  log('登录成功，进入对话页')

  /* ---------- P3 对话 ---------- */
  await page.waitForSelector('.thread__scroll')
  await page.waitForTimeout(600)
  await shot(page, '02-chat-empty')

  await ask(page, '试用期最长可以约定多久？', 1)
  log('已发送问题，流式接收完成')
  await shot(page, '03-chat-answer')

  // 展开来源
  const toggle = page.locator('.sources__toggle').first()
  if (await toggle.count()) {
    await toggle.click()
    await page.waitForTimeout(300)
    await shot(page, '04-chat-sources')
  }

  // 纯法条问题：无 LLM key 时应出现明确错误提示（llm_not_configured）
  await ask(page, '劳动合同法第八十二条原文怎么规定', 1)
  log('纯法条问题返回（无 key 时应显示配置提示）')
  const errText = await page.locator('.bubble__error').last().textContent().catch(() => '')
  log('纯法条问题返回', (errText || '').slice(0, 40))
  await shot(page, '05-chat-vector-without-key')

  /* ---------- P6 知识库管理 ---------- */
  await page.click('a[href="/kb"]')
  await page.waitForSelector('.kb-card', {timeout: 15000})
  await shot(page, '07-kb-list')
  log('知识库列表')

  await page.locator('.kb-card', {hasText: '劳动法'}).first().click()
  await page.waitForSelector('.doc-table__row', {timeout: 15000})
  await shot(page, '08-kb-documents')
  log('文档列表')

  /* ---------- P5 预览 / 编辑 ---------- */
  await page.locator('.doc-table__row').first().click()
  await page.waitForSelector('.chunk', {timeout: 20000})
  await page.waitForTimeout(500)
  await shot(page, '09-kb-preview')
  log('块预览')

  await page.locator('.btn', {hasText: '编辑'}).first().click()
  await page.waitForSelector('.editor__textarea', {timeout: 10000})
  await shot(page, '10-chunk-edit')
  log('进入块编辑态')
  await page.locator('.btn', {hasText: '取消'}).first().click()

  /* ---------- P4 上传解析 ---------- */
  // 从预览页返回知识库 → 文档列表 → 上传
  await page.click('a[href="/kb"]')
  await page.waitForSelector('.kb-card')
  await page.locator('.kb-card', {hasText: '劳动法'}).first().click()
  await page.waitForSelector('.btn', {hasText: '上传文档'})
  await page.locator('.btn', {hasText: '上传文档'}).click()
  await page.waitForSelector('input[type=file]', {state: 'attached'})
  await page.setInputFiles('input[type=file]', SAMPLE)
  await page.waitForTimeout(400)
  await shot(page, '11-kb-parse-ready')
  log('已选择文件')

  await page.locator('.btn', {hasText: '开始解析'}).click()
  log('已提交解析，等待完成…')
  await page.waitForFunction(
    () => document.body.innerText.includes('完成') && !document.body.innerText.includes('解析中'),
    null,
    {timeout: 300000},
  )
  await page.waitForTimeout(800)
  await shot(page, '12-kb-parse-done')
  log('解析完成')

  /* ---------- 删除走查文档 ---------- */
  await page.click('a[href="/kb"]')
  await page.waitForSelector('.kb-card')
  await page.locator('.kb-card', {hasText: '劳动法'}).first().click()
  await page.waitForSelector('.doc-table__row')
  const sampleName = SAMPLE.split(/[\\/]/).pop()
  const target = page.locator('.doc-table__row', {hasText: sampleName}).first()
  await target.locator('.link-btn--danger').click()
  await page.waitForSelector('.el-message-box', {timeout: 10000})
  await shot(page, '13-delete-confirm')
  await page.locator('.el-message-box .el-button--primary').click()
  await page.waitForTimeout(1200)
  await shot(page, '14-after-delete')
  log('删除走查文档')
} catch (err) {
  log('走查失败', err.message)
  await shot(page, '99-failure').catch(() => {})
  process.exitCode = 1
} finally {
  writeFileSync(
    resolve(ROOT, 'docs/e2e/last-run.json'),
    JSON.stringify({base: BASE, user: USER, steps, consoleErrors}, null, 2),
    'utf-8',
  )
  console.log(`\n控制台错误 ${consoleErrors.length} 条`)
  consoleErrors.slice(0, 10).forEach((e) => console.log(`  - ${e}`))
  await browser.close()
}
