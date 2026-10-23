/** SSE 底座：把 fetch 流包装成"可中断 + 有状态"的启动器。
 *
 * 事件由调用方（chat store）消费，这里只负责生命周期与中断，
 * 避免把业务状态塞进通用组合式函数。
 */
import {ref} from 'vue'

import {streamChat} from '@/api/chat'
import type {SSEEvent, StreamPayload} from '@/api/chat'

export function useSSE() {
  const streaming = ref(false)
  const aborted = ref(false)
  let controller: AbortController | null = null

  async function start(payload: StreamPayload, onEvent: (evt: SSEEvent) => void): Promise<void> {
    controller = new AbortController()
    aborted.value = false
    streaming.value = true
    try {
      await streamChat(payload, onEvent, controller.signal)
    } finally {
      streaming.value = false
      controller = null
    }
  }

  /** 主动中断：已生成内容由后端照常落库，前端保留已有文本并标记中断。 */
  function abort() {
    aborted.value = true
    controller?.abort()
  }

  return {streaming, aborted, start, abort}
}
