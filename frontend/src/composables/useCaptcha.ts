/** 图形验证码的加载 + 自愈重试。
 *
 * 为什么要重试：验证码依赖 Redis。2026-10-27 线上 Redis 因卷属主错配变成只读后，
 * `/auth/captcha` 持续 502，而登录页只会弹一句 toast，图片位置永远停在"加载中…"，
 * 用户除了手动刷新整页没有任何自救途径——他自己不可能知道"过十几秒会自己好"。
 *
 * 策略：
 * * 只有**服务端暂时不可用**才重试（5xx / 网络层异常 / `upstream_error`）；
 *   验证码输错、参数错误这类 4xx 不重试，重试也没用。
 * * 退避 2s → 5s → 10s，最多 3 次；倒计时显示在按钮上，用户可以不等、直接点。
 * * 组件卸载后不再重试（避免登录页已跳走还在发请求）。
 */
import {onUnmounted, ref} from 'vue'

import {fetchCaptcha} from '@/api/auth'
import {ApiError, errorText} from '@/api/http'

const RETRY_DELAYS_MS = [2000, 5000, 10000]

export function useCaptcha() {
  const id = ref('')
  const image = ref('')
  const loading = ref(false)
  const error = ref('')
  /** >0 表示正在倒计时等待自动重试，单位秒。 */
  const retryIn = ref(0)

  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let tickTimer: ReturnType<typeof setInterval> | null = null
  let attempt = 0
  let disposed = false

  function clearTimers() {
    if (retryTimer !== null) {
      clearTimeout(retryTimer)
      retryTimer = null
    }
    if (tickTimer !== null) {
      clearInterval(tickTimer)
      tickTimer = null
    }
    retryIn.value = 0
  }

  function shouldRetry(err: unknown): boolean {
    if (err instanceof ApiError) {
      // status=0 是网络层失败（断网、DNS、超时），也值得再试
      return err.status === 0 || err.status >= 500 || err.code === 'upstream_error'
    }
    return true
  }

  function scheduleRetry() {
    const delay = RETRY_DELAYS_MS[attempt]
    if (delay === undefined) return
    attempt += 1
    retryIn.value = Math.ceil(delay / 1000)
    tickTimer = setInterval(() => {
      if (retryIn.value > 0) retryIn.value -= 1
    }, 1000)
    retryTimer = setTimeout(() => {
      clearTimers()
      void fetchOnce()
    }, delay)
  }

  async function fetchOnce() {
    loading.value = true
    error.value = ''
    try {
      const data = await fetchCaptcha()
      id.value = data.captcha_id
      image.value = data.image
      attempt = 0
    } catch (err) {
      image.value = ''
      error.value = errorText(err)
      if (!disposed && shouldRetry(err)) scheduleRetry()
    } finally {
      loading.value = false
    }
  }

  /** 用户主动刷新（点图、提交失败后）：重置退避计数，立刻请求。 */
  async function reload() {
    attempt = 0
    clearTimers()
    await fetchOnce()
  }

  onUnmounted(() => {
    disposed = true
    clearTimers()
  })

  return {id, image, loading, error, retryIn, reload}
}
