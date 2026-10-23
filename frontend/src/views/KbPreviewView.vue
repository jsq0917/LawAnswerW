<template>
  <AppShell :title="`块预览 · ${doc?.filename || ''}`">
    <div class="preview">
      <div class="preview__head row row--between">
        <div class="row" style="gap: 10px">
          <button class="icon-btn" type="button" title="返回知识库" @click="router.push({name: 'kb'})">←</button>
          <div class="col">
            <strong class="ellipsis">{{ doc?.filename || '文档' }}</strong>
            <span class="faint" style="font-size: 12px">
              {{ doc?.file_type }} · {{ doc?.chunk_count || 0 }} 块 · {{ doc?.article_count || 0 }} 条
            </span>
          </div>
        </div>
        <div class="row" style="gap: 8px">
          <StatusPill v-if="doc" :status="doc.status"/>
          <button class="btn btn--ghost btn--sm" type="button" @click="refreshAll">刷新</button>
        </div>
      </div>

      <div class="preview__grid">
        <!-- 左：块列表 -->
        <section class="card preview__list">
          <div class="preview__filters">
            <input
              v-model="keyword"
              class="input"
              placeholder="按条号 / 关键词过滤"
              @input="onSearch"
            />
            <el-radio-group v-model="typeFilter" size="small" @change="reload(0)">
              <el-radio-button value="">全部</el-radio-button>
              <el-radio-button value="parent">父块</el-radio-button>
              <el-radio-button value="child">子块</el-radio-button>
            </el-radio-group>
          </div>

          <div v-if="loadingChunks" class="preview__loading"><LoadingDots/></div>
          <ul v-else-if="chunks.length" class="chunk-list stagger">
            <li
              v-for="c in chunks"
              :key="c.id"
              class="chunk"
              :class="{'chunk--active': active?.id === c.id}"
              @click="active = c"
            >
              <div class="row row--between" style="gap: 6px">
                <span class="mono chunk__no">{{ c.article_no || `#${c.chunk_index}` }}</span>
                <span class="pill" :class="c.chunk_type === 'parent' ? 'pill--info' : ''">
                  {{ c.chunk_type === 'parent' ? '父' : '子' }}
                </span>
              </div>
              <p class="chunk__text">{{ c.content.slice(0, 40) }}…</p>
            </li>
          </ul>
          <EmptyState v-else title="没有匹配的块" description="换个关键词或筛选条件试试"/>

          <div class="preview__pager row row--between">
            <button class="btn btn--ghost btn--sm" type="button" :disabled="offset === 0" @click="reload(Math.max(0, offset - limit))">上一页</button>
            <span class="faint">{{ offset + 1 }}–{{ Math.min(offset + limit, total) }} / {{ total }}</span>
            <button
              class="btn btn--ghost btn--sm"
              type="button"
              :disabled="offset + limit >= total"
              @click="reload(offset + limit)"
            >
              下一页
            </button>
          </div>
        </section>

        <!-- 右：块详情 / 编辑 -->
        <section class="card preview__detail">
          <ChunkEditor
            v-if="active"
            :chunk="active"
            :saving="saving"
            @save="onSave"
            @remove="onRemove"
          />
          <EmptyState v-else title="选择一个块" description="左侧点击任意块查看完整内容与元数据，可编辑后重新向量化"/>
        </section>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import {onMounted, ref} from 'vue'
import {useRoute, useRouter} from 'vue-router'
import {ElMessageBox} from 'element-plus'

import AppShell from '@/components/AppShell.vue'
import ChunkEditor from '@/components/ChunkEditor.vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingDots from '@/components/LoadingDots.vue'
import StatusPill from '@/components/StatusPill.vue'
import * as kbApi from '@/api/kb'
import type {ChunkItem, DocumentDetail} from '@/api/kb'
import {errorText} from '@/api/http'
import {useKbStore} from '@/stores/kb'
import {useToast} from '@/composables/useToast'

const route = useRoute()
const router = useRouter()
const kbStore = useKbStore()
const toast = useToast()

const docId = String(route.params.docId || '')
const doc = ref<DocumentDetail | null>(null)
const chunks = ref<ChunkItem[]>([])
const active = ref<ChunkItem | null>(null)
const total = ref(0)
const limit = 50
const offset = ref(0)
const keyword = ref('')
const typeFilter = ref<'' | 'parent' | 'child'>('')
const loadingChunks = ref(false)
const saving = ref(false)

let searchTimer: number | undefined

async function loadDoc() {
  try {
    doc.value = await kbApi.getDoc(docId)
  } catch (err) {
    toast.error(errorText(err))
  }
}

async function reload(nextOffset = 0) {
  offset.value = nextOffset
  loadingChunks.value = true
  try {
    const page = await kbApi.listChunks(docId, {
      limit,
      offset: nextOffset,
      q: keyword.value || undefined,
      chunk_type: typeFilter.value || undefined,
    })
    chunks.value = page.items
    total.value = page.total
    if (active.value) {
      const match = page.items.find((c) => c.id === active.value?.id)
      if (!match) active.value = page.items[0] || null
      else active.value = match
    } else {
      active.value = page.items[0] || null
    }
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    loadingChunks.value = false
  }
}

function onSearch() {
  window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => reload(0), 300)
}

async function refreshAll() {
  await Promise.all([loadDoc(), reload(offset.value)])
}

async function onSave(chunk: ChunkItem, content: string) {
  saving.value = true
  try {
    await kbApi.patchChunk(chunk.id, content)
    toast.success('已保存，正在重新向量化…')
    await waitForVectorize()
    await reload(offset.value)
    toast.success('向量已同步')
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    saving.value = false
  }
}

/** 保存后文档状态会短暂回到 parsing（重新向量化），轮询到 ready 再刷新块 */
async function waitForVectorize() {
  for (let i = 0; i < 15; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 600))
    try {
      const detail = await kbApi.getDoc(docId)
      doc.value = detail
      if (detail.status === 'ready' || detail.status === 'failed') return
    } catch {
      return
    }
  }
}

async function onRemove(chunk: ChunkItem) {
  try {
    await ElMessageBox.confirm(
      '删除后该块不再参与检索（软删除，可从数据库恢复）。确定删除？',
      '删除块',
      {confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning'},
    )
  } catch {
    return
  }
  try {
    await kbApi.deleteChunk(chunk.id)
    chunks.value = chunks.value.filter((c) => c.id !== chunk.id)
    total.value = Math.max(0, total.value - 1)
    active.value = chunks.value[0] || null
    toast.success('已删除')
    kbStore.loadKbs(true)
  } catch (err) {
    toast.error(errorText(err))
  }
}

onMounted(async () => {
  await Promise.all([loadDoc(), reload(0)])
})
</script>

<style scoped>
.preview { padding: 20px; max-width: 1320px; width: 100%; margin: 0 auto; display: flex; flex-direction: column; min-height: 0; }
.preview__head { margin-bottom: 14px; }
.preview__grid { display: grid; grid-template-columns: 390px 1fr; gap: 14px; align-items: start; }
.preview__list { display: flex; flex-direction: column; max-height: calc(100vh - 170px); }
.preview__filters { padding: 12px; border-bottom: 1px solid var(--c-border); display: flex; flex-direction: column; gap: 10px; }
.preview__loading { display: grid; place-items: center; padding: 30px; color: var(--c-faint); }
.chunk-list { list-style: none; margin: 0; padding: 8px; overflow-y: auto; flex: 1; display: flex; flex-direction: column; gap: 4px; }
.chunk { padding: 9px 10px; border-radius: 10px; cursor: pointer; border: 1px solid transparent; transition: all var(--dur) var(--ease); }
.chunk:hover { background: var(--c-bg); }
.chunk--active { background: var(--c-primary-soft); border-color: #C9D3F5; }
.chunk__no { font-size: 12.5px; font-weight: 600; color: var(--c-primary); }
.chunk__text { margin: 4px 0 0; font-size: 12.5px; color: var(--c-muted); line-height: 1.6; }
.preview__pager { padding: 8px 12px; border-top: 1px solid var(--c-border); font-size: 12px; }
.preview__detail { padding: 16px; max-height: calc(100vh - 170px); overflow: auto; }
.icon-btn {
  width: 30px; height: 30px; border: 1px solid var(--c-border); border-radius: 8px;
  background: #fff; color: var(--c-muted); cursor: pointer;
}
.icon-btn:hover { border-color: var(--c-primary); color: var(--c-primary); }

@media (max-width: 1000px) {
  .preview__grid { grid-template-columns: 1fr; }
  .preview__list, .preview__detail { max-height: none; }
}
</style>
