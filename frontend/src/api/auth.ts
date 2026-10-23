/** 认证接口（P1/P2）。字段与后端 app/api/auth.py 一一对应。 */
import {http} from './http'

export interface Captcha {
  captcha_id: string
  image: string // data:image/svg+xml;base64,...
  expires_in: number
}

export interface User {
  id: number
  username: string
}

export interface LoginResult {
  access_token: string
  token_type: string
  expires_in: number
  user: User
}

export interface UsernameCheck {
  available: boolean
  reason: string | null
}

export function fetchCaptcha(): Promise<Captcha> {
  return http.post<Captcha>('/auth/captcha').then((r) => r.data)
}

export function checkUsername(username: string): Promise<UsernameCheck> {
  return http.get<UsernameCheck>('/auth/check-username', {params: {username}}).then((r) => r.data)
}

export function register(payload: {
  username: string
  password: string
  captcha_id: string
  captcha_code: string
}): Promise<User> {
  return http.post<User>('/auth/register', payload).then((r) => r.data)
}

export function login(payload: {
  username: string
  password: string
  captcha_id: string
  captcha_code: string
}): Promise<LoginResult> {
  return http.post<LoginResult>('/auth/login', payload).then((r) => r.data)
}

export function fetchMe(): Promise<User> {
  return http.get<User>('/auth/me').then((r) => r.data)
}
