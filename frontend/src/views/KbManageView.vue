<template>
  <AppShell title="知识库">
    <div class="kb">
      <!-- ---------- 库列表 ---------- -->
      <template v-if="!selectedKb">
        <div class="kb__head">
          <p class="muted">每个知识库对应一组法条文档，可独立上传与检索。</p>
          <button class="btn btn--sm" type="button" @click="openCreate">＋ 新建知识库</button>
        </div>

        <div v-if="kbStore.kbs.length" class="kb__grid stagger">
          <article v-for="kb in kbStore.kbs" :key="kb.id" class="kb-card card" @click="enter(kb)">
            <div class="kb-card__icon">{{ kb.name.slice(0, 1) }}</div>
            <h3>{{ kb.name }}</h3>
            <p class="kb-card__desc">{{ kb.description || '暂无描述' }}</p>
            <div class="kb-card__stats">
              <span><b>{{ kb.doc_count }}</b> 文档</span>
              <span><b>{{ kb.chunk_count }}</b> 块</span>
            </div>
            <div class="kb-card__foot">
              <span class="faint">{{ kb.id }}</span>
              <span class="kb-card__foot-right">
                <span class="faint">{{ kb.updated_at ? kb.updated_at.slice(0, 16) : '' }}</span>
                <button class="link-btn link-btn--danger" type="button" @click.stop="confirmRemoveKb(kb)">删除</button>
              </span>
            </div>
          </article>
        </div>
        <EmptyState v-else-if="!kbStore.loading" title="还没有知识库" description="新建一个知识库，然后上传法条文档开始解析">
          <button class="btn btn--sm" type="button" @click="openCreate">新建知识库</button>
        </EmptyState>
        <div v-else class="kb__loading"><LoadingDots/></div>
      </template>

      <!-- ---------- 文档列表 ---------- -->
      <template v-else>
        <div class="kb__head">
          <div class="row" style="gap: 10px">
            <button class="icon-btn" type="button" title="返回知识库列表" @click="back">←</button>
            <div class="col">
              <strong>{{ selectedKb.name }}</strong>
              <span class="faint" style="font-size: 12px">{{ selectedKb.id }} · {{ selectedKb.doc_count }} 文档 / {{ selectedKb.chunk_count }} 块</span>
            </div>
          </div>
          <div class="row" style="gap: 8px">
            <button class="btn btn--ghost btn--sm" type="button" @click="refreshDocs">刷新</button>
            <button class="btn btn--sm" type="button" @click="goParse">上传文档</button>
            <button class="btn btn--ghost btn--sm kb__danger" type="button" @click="confirmRemoveKb(selectedKb)">删除知识库</button>
          </div>
        </div>

        <div v-if="docs.length" class="doc-table card">
          <div class="doc-table__head">
            <span>文件名</span><span>类型</span><span>大小</span><span>块数</span><span>状态</span><span>上传时间</span><span>操作</span>
          </div>
          <div
            v-for="d in docs"
            :key="d.id"
            class="doc-table__row"
            @click="openDoc(d)"
          >
            <span class="ellipsis" :title="d.filename">{{ d.filename }}</span>
            <span class="faint">{{ d.file_type }}</span>
            <span class="faint">{{ sizeText(d.file_size) }}</span>
            <span class="mono">{{ d.chunk_count }}</span>
            <span><StatusPill :status="d.status"/></span>
            <span class="faint">{{ d.created_at ? d.created_at.slice(5, 16) : '—' }}</span>
            <span class="doc-table__ops" @click.stop>
              <button class="link-btn" type="button" @click="reingest(d)">重新解析</button>
              <button class="link-btn link-btn--danger" type="button" @click="removeDoc(d)">删除</button>
            </span>
          </div>
        </div>
        <EmptyState
          v-else-if="!loadingDocs"
          title="该库还没有文档"
          description="上传 PDF / PPTX / Markdown / 图片，系统会自动解析、清洗、分块并向量化"
        >
          <button class="btn btn--sm" type="button" @click="goParse">上传文档</button>
        </EmptyState>
        <div v-else class="kb__loading"><LoadingDots/></div>
      </template>

      <!-- ---------- 新建知识库弹窗 ---------- -->
      <el-dialog v-model="createVisible" title="新建知识库" width="460px" :close-on-click-modal="false">
        <div class="field">
          <label class="field__label">名称</label>
          <input v-model.trim="createForm.name" class="input" maxlength="64" placeholder="例如：劳动法知识库" @input="syncSlug"/>
        </div>
        <div class="field">
          <label class="field__label">标识（slug）</label>
          <input v-model.trim="createForm.id" class="input mono" maxlength="32" placeholder="kb_xxx（小写字母/数字/下划线）"/>
          <p class="field__hint">作为 Milvus 分区键与 API 路径，创建后不建议修改。</p>
        </div>
        <div class="field">
          <label class="field__label">描述（可选）</label>
          <input v-model.trim="createForm.description" class="input" maxlength="255" placeholder="一句话说明这个库收录什么"/>
        </div>
        <template #footer>
          <button class="btn btn--ghost btn--sm" type="button" @click="createVisible = false">取消</button>
          <button class="btn btn--sm" type="button" :disabled="creating || !canCreate" @click="submitCreate">
            {{ creating ? '创建中…' : '创建' }}
          </button>
        </template>
      </el-dialog>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import {computed, onMounted, reactive, ref} from 'vue'
import {useRouter} from 'vue-router'
import {ElMessageBox} from 'element-plus'

import AppShell from '@/components/AppShell.vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingDots from '@/components/LoadingDots.vue'
import StatusPill from '@/components/StatusPill.vue'
import * as kbApi from '@/api/kb'
import type {DocumentItem, KnowledgeBase} from '@/api/kb'
import {errorText} from '@/api/http'
import {useKbStore} from '@/stores/kb'
import {useToast} from '@/composables/useToast'

const kbStore = useKbStore()
const toast = useToast()
const router = useRouter()

const selectedKb = ref<KnowledgeBase | null>(null)
const docs = ref<DocumentItem[]>([])
const loadingDocs = ref(false)

const createVisible = ref(false)
const creating = ref(false)
const createForm = reactive({name: '', id: '', description: ''})
const canCreate = computed(() => createForm.name.length > 0 && /^[a-z0-9_]{2,32}$/.test(createForm.id))

function enter(kb: KnowledgeBase) {
  selectedKb.value = kb
  refreshDocs()
}

function back() {
  selectedKb.value = null
  docs.value = []
  kbStore.loadKbs(true)
}

async function refreshDocs() {
  if (!selectedKb.value) return
  loadingDocs.value = true
  try {
    docs.value = await kbStore.loadDocs(selectedKb.value.id)
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    loadingDocs.value = false
  }
}

function openDoc(doc: DocumentItem) {
  if (!selectedKb.value) return
  router.push({name: 'kb-preview', params: {kbId: selectedKb.value.id, docId: doc.id}})
}

function goParse() {
  if (!selectedKb.value) return
  router.push({name: 'kb-parse', params: {kbId: selectedKb.value.id}})
}

async function reingest(doc: DocumentItem) {
  try {
    await kbApi.reingestDoc(doc.id)
    toast.success('已提交重新解析')
    await refreshDocs()
  } catch (err) {
    toast.error(errorText(err))
  }
}

async function removeDoc(doc: DocumentItem) {
  try {
    await ElMessageBox.confirm(`删除「${doc.filename}」会同时删除其 ${doc.chunk_count} 个块与向量，确定继续？`, '删除文档', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await kbApi.deleteDoc(doc.id)
    docs.value = docs.value.filter((x) => x.id !== doc.id)
    toast.success('已删除')
    kbStore.loadKbs(true)
  } catch (err) {
    toast.error(errorText(err))
  }
}

/* ---------- 删除知识库 ---------- */
/** 删库不可恢复：确认框里把"会一起删掉什么"写清楚，而不是只问"确定吗"。 */
async function confirmRemoveKb(kb: KnowledgeBase) {
  const scope = kb.doc_count || kb.chunk_count
    ? `将同时删除其 ${kb.doc_count} 个文档、${kb.chunk_count} 个块与全部向量，源文件也会清理`
    : '该库还没有文档，只删除空库本身'
  try {
    await ElMessageBox.confirm(`删除「${kb.name}」${scope}，且不可恢复。确定继续？`, '删除知识库', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    const result = await kbStore.removeKb(kb.id)
    if (selectedKb.value?.id === kb.id) {
      // 正停在这个库的文档列表里：退回列表，并把库详情一起清掉
      selectedKb.value = null
      docs.value = []
      await kbStore.loadKbs(true)
    }
    toast.success(`已删除「${result.name}」`)
  } catch (err) {
    toast.error(errorText(err))
  }
}

/* ---------- 新建 ---------- */
function openCreate() {
  createForm.name = ''
  createForm.id = ''
  createForm.description = ''
  createVisible.value = true
}

/** slug 建议：ASCII 名称直接转下划线；中文名给 kb_ + 时间后缀，用户可改 */
function syncSlug() {
  if (createForm.id) return
  const ascii = createForm.name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
  createForm.id = /^[a-z0-9_]{2,32}$/.test(ascii) ? ascii : `kb_${Date.now().toString(36)}`
}

async function submitCreate() {
  if (!canCreate.value) return
  creating.value = true
  try {
    await kbApi.createKb({id: createForm.id, name: createForm.name, description: createForm.description || undefined})
    toast.success('知识库已创建')
    createVisible.value = false
    await kbStore.loadKbs(true)
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    creating.value = false
  }
}

function sizeText(size: number): string {
  if (!size) return '—'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

onMounted(() => kbStore.loadKbs(true))
</script>

<style scoped>
.kb { padding: 20px; max-width: 1180px; width: 100%; margin: 0 auto; }
.kb__head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
.kb__head p { margin: 0; font-size: 13px; }
.kb__grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; }
.kb__loading { display: grid; place-items: center; padding: 40px; color: var(--c-faint); }

.kb-card { padding: 16px; cursor: pointer; transition: transform var(--dur) var(--ease), box-shadow var(--dur) var(--ease); }
.kb-card:hover { transform: translateY(-2px); box-shadow: var(--shadow-pop); }
.kb-card__icon {
  display: grid; place-items: center; width: 36px; height: 36px;
  border-radius: 11px; background: var(--c-primary-soft); color: var(--c-primary);
  font-weight: 700; margin-bottom: 10px;
}
.kb-card h3 { font-size: 15px; }
.kb-card__desc { margin: 4px 0 12px; font-size: 12.5px; color: var(--c-muted); min-height: 34px; }
.kb-card__stats { display: flex; gap: 14px; font-size: 12.5px; color: var(--c-muted); }
.kb-card__stats b { color: var(--c-text); font-size: 15px; }
.kb-card__foot { display: flex; justify-content: space-between; margin-top: 12px; padding-top: 10px; border-top: 1px dashed var(--c-border); font-size: 11.5px; }
.kb-card__foot-right { display: flex; align-items: center; gap: 10px; }
.kb__danger { color: var(--c-danger); }

.icon-btn {
  width: 30px; height: 30px; border: 1px solid var(--c-border); border-radius: 8px;
  background: #fff; color: var(--c-muted); cursor: pointer;
}
.icon-btn:hover { border-color: var(--c-primary); color: var(--c-primary); }

/* ---------- 文档表 ---------- */
.doc-table { overflow: hidden; }
.doc-table__head, .doc-table__row {
  display: grid;
  grid-template-columns: 2.2fr .8fr .8fr .7fr .9fr 1.1fr 1.2fr;
  gap: 8px; align-items: center;
  padding: 10px 14px; font-size: 13px;
}
.doc-table__head { background: var(--c-bg); color: var(--c-muted); font-size: 12px; font-weight: 600; border-bottom: 1px solid var(--c-border); }
.doc-table__row { border-bottom: 1px solid var(--c-border); cursor: pointer; transition: background var(--dur) var(--ease); }
.doc-table__row:last-child { border-bottom: none; }
.doc-table__row:hover { background: #FAFBFE; }
.doc-table__ops { display: flex; gap: 10px; }
.link-btn { border: none; background: transparent; color: var(--c-primary); font-size: 12.5px; cursor: pointer; padding: 0; }
.link-btn:hover { text-decoration: underline; }
.link-btn--danger { color: var(--c-danger); }

@media (max-width: 900px) {
  .doc-table__head { display: none; }
  .doc-table__row { grid-template-columns: 1fr auto; row-gap: 6px; }
  .doc-table__row > span:nth-child(2),
  .doc-table__row > span:nth-child(3),
  .doc-table__row > span:nth-child(4),
  .doc-table__row > span:nth-child(6) { display: none; }
}
</style>
