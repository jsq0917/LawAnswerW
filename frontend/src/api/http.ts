/** HTTP 基础设施：token 注入、统一错误、401 跳登录。
 *
 * 后端错误信封固定为 {code, message, detail, request_id}（app/core/errors.py），
 * 前端只对接这一种结构，页面里 catch 到的都是 ApiError。
 */
import axios, {AxiosError} from 'axios'

export const TOKEN_KEY = 'lawanswer.token'
export const USER_KEY = 'lawanswer.user'

export interface ApiErrorBody {
  code: string
  message: string
  detail?: unknown
  request_id?: string
}

export class ApiError extends Error {
  code: string
  status: number
  detail: unknown

  constructor(message: string, code = 'unknown', status = 0, detail: unknown = null) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.detail = detail
  }
}

export const http = axios.create({baseURL: '/api', timeout: 30000})

http.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

http.interceptors.response.use(
  (resp) => resp,
  (error: AxiosError<ApiErrorBody>) => {
    const status = error.response?.status ?? 0
    const body = error.response?.data
    if (status === 401) {
      clearAuth()
      const here = window.location.pathname + window.location.search
      if (!here.startsWith('/login') && !here.startsWith('/register')) {
        window.location.assign(`/login?redirect=${encodeURIComponent(here)}`)
      }
    }
    return Promise.reject(
      new ApiError(body?.message || error.message || '网络异常，请稍后重试', body?.code, status, body?.detail),
    )
  },
)

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return '未知错误'
}
