/** 认证状态（token + 当前用户），会话级持久化到 localStorage。 */
import {computed, ref} from 'vue'
import {defineStore} from 'pinia'

import * as authApi from '@/api/auth'
import type {LoginResult, User} from '@/api/auth'
import {TOKEN_KEY, USER_KEY, clearAuth} from '@/api/http'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem(TOKEN_KEY) || '')
  const user = ref<User | null>(readUser())

  const isLoggedIn = computed(() => !!token.value)
  const username = computed(() => user.value?.username || '')

  function readUser(): User | null {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || 'null') as User | null
    } catch {
      return null
    }
  }

  function persist(result: LoginResult) {
    token.value = result.access_token
    user.value = result.user
    localStorage.setItem(TOKEN_KEY, result.access_token)
    localStorage.setItem(USER_KEY, JSON.stringify(result.user))
  }

  async function login(payload: {
    username: string
    password: string
    captcha_id: string
    captcha_code: string
  }): Promise<LoginResult> {
    const result = await authApi.login(payload)
    persist(result)
    return result
  }

  async function register(payload: {
    username: string
    password: string
    captcha_id: string
    captcha_code: string
  }) {
    return authApi.register(payload)
  }

  /** 刷新页面后用 token 换最新用户信息（token 失效会 401 → 拦截器跳登录）。 */
  async function refresh() {
    if (!token.value) return
    try {
      user.value = await authApi.fetchMe()
      localStorage.setItem(USER_KEY, JSON.stringify(user.value))
    } catch {
      logout()
    }
  }

  function logout() {
    token.value = ''
    user.value = null
    clearAuth()
  }

  return {token, user, isLoggedIn, username, login, register, refresh, logout}
})
