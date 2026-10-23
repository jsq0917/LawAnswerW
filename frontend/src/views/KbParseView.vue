<template>
  <AppShell :title="`解析文档 · ${kbStore.kbName(kbId)}`">
    <div class="parse">
      <div class="parse__head row row--between">
        <div class="row" style="gap: 10px">
          <button class="icon-btn" type="button" title="返回知识库" @click="router.push({name: 'kb'})">←</button>
          <div class="col">
            <strong>上传并解析</strong>
            <span class="faint" style="font-size: 12px">{{ kbId }}</span>
          </div>
        </div>
        <button class="btn btn--ghost btn--sm" type="button" :disabled="!readyDocs.length" @click="goPreview">
          去预览
        </button>
      </div>

      <div class="parse__grid">
        <!-- 左：上传 -->
        <section class="card parse__panel">
          <h3 class="parse__title">1. 选择文件</h3>
          <UploadDropzone v-model="files" :disabled="uploading"/>

          <h3 class="parse__title" style="margin-top: 20px">2. 解析参数</h3>
          <div class="field">
            <label class="field__label">文本切块大小 <b class="mono">{{ chunkSize }}</b> 字</label>
            <el-slider v-model="chunkSize" :min="100" :max="1000" :step="50" :show-tooltip="false"/>
            <p class="field__hint">法条按"第X条"为不可切割边界，切块大小只影响长条文内部的款项拆分（默认 300）。</p>
          </div>
          <div class="field">
            <label class="field__label">重叠大小 <b class="mono">{{ overlap }}</b> 字</label>
            <el-slider v-model="overlap" :min="0" :max="200" :step="10" :show-tooltip="false"/>
            <p v-if="overlap >= chunkSize" class="field__error">重叠必须小于切块大小</p>
            <p v-else class="field__hint">相邻子块之间的重叠字数，避免跨块语义被切断（默认 50）。</p>
          </div>
          <div class="field">
            <label class="field__label">检索模式</label>
            <el-radio-group v-model="mode" size="small">
              <el-radio-button value="hybrid">混合检索（推荐）</el-radio-button>
              <el-radio-button value="dense">稠密向量</el-radio-button>
            </el-radio-group>
            <p class="field__hint">混合检索 = 稠密 + 稀疏双路召回，法条术语命中更稳。</p>
          </div>

          <button
            class="btn btn--block"
            type="button"
            :disabled="!files.length || uploading || overlap >= chunkSize"
            @click="startParse"
          >
            {{ uploading ? '提交中…' : `开始解析（${files.length} 个文件）` }}
          </button>
        </section>

        <!-- 右：进度 -->
        <section class="card parse__panel">
          <h3 class="parse__title">解析进度</h3>
          <p v-if="!tracked.length" class="faint" style="font-size: 13px">
            上传后这里会显示每个文件的处理进度（解析 → 清洗 → 分块 → 向量化）。
          </p>
          <ul v-else class="progress-list">
            <li v-for="item in tracked" :key="item.filename" class="progress-item">
              <div class="row row--between">
                <span class="ellipsis" :title="item.filename">{{ item.filename }}</span>
                <StatusPill :status="item.status"/>
              </div>
              <div class="progress-bar">
                <div class="progress-bar__fill" :class="fillClass(item.status)" :style="{width: `${item.percent}%`}"/>
              </div>
              <div class="row row--between" style="font-size: 12px">
                <span class="faint">{{ item.label }}</span>
                <span v-if="item.status === 'ready'" class="faint">
                  {{ item.chunks }} 块 · {{ item.articles }} 条
                </span>
                <span v-else-if="item.status === 'failed'" class="progress-item__error" :title="item.error">
                  失败：{{ item.error || '未知原因' }}
                </span>
              </div>
            </li>
          </ul>

          <div v-if="allDone" class="parse__done">
            <p>共 {{ totalChunks }} 块（{{ readyCount }} 个文件已就绪）</p>
            <button class="btn btn--sm" type="button" @click="goPreview">去预览</button>
          </div>
        </section>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import {computed, onBeforeUnmount, ref} from 'vue'
import {useRoute, useRouter} from 'vue-router'

import AppShell from '@/components/AppShell.vue'
import StatusPill from '@/components/StatusPill.vue'
import UploadDropzone from '@/components/UploadDropzone.vue'
import * as kbApi from '@/api/kb'
import type {DocumentItem} from '@/api/kb'
import {errorText} from '@/api/http'
import {useKbStore} from '@/stores/kb'
import {useToast} from '@/composables/useToast'

const route = useRoute()
const router = useRouter()
const kbStore = useKbStore()
const toast = useToast()

const kbId = String(route.params.kbId || '')
const files = ref<File[]>([])
const chunkSize = ref(300)
const overlap = ref(50)
const mode = ref<'dense' | 'hybrid'>('hybrid')
const uploading = ref(false)
const docs = ref<DocumentItem[]>([])

let stopPolling: (() => void) | null = null

const STAGE_LABEL: Record<string, string> = {
  pending: '排队中（解析 0%）',
  parsing: '解析中',
  ready: '完成',
  failed: '失败',
}
const STAGE_PERCENT: Record<string, number> = {pending: 5, parsing: 60, ready: 100, failed: 100}

interface TrackedFile {
  filename: string
  status: DocumentItem['status']
  percent: number
  label: string
  chunks: number
  articles: number
  error: string
}

/** 上传后按文件名把文档状态映射回文件列表（进度条数据源） */
const tracked = computed<TrackedFile[]>(() =>
  files.value.map((f) => {
    // 兼容历史数据：早期入库的文档名可能带存储前缀（<随机ID>_原名）
    const doc = docs.value.find((d) => d.filename === f.name || d.filename.endsWith(`_${f.name}`))
    if (!doc) {
      return {filename: f.name, status: 'pending', percent: 0, label: '等待提交', chunks: 0, articles: 0, error: ''}
    }
    const base = STAGE_PERCENT[doc.status] ?? 0
    const percent = doc.status === 'parsing' ? Math.max(base, Math.min(95, doc.progress || base)) : base
    return {
      filename: f.name,
      status: doc.status,
      percent,
      label: STAGE_LABEL[doc.status] || doc.status,
      chunks: doc.chunk_count,
      articles: doc.article_count,
      error: doc.error_msg || '',
    }
  }),
)

const readyDocs = computed(() => docs.value.filter((d) => d.status === 'ready'))
// 同样只看本批文件：库里已有文档不应算进"本次解析"的统计
const readyCount = computed(() => tracked.value.filter((t) => t.status === 'ready').length)
// 只统计"本次上传的文件"，不把库里已有文档算进来（§4.2 P4：完成后显示本批块数）
const totalChunks = computed(() => {
  const names = new Set(files.value.map((f) => f.name))
  return docs.value
    .filter((d) => d.status === 'ready' && (names.has(d.filename) || [...names].some((n) => d.filename.endsWith(`_${n}`))))
    .reduce((sum, d) => sum + d.chunk_count, 0)
})
const allDone = computed(
  () => tracked.value.length > 0 && tracked.value.every((t) => t.status === 'ready' || t.status === 'failed'),
)

function fillClass(status: string): string {
  if (status === 'ready') return 'progress-bar__fill--ok'
  if (status === 'failed') return 'progress-bar__fill--danger'
  return ''
}

async function startParse() {
  if (!files.value.length) return
  if (overlap.value >= chunkSize.value) {
    toast.error('重叠必须小于切块大小')
    return
  }
  uploading.value = true
  try {
    await kbApi.uploadDocs(kbId, files.value, {
      chunk_size: chunkSize.value,
      overlap: overlap.value,
      retrieval_mode: mode.value,
    })
    toast.success('已提交解析，进度实时刷新')
    stopPolling?.()
    stopPolling = kbStore.startPolling(kbId, (list) => {
      docs.value = list
      if (list.every((d) => d.status === 'ready' || d.status === 'failed')) {
        kbStore.loadKbs(true)
      }
    })
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    uploading.value = false
  }
}

function goPreview() {
  const doc = readyDocs.value[0] || docs.value[0]
  if (!doc) {
    toast.warn('还没有解析完成的文档')
    return
  }
  router.push({name: 'kb-preview', params: {kbId, docId: doc.id}})
}

onBeforeUnmount(() => stopPolling?.())
</script>

<style scoped>
.parse { padding: 20px; max-width: 1180px; width: 100%; margin: 0 auto; }
.parse__head { margin-bottom: 16px; }
.parse__grid { display: grid; grid-template-columns: 1.15fr .85fr; gap: 16px; align-items: start; }
.parse__panel { padding: 18px; }
.parse__title { font-size: 14px; margin-bottom: 10px; }
.icon-btn {
  width: 30px; height: 30px; border: 1px solid var(--c-border); border-radius: 8px;
  background: #fff; color: var(--c-muted); cursor: pointer;
}
.icon-btn:hover { border-color: var(--c-primary); color: var(--c-primary); }

.progress-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 14px; }
.progress-item { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
.progress-item__error { color: var(--c-danger); max-width: 60%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.progress-bar { height: 6px; border-radius: 999px; background: var(--c-bg); overflow: hidden; }
.progress-bar__fill {
  height: 100%; border-radius: 999px; background: var(--c-primary);
  transition: width 400ms var(--ease), background var(--dur) var(--ease);
}
.progress-bar__fill--ok { background: var(--c-accent); }
.progress-bar__fill--danger { background: var(--c-danger); }

.parse__done {
  margin-top: 16px; padding-top: 12px; border-top: 1px dashed var(--c-border);
  display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: 13px;
}
.parse__done p { margin: 0; }

@media (max-width: 980px) {
  .parse__grid { grid-template-columns: 1fr; }
}
</style>
