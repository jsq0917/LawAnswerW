/** 知识库状态（P4-P6 共用）：列表 + 各库文档缓存。 */
import {ref} from 'vue'
import {defineStore} from 'pinia'

import * as kbApi from '@/api/kb'
import type {DocumentItem, KnowledgeBase} from '@/api/kb'

export const useKbStore = defineStore('kb', () => {
  const kbs = ref<KnowledgeBase[]>([])
  const loading = ref(false)
  const docsByKb = ref<Record<string, DocumentItem[]>>({})

  async function loadKbs(force = false) {
    if (kbs.value.length && !force) return kbs.value
    loading.value = true
    try {
      kbs.value = await kbApi.listKbs()
      return kbs.value
    } finally {
      loading.value = false
    }
  }

  function kbName(id: string): string {
    return kbs.value.find((k) => k.id === id)?.name || id
  }

  async function loadDocs(kbId: string) {
    const docs = await kbApi.listDocs(kbId)
    docsByKb.value = {...docsByKb.value, [kbId]: docs}
    return docs
  }

  /** 轮询解析中的文档（P4 进度条：pending/parsing 时每 2s 刷新一次）。 */
  function startPolling(kbId: string, onChange?: (docs: DocumentItem[]) => void): () => void {
    let timer: number | undefined
    const tick = async () => {
      try {
        const docs = await loadDocs(kbId)
        onChange?.(docs)
        const busy = docs.some((d) => d.status === 'pending' || d.status === 'parsing')
        if (busy) {
          timer = window.setTimeout(tick, 2000)
        }
      } catch {
        /* 轮询失败静默，下一次再来 */
      }
    }
    tick()
    return () => {
      if (timer) window.clearTimeout(timer)
    }
  }

  return {kbs, loading, docsByKb, loadKbs, loadDocs, kbName, startPolling}
})
