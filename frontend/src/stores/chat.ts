/** 对话状态：会话列表 + 当前会话 + 消息流 + SSE 事件归并。
 *
 * 设计取舍：
 * * 会话列表与消息都放 store —— 去知识库页再回来不丢上下文；
 * * SSE 事件在这里归并为"一条 assistant 消息"，视图只负责渲染；
 * * 参数（模型/温度/topP/max_tokens/轮数）与会话绑定，切换会话自动回填。
 */
import {computed, ref} from 'vue'
import {defineStore} from 'pinia'

import * as sessionApi from '@/api/sessions'
import type {ChatMessage, Session, SessionParams, SourceRef} from '@/api/sessions'
import type {ChatMeta, SSEEvent} from '@/api/chat'
import {errorText} from '@/api/http'
import {useSSE} from '@/composables/useSSE'

export type ChatStage = 'intent' | 'retrieval' | 'rerank' | 'generate' | ''

interface AssistantDraft {
  content: string
  sources: SourceRef[]
  meta: ChatMeta | null
  stage: ChatStage
  error: string | null
  ttft_ms: number
  latency_ms: number
}

function emptyDraft(): AssistantDraft {
  return {content: '', sources: [], meta: null, stage: '', error: null, ttft_ms: 0, latency_ms: 0}
}

export const useChatStore = defineStore('chat', () => {
  const sessions = ref<Session[]>([])
  const currentId = ref<string>('')
  const messages = ref<ChatMessage[]>([])
  const draft = ref<AssistantDraft | null>(null)
  const loadingSessions = ref(false)
  const loadingMessages = ref(false)
  const lastQuery = ref('')

  const sse = useSSE()
  const streaming = computed(() => sse.streaming.value)

  const currentSession = computed(() => sessions.value.find((s) => s.id === currentId.value) || null)
  const kbIds = computed(() => currentSession.value?.kb_ids || [])

  async function loadSessions() {
    loadingSessions.value = true
    try {
      sessions.value = await sessionApi.listSessions()
      if (!currentId.value && sessions.value.length) {
        await selectSession(sessions.value[0].id)
      }
    } finally {
      loadingSessions.value = false
    }
  }

  async function createSession(params: SessionParams = {}): Promise<Session> {
    const session = await sessionApi.createSession(params)
    sessions.value.unshift(session)
    currentId.value = session.id
    messages.value = []
    return session
  }

  async function selectSession(id: string) {
    if (sse.streaming.value) sse.abort()
    currentId.value = id
    draft.value = null
    loadingMessages.value = true
    try {
      const page = await sessionApi.listMessages(id)
      messages.value = page.items
    } catch (err) {
      messages.value = []
      throw err
    } finally {
      loadingMessages.value = false
    }
  }

  async function renameSession(id: string, title: string) {
    const updated = await sessionApi.updateSession(id, {title})
    const idx = sessions.value.findIndex((s) => s.id === id)
    if (idx >= 0) sessions.value[idx] = updated
  }

  async function updateParams(id: string, params: SessionParams) {
    const updated = await sessionApi.updateSession(id, params)
    const idx = sessions.value.findIndex((s) => s.id === id)
    if (idx >= 0) sessions.value[idx] = updated
    return updated
  }

  /** 乐观更新：先移除，失败再回滚（§4.2 P3 删除二次确认 → 乐观更新）。 */
  async function removeSession(id: string) {
    const snapshot = [...sessions.value]
    sessions.value = sessions.value.filter((s) => s.id !== id)
    try {
      await sessionApi.deleteSession(id)
      if (currentId.value === id) {
        currentId.value = sessions.value[0]?.id || ''
        messages.value = []
        if (currentId.value) await selectSession(currentId.value)
      }
    } catch (err) {
      sessions.value = snapshot
      throw err
    }
  }

  function appendUser(text: string) {
    messages.value.push({
      id: Date.now(),
      seq: messages.value.length + 1,
      role: 'user',
      content: text,
      sources: null,
      intent: null,
      rewrite_type: null,
      route: null,
      cached: false,
      latency_ms: null,
      ttft_ms: null,
      token_usage: null,
      error: null,
      created_at: null,
    })
  }

  function handleEvent(evt: SSEEvent) {
    if (!draft.value) draft.value = emptyDraft()
    const d = draft.value
    switch (evt.event) {
      case 'meta':
        d.meta = evt.data
        d.stage = 'intent'
        break
      case 'stage':
        d.stage = evt.data.stage
        break
      case 'sources':
        d.sources = evt.data.sources
        break
      case 'delta':
        d.content += evt.data.text
        break
      case 'done':
        d.ttft_ms = evt.data.ttft_ms
        d.latency_ms = evt.data.latency_ms
        break
      case 'error':
        d.error = evt.data.message
        break
    }
  }

  /** 发送一轮问答：立即插入用户气泡 + 流式草稿，结束转正式消息。 */
  async function send(query: string, kb_override?: string[] | null) {
    const text = query.trim()
    if (!text || sse.streaming.value) return
    lastQuery.value = text

    if (!currentId.value) {
      await createSession()
    }
    appendUser(text)
    draft.value = emptyDraft()

    const history = messages.value
      .filter((m) => m.role !== 'system')
      .slice(0, -1)
      .map((m) => ({role: m.role, content: m.content}))

    try {
      await sse.start(
        {
          query: text,
          history,
          kb_ids: kb_override ?? (kbIds.value.length ? kbIds.value : null),
          session_id: currentId.value || null,
        },
        handleEvent,
      )
    } catch (err) {
      if (sse.aborted.value) {
        if (draft.value) draft.value.error = draft.value.error || '已中断（已生成内容已保存）'
      } else {
        if (draft.value) draft.value.error = errorText(err)
      }
    } finally {
      const d = draft.value
      if (d && (d.content || d.error)) {
        messages.value.push({
          id: Date.now() + 1,
          seq: messages.value.length + 1,
          role: 'assistant',
          content: d.content,
          sources: d.sources,
          intent: d.meta?.intent ?? null,
          rewrite_type: d.meta?.rewrite_type ?? null,
          route: d.meta?.route ?? null,
          cached: d.meta?.cached ?? false,
          latency_ms: d.latency_ms || null,
          ttft_ms: d.ttft_ms || null,
          token_usage: null,
          error: d.error,
          created_at: null,
        })
      }
      draft.value = null
      // 会话的 updated_at / message_count 变了，静默刷新列表顺序
      sessionApi.listSessions().then((list) => {
        sessions.value = list
      }).catch(() => void 0)
    }
  }

  function stop() {
    sse.abort()
  }

  return {
    sessions,
    currentId,
    currentSession,
    kbIds,
    messages,
    draft,
    loadingSessions,
    loadingMessages,
    streaming,
    lastQuery,
    loadSessions,
    createSession,
    selectSession,
    renameSession,
    updateParams,
    removeSession,
    send,
    stop,
  }
})
