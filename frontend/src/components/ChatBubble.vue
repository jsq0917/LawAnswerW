<template>
  <div class="bubble" :class="`bubble--${role}`">
    <div v-if="role === 'assistant'" class="bubble__avatar">律</div>
    <div class="bubble__body">
      <!-- 助手回答：Markdown + 引用高亮；用户消息保留换行纯文本 -->
      <StreamMarkdown v-if="role === 'assistant'" :content="content" :streaming="streaming"/>
      <p v-else class="bubble__plain">{{ content }}</p>

      <div v-if="showMeta" class="bubble__meta row">
        <span v-if="route" class="pill pill--info">{{ routeLabel }}</span>
        <span v-if="cached" class="pill pill--ok">缓存命中</span>
        <span v-if="degraded" class="pill pill--warn">{{ degradedText }}</span>
        <span v-if="ttft" class="faint">首字 {{ ttft }}ms</span>
        <span v-if="latency" class="faint">总 {{ latency }}ms</span>
      </div>

      <div v-if="error" class="bubble__error">
        <span>{{ error }}</span>
        <button v-if="retriable" class="btn btn--ghost btn--sm" type="button" @click="$emit('retry')">
          重试
        </button>
      </div>

      <SourceList v-if="sources && sources.length" :sources="sources"/>
    </div>
  </div>
</template>

<script setup lang="ts">
import {computed} from 'vue'

import StreamMarkdown from './StreamMarkdown.vue'
import SourceList from './SourceList.vue'
import type {SourceRef} from '@/api/sessions'

const props = defineProps<{
  role: 'user' | 'assistant' | 'system'
  content: string
  sources?: SourceRef[] | null
  route?: string | null
  cached?: boolean
  degraded?: boolean | string
  ttft?: number | null
  latency?: number | null
  error?: string | null
  streaming?: boolean
}>()

defineEmits<{(e: 'retry'): void}>()

const ROUTE_LABEL: Record<string, string> = {
  pure_structured: '高频问答命中',
  pure_vector: '法条检索',
  hybrid: '问答 + 法条',
  cross_law: '跨法检索',
  greeting: '问候',
  no_data: '无相关资料',
}
const routeLabel = computed(() => ROUTE_LABEL[props.route || ''] || props.route || '')
const degradedText = computed(() =>
  props.degraded === 'vector_unavailable'
    ? '向量库暂不可用，本次仅使用高频问答'
    : '意图识别降级（规则兜底）',
)
const showMeta = computed(() => props.role === 'assistant' && (!!props.route || !!props.cached || !!props.degraded))
const retriable = computed(() => !!props.error)
</script>

<style scoped>
.bubble { display: flex; gap: 10px; padding: 6px 0 18px; }
.bubble--user { flex-direction: row-reverse; }
.bubble__avatar {
  display: grid; place-items: center;
  width: 30px; height: 30px; border-radius: 9px; flex: 0 0 30px;
  background: var(--c-primary); color: #fff; font-size: 13px; font-weight: 700;
}
.bubble__body { max-width: min(760px, 88%); }
.bubble--user .bubble__body { max-width: min(640px, 82%); }
.bubble--assistant .bubble__body {
  background: var(--c-card);
  border: 1px solid var(--c-border);
  border-radius: 4px 14px 14px 14px;
  padding: 12px 16px;
  box-shadow: var(--shadow-card);
}
.bubble--user .bubble__body {
  background: var(--c-primary);
  color: #fff;
  border-radius: 14px 4px 14px 14px;
  padding: 10px 14px;
}
.bubble__plain { margin: 0; white-space: pre-wrap; word-break: break-word; }
.bubble__meta { flex-wrap: wrap; gap: 8px; margin-top: 10px; font-size: 12px; }
.bubble__error {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin-top: 10px; padding: 8px 12px; border-radius: 10px;
  background: var(--c-danger-soft); color: #C7443A; font-size: 13px;
}
</style>
