<template>
  <AppShell title="对话">
    <div class="chat">
      <!-- ---------- 左：会话列表（可折叠） ---------- -->
      <aside class="sessions" :class="{'sessions--hidden': !showSessions}">
        <div class="sessions__head">
          <button class="btn btn--sm btn--block" type="button" @click="onNew">＋ 新建对话</button>
        </div>

        <div class="sessions__list" v-if="chat.sessions.length">
          <div
            v-for="s in chat.sessions"
            :key="s.id"
            class="session"
            :class="{'session--active': s.id === chat.currentId}"
            @click="onSelect(s.id)"
          >
            <template v-if="renamingId === s.id">
              <input
                ref="renameInput"
                v-model="renameText"
                class="session__input"
                @click.stop
                @keydown.enter.prevent="commitRename(s.id)"
                @keydown.esc.prevent="cancelRename"
                @blur="commitRename(s.id)"
              />
            </template>
            <template v-else>
              <div class="session__body">
                <span class="session__title ellipsis">{{ s.title }}</span>
                <span class="session__meta faint">{{ s.message_count }} 条 · {{ shortTime(s.updated_at) }}</span>
              </div>
              <div class="session__ops" @click.stop>
                <button class="session__op" title="重命名" type="button" @click="startRename(s)">✎</button>
                <button class="session__op session__op--danger" title="删除" type="button" @click="onDelete(s)">🗑</button>
              </div>
            </template>
          </div>
        </div>
        <EmptyState
          v-else-if="!chat.loadingSessions"
          title="还没有对话"
          description="点击「新建对话」，问一个劳动法问题试试"
        />
        <div v-else class="sessions__loading"><LoadingDots/></div>
      </aside>

      <!-- ---------- 中：消息流 ---------- -->
      <section class="thread">
        <div class="thread__toolbar">
          <button class="icon-btn" type="button" :title="showSessions ? '收起会话列表' : '展开会话列表'" @click="showSessions = !showSessions">
            ☰
          </button>
          <div class="kb-chips">
            <button
              v-for="kb in kbStore.kbs"
              :key="kb.id"
              class="chip"
              :class="{'chip--on': selectedKbIds.includes(kb.id)}"
              type="button"
              :disabled="!chat.currentId"
              @click="toggleKb(kb.id)"
            >
              {{ kb.name }}
            </button>
            <span v-if="!selectedKbIds.length" class="faint chip-hint">未选择知识库 → 将检索全部</span>
          </div>
          <button class="icon-btn" type="button" :title="showParams ? '收起参数' : '展开参数'" @click="showParams = !showParams">
            ⚙
          </button>
        </div>

        <div ref="scrollEl" class="thread__scroll">
          <div class="thread__inner">
            <EmptyState
              v-if="!chat.messages.length && !chat.draft"
              title="问一个具体的法律问题"
              description="例如「试用期最长可以约定多久？」「公司裁员，经济补偿怎么算，试用期算在内吗」"
            >
              <div class="suggests">
                <button v-for="q in suggestions" :key="q" class="suggest" type="button" @click="quickAsk(q)">
                  {{ q }}
                </button>
              </div>
            </EmptyState>

            <ChatBubble
              v-for="m in chat.messages"
              :key="m.id"
              :role="m.role"
              :content="m.content"
              :sources="m.sources"
              :route="m.route"
              :cached="m.cached"
              :ttft="m.ttft_ms"
              :latency="m.latency_ms"
              :error="m.error"
            />

            <!-- 流式草稿：状态条 + 逐 token 追加 -->
            <div v-if="chat.draft" class="draft">
              <div v-if="!chat.draft.content" class="draft__status">
                <LoadingDots/>
                <span>{{ stageText }}</span>
              </div>
              <ChatBubble
                v-if="chat.draft.content"
                role="assistant"
                :content="chat.draft.content"
                :sources="chat.draft.sources"
                :route="chat.draft.meta?.route"
                :cached="chat.draft.meta?.cached"
                :degraded="chat.draft.meta?.degraded"
                :streaming="chat.streaming"
                :error="chat.draft.error"
                @retry="retry"
              />
            </div>
          </div>
        </div>

        <div class="composer">
          <div class="composer__box" :class="{'composer__box--busy': chat.streaming}">
            <textarea
              ref="inputEl"
              v-model="input"
              class="composer__input"
              rows="1"
              placeholder="描述你的问题，Enter 发送 / Shift+Enter 换行"
              :disabled="chat.streaming"
              @keydown.enter.exact.prevent="onSend"
              @input="autoGrow"
            />
            <div class="composer__actions">
              <span class="faint composer__count">{{ input.length }}/2000</span>
              <button v-if="chat.streaming" class="btn btn--ghost btn--sm" type="button" @click="chat.stop()">
                停止生成
              </button>
              <button v-else class="btn btn--sm" type="button" :disabled="!input.trim()" @click="onSend">
                发送
              </button>
            </div>
          </div>
          <p class="composer__tip faint">
            回答基于所选知识库的法条与高频问答，仅供参考；涉及争议请以裁判机关认定为准。
          </p>
        </div>
      </section>

      <!-- ---------- 右：参数抽屉（默认收起） ---------- -->
      <aside v-if="showParams" class="params-drawer fade-up">
        <ParamPanel
          v-if="chat.currentSession"
          :model="chat.currentSession.model"
          :temperature="chat.currentSession.temperature"
          :top-p="chat.currentSession.top_p"
          :max-tokens="chat.currentSession.max_tokens"
          :history-rounds="chat.currentSession.history_rounds"
          @change="onParamChange"
        />
        <p v-else class="faint">先新建对话后可调整参数</p>
      </aside>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import {computed, nextTick, onMounted, ref, watch} from 'vue'
import {ElMessageBox} from 'element-plus'

import AppShell from '@/components/AppShell.vue'
import ChatBubble from '@/components/ChatBubble.vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingDots from '@/components/LoadingDots.vue'
import ParamPanel from '@/components/ParamPanel.vue'
import {errorText} from '@/api/http'
import type {Session, SessionParams} from '@/api/sessions'
import {useChatStore} from '@/stores/chat'
import {useKbStore} from '@/stores/kb'
import {useToast} from '@/composables/useToast'

const chat = useChatStore()
const kbStore = useKbStore()
const toast = useToast()

const input = ref('')
const showSessions = ref(true)
const showParams = ref(false)
const scrollEl = ref<HTMLElement | null>(null)
const inputEl = ref<HTMLTextAreaElement | null>(null)
const renameInput = ref<HTMLInputElement | HTMLInputElement[] | null>(null)
const renamingId = ref('')
const renameText = ref('')

const suggestions = [
  '试用期最长可以约定多久？',
  '公司裁员，经济补偿怎么算，试用期算在内吗',
  '劳动合同法第八十二条原文怎么规定',
]

const stageText = computed(() => {
  const map: Record<string, string> = {
    intent: '正在识别意图…',
    retrieval: '正在检索法条…',
    rerank: '正在重排序…',
    generate: '正在生成回答…',
  }
  return map[chat.draft?.stage || 'intent'] || '正在处理…'
})

const selectedKbIds = computed(() => chat.currentSession?.kb_ids || [])

/* ---------------- 会话操作 ---------------- */
async function onNew() {
  try {
    await chat.createSession({kb_ids: kbStore.kbs.map((k) => k.id)})
    await nextTick()
    inputEl.value?.focus()
  } catch (err) {
    toast.error(errorText(err))
  }
}

async function onSelect(id: string) {
  if (id === chat.currentId) return
  try {
    await chat.selectSession(id)
  } catch (err) {
    toast.error(errorText(err))
  }
}

function startRename(s: Session) {
  renamingId.value = s.id
  renameText.value = s.title
  nextTick(() => {
    const el = Array.isArray(renameInput.value) ? renameInput.value[0] : renameInput.value
    el?.focus()
    el?.select()
  })
}

function cancelRename() {
  renamingId.value = ''
  renameText.value = ''
}

async function commitRename(id: string) {
  if (renamingId.value !== id) return
  const title = renameText.value.trim()
  cancelRename()
  if (!title) return
  try {
    await chat.renameSession(id, title)
  } catch (err) {
    toast.error(errorText(err))
  }
}

async function onDelete(s: Session) {
  try {
    await ElMessageBox.confirm(`删除后不可恢复，确定删除「${s.title}」？`, '删除会话', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await chat.removeSession(s.id)
    toast.success('已删除')
  } catch (err) {
    toast.error(errorText(err))
  }
}

/* ---------------- 知识库与参数 ---------------- */
async function toggleKb(id: string) {
  if (!chat.currentId) return
  const next = selectedKbIds.value.includes(id)
    ? selectedKbIds.value.filter((x) => x !== id)
    : [...selectedKbIds.value, id]
  try {
    await chat.updateParams(chat.currentId, {kb_ids: next})
  } catch (err) {
    toast.error(errorText(err))
  }
}

async function onParamChange(patch: SessionParams) {
  if (!chat.currentId) return
  try {
    await chat.updateParams(chat.currentId, patch)
  } catch (err) {
    toast.error(errorText(err))
  }
}

/* ---------------- 发送 ---------------- */
function autoGrow() {
  const el = inputEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`
}

async function onSend() {
  const text = input.value.trim()
  if (!text || chat.streaming) return
  if (text.length > 2000) {
    toast.warn('问题过长（上限 2000 字）')
    return
  }
  input.value = ''
  await nextTick()
  autoGrow()
  try {
    await chat.send(text)
  } catch (err) {
    toast.error(errorText(err))
  }
}

function quickAsk(q: string) {
  input.value = q
  onSend()
}

function retry() {
  const last = chat.messages[chat.messages.length - 1]
  if (last && last.role === 'assistant' && last.error) chat.messages.pop()
  if (chat.lastQuery) chat.send(chat.lastQuery)
}

/* ---------------- 滚动 ---------------- */
function scrollToBottom() {
  const el = scrollEl.value
  if (el) el.scrollTop = el.scrollHeight
}

watch(
  () => [chat.messages.length, chat.draft?.content, chat.draft?.stage],
  () => nextTick(scrollToBottom),
)

function shortTime(value: string | null): string {
  if (!value) return ''
  return value.slice(5, 16)
}

onMounted(async () => {
  await Promise.all([kbStore.loadKbs(), chat.loadSessions()])
  await nextTick()
  scrollToBottom()
})
</script>

<style scoped>
.chat { display: flex; flex: 1; min-height: 0; }

/* ---------- 会话列表 ---------- */
.sessions {
  width: 232px; flex: 0 0 232px;
  border-right: 1px solid var(--c-border);
  background: #fff;
  display: flex; flex-direction: column;
  transition: width var(--dur) var(--ease), flex-basis var(--dur) var(--ease), opacity var(--dur) var(--ease);
}
.sessions--hidden { width: 0; flex-basis: 0; opacity: 0; overflow: hidden; border-right: none; }
.sessions__head { padding: 12px; }
.sessions__list { flex: 1; overflow-y: auto; padding: 0 8px 12px; display: flex; flex-direction: column; gap: 4px; }
.sessions__loading { display: grid; place-items: center; padding: 24px; color: var(--c-faint); }

.session {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 10px; border-radius: 10px; cursor: pointer;
  transition: background var(--dur) var(--ease);
}
.session:hover { background: var(--c-bg); }
.session--active { background: var(--c-primary-soft); }
.session__body { display: flex; flex-direction: column; min-width: 0; flex: 1; }
.session__title { font-size: 13.5px; font-weight: 500; }
.session--active .session__title { color: var(--c-primary); font-weight: 600; }
.session__meta { font-size: 11px; }
.session__ops { display: none; gap: 2px; }
.session:hover .session__ops { display: flex; }
.session__op {
  width: 24px; height: 24px; border: none; border-radius: 6px;
  background: transparent; cursor: pointer; font-size: 12px; opacity: .7;
}
.session__op:hover { background: #fff; opacity: 1; }
.session__op--danger:hover { color: var(--c-danger); }
.session__input {
  width: 100%; height: 28px; padding: 0 8px; font-size: 13px;
  border: 1px solid var(--c-primary); border-radius: 8px; outline: none;
}

/* ---------- 中间消息流 ---------- */
.thread { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.thread__toolbar {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 16px; border-bottom: 1px solid var(--c-border);
  background: rgba(255, 255, 255, .7);
}
.icon-btn {
  width: 30px; height: 30px; border: 1px solid var(--c-border); border-radius: 8px;
  background: #fff; color: var(--c-muted); cursor: pointer; font-size: 13px;
  transition: all var(--dur) var(--ease);
}
.icon-btn:hover { border-color: var(--c-primary); color: var(--c-primary); }
.kb-chips { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; flex: 1; }
.chip {
  height: 26px; padding: 0 11px; border-radius: var(--r-chip);
  border: 1px solid var(--c-border-strong); background: #fff;
  color: var(--c-muted); font-size: 12.5px; cursor: pointer;
  transition: all var(--dur) var(--ease);
}
.chip:hover:not(:disabled) { border-color: var(--c-primary); color: var(--c-primary); }
.chip--on { background: var(--c-primary); border-color: var(--c-primary); color: #fff; }
.chip:disabled { opacity: .55; cursor: not-allowed; }
.chip-hint { font-size: 12px; }

.thread__scroll { flex: 1; overflow-y: auto; }
.thread__inner { max-width: 900px; margin: 0 auto; padding: 20px 20px 8px; }
.suggests { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; }
.suggest {
  padding: 8px 12px; border-radius: 10px;
  border: 1px solid var(--c-border); background: #fff;
  color: var(--c-text); font-size: 13px; cursor: pointer;
  transition: all var(--dur) var(--ease);
}
.suggest:hover { border-color: var(--c-primary); color: var(--c-primary); transform: translateY(-1px); }

.draft__status {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 8px 14px; margin-bottom: 14px; border-radius: 999px;
  background: #fff; border: 1px solid var(--c-border);
  color: var(--c-muted); font-size: 13px;
  box-shadow: var(--shadow-card);
  animation: fade-up 220ms var(--ease) both;
}

/* ---------- 输入区 ---------- */
.composer { padding: 10px 16px 14px; border-top: 1px solid var(--c-border); background: rgba(255, 255, 255, .82); backdrop-filter: blur(8px); }
.composer__box {
  max-width: 900px; margin: 0 auto;
  display: flex; align-items: flex-end; gap: 10px;
  padding: 8px 10px 8px 14px; border-radius: 14px;
  background: #fff; border: 1px solid var(--c-border-strong);
  box-shadow: var(--shadow-card);
  transition: border-color var(--dur) var(--ease), box-shadow var(--dur) var(--ease);
}
.composer__box:focus-within { border-color: var(--c-primary); box-shadow: 0 0 0 3px var(--c-primary-soft); }
.composer__box--busy { border-color: var(--c-accent); }
.composer__input {
  flex: 1; border: none; outline: none; resize: none;
  font-family: inherit; font-size: 14px; line-height: 1.7;
  max-height: 160px; padding: 6px 0; background: transparent; color: var(--c-text);
}
.composer__actions { display: flex; align-items: center; gap: 8px; }
.composer__count { font-size: 11.5px; }
.composer__tip { max-width: 900px; margin: 8px auto 0; font-size: 11.5px; text-align: center; }

/* ---------- 参数抽屉 ---------- */
.params-drawer {
  width: 260px; flex: 0 0 260px;
  border-left: 1px solid var(--c-border); background: #fff;
  padding: 16px; overflow-y: auto;
}

@media (max-width: 1100px) {
  .sessions { width: 200px; flex-basis: 200px; }
  .params-drawer { width: 230px; flex-basis: 230px; }
}
@media (max-width: 900px) {
  .sessions { position: absolute; z-index: 12; height: 100%; }
  .params-drawer { position: absolute; right: 0; z-index: 12; height: 100%; }
}
</style>
