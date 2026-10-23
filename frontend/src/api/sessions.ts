/** 会话与消息历史（P3）。字段与后端 app/api/sessions.py 对应。 */
import {http} from './http'

export interface Session {
  id: string
  title: string
  kb_ids: string[]
  model: string
  temperature: number
  top_p: number
  max_tokens: number
  history_rounds: number
  message_count: number
  created_at: string | null
  updated_at: string | null
}

export interface SessionParams {
  title?: string
  kb_ids?: string[]
  model?: string
  temperature?: number
  top_p?: number
  max_tokens?: number
  history_rounds?: number
}

export interface ChatMessage {
  id: number
  seq: number
  role: 'user' | 'assistant' | 'system'
  content: string
  sources: SourceRef[] | null
  intent: string | null
  rewrite_type: string | null
  route: string | null
  cached: boolean
  latency_ms: number | null
  ttft_ms: number | null
  token_usage: Record<string, number> | null
  error: string | null
  created_at: string | null
}

export interface SourceRef {
  law: string
  chapter_path: string
  article_no: string
  score: number
  snippet: string
  chunk_id?: string
  kind: 'law' | 'qa'
}

export function listSessions(): Promise<Session[]> {
  return http.get<Session[]>('/sessions').then((r) => r.data)
}

export function createSession(payload: SessionParams = {}): Promise<Session> {
  return http.post<Session>('/sessions', payload).then((r) => r.data)
}

export function updateSession(id: string, payload: SessionParams): Promise<Session> {
  return http.patch<Session>(`/sessions/${id}`, payload).then((r) => r.data)
}

export function deleteSession(id: string): Promise<{deleted: string}> {
  return http.delete<{deleted: string}>(`/sessions/${id}`).then((r) => r.data)
}

export function listMessages(id: string, limit = 100): Promise<{count: number; items: ChatMessage[]}> {
  return http
    .get<{count: number; items: ChatMessage[]}>(`/sessions/${id}/messages`, {params: {limit}})
    .then((r) => r.data)
}
