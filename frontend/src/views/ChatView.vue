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

      <!-- 窄屏浮层遮罩：点击关闭侧栏/参数栏 -->
      <div v-if="isNarrow && (showSessions || showParams)" class="chat__mask" @click="closeDrawers"/>

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

            <!-- 追问预测：只跟着最后一条回答出现。
                 主体点击 = 直接发送（低摩擦追问问答）；右侧 ✎ = 只填入输入框，
                 想改一改再发的人不必先发一条废问题。 -->
            <div v-if="followups.length" class="followups">
              <p class="followups__title faint">你可能还想问</p>
              <div class="followups__list">
                <div v-for="q in followups" :key="q" class="followup">
                  <button class="followup__ask" type="button" :disabled="chat.streaming" @click="askFollowup(q)">
                    {{ q }}
                  </button>
                  <button
                    class="followup__fill"
                    type="button"
                    title="填入输入框（可编辑后再发送）"
                    @click="fillFollowup(q)"
                  >
                    ✎
                  </button>
                </div>
              </div>
            </div>

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
import {computed, nextTick, onBeforeUnmount, onMounted, ref, watch} from 'vue'
import {ElMessageBox} from 'element-plus'

import AppShell from '@/components/AppShell.vue'
import ChatBubble from '@/components/ChatBubble.vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingDots from '@/components/LoadingDots.vue'
import ParamPanel from '@/components/ParamPanel.vue'
import {errorText} from '@/api/http'
import type {Session, SessionParams} from '@/api/sessions'
import {useChatStore} from '@/stores/chat'
import type {UiMessage} from '@/stores/chat'
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

/* ---------------- 窄屏抽屉 ---------------- */
// 与样式里的断点保持一致（780px）。宽屏保持侧栏常驻，窄屏改为浮层抽屉并默认收起，
// 否则浮层会盖住正文左侧，导致消息内容被裁切。
const NARROW_QUERY = '(max-width: 780px)'
const isNarrow = ref(false)
let narrowMql: MediaQueryList | null = null

function applyNarrow(matches: boolean) {
  isNarrow.value = matches
  showSessions.value = !matches
  if (matches) showParams.value = false
}

function onNarrowChange(e: MediaQueryListEvent) {
  applyNarrow(e.matches)
}

function closeDrawers() {
  showSessions.value = false
  showParams.value = false
}

// 点击侧栏里的会话后，窄屏下应自动收起抽屉，把正文让出来
function closeSessionsIfNarrow() {
  if (isNarrow.value) showSessions.value = false
}

const suggestions = [
  '试用期最长可以约定多久？',
  '公司裁员，经济补偿怎么算，试用期算在内吗',
  '劳动合同法第八十二条原文怎么规定',
]

/* ---------------- 追问预测 ---------------- */
// 只认「最后一条消息就是 assistant」的情况：正在等回答时（最后一条是 user）
// 或还在流式中，都不应该显示上一轮的推荐，否则会出现"问 A 却推荐上次的追问"。
const lastAnswer = computed<UiMessage | null>(() => {
  const list = chat.messages
  const last = list[list.length - 1]
  return last && last.role === 'assistant' ? last : null
})

const followups = computed<string[]>(() => {
  if (chat.streaming || chat.draft) return []
  return lastAnswer.value?.suggestions ?? []
})

/** 点击推荐项：直接当成一条新问题发出。 */
async function askFollowup(q: string) {
  if (chat.streaming) return
  try {
    await chat.send(q, liveKbIds.value)
  } catch (err) {
    toast.error(errorText(err))
  }
}

/** 点击 ✎：只填入输入框并聚焦，用户改完自己发。 */
async function fillFollowup(q: string) {
  input.value = q
  await nextTick()
  autoGrow()
  inputEl.value?.focus()
}

const stageText = computed(() => {
  const map: Record<string, string> = {
    intent: '正在识别意图…',
    retrieval: '正在检索法条…',
    rerank: '正在重排序…',
    generate: '正在生成回答…',
  }
  return map[chat.draft?.stage || 'intent'] || '正在处理…'
})

/** 会话里存的是 kb id 快照（JSON 数组）。库被删掉后快照会指向不存在的库，
 *  按它检索只会得到空结果，所以展示与发送都只认"当前还存在的库"。 */
const liveKbIds = computed(() =>
  (chat.currentSession?.kb_ids || []).filter((id) => kbStore.kbs.some((k) => k.id === id)),
)

const selectedKbIds = computed(() => liveKbIds.value)

/* ---------------- 会话操作 ---------------- */
async function onNew() {
  try {
    await chat.createSession({kb_ids: kbStore.kbs.map((k) => k.id)})
    closeSessionsIfNarrow()
    await nextTick()
    inputEl.value?.focus()
  } catch (err) {
    toast.error(errorText(err))
  }
}

async function onSelect(id: string) {
  if (id === chat.currentId) {
    closeSessionsIfNarrow()
    return
  }
  try {
    await chat.selectSession(id)
    closeSessionsIfNarrow()
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
    await chat.send(text, liveKbIds.value)
  } catch (err) {
    toast.error(errorText(err))
  }
}

function quickAsk(q: string) {
  input.value = q
  onSend()
}

function retry() {
  if (chat.streaming) return
  chat.retryLast(liveKbIds.value).catch((err) => toast.error(errorText(err)))
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

// 推荐区是回答落屏后才异步回来的：只有用户本来就停在底部时才跟着滚，
// 免得他正在翻历史却被强行拉回底部。
watch(
  () => followups.value.length,
  () => {
    const el = scrollEl.value
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 160
    if (nearBottom) nextTick(scrollToBottom)
  },
)

function shortTime(value: string | null): string {
  if (!value) return ''
  return value.slice(5, 16)
}

onMounted(async () => {
  narrowMql = window.matchMedia(NARROW_QUERY)
  applyNarrow(narrowMql.matches)
  narrowMql.addEventListener('change', onNarrowChange)

  await Promise.all([kbStore.loadKbs(), chat.loadSessions()])
  await nextTick()
  scrollToBottom()
})

onBeforeUnmount(() => {
  narrowMql?.removeEventListener('change', onNarrowChange)
  narrowMql = null
})
</script>

<style scoped>
/* 定位参照：窄屏下两侧面板用绝对定位浮层，必须以此为包含块，
   否则会相对视口定位、脱离文档流盖住正文（曾导致小窗口下左侧内容被裁切）。
   高度锁定为「一屏」：左右两列才能各自成为独立滚动容器。
   注意高度链：只要中间有一层是 auto（内容撑高），.thread__scroll / .sessions__list
   的 overflow 就不会生效，滚动会退化成整页滚动，两列就被绑在一起同步移动。 */
.chat {
  position: relative;
  display: flex;
  /* 必须是 auto 基准的 flex：写 flex:1（= basis 0）会让 flex-basis 覆盖下面的 height，
     高度又退回内容撑高，两列滚动就失效了。 */
  flex: 1 1 auto;
  min-height: 0;
  height: calc(100vh - var(--h-topbar));
  height: calc(100dvh - var(--h-topbar)); /* 移动端浏览器地址栏收放时用动态视口更准 */
  overflow: hidden;
}
.chat__mask { display: none; }

/* ---------- 会话列表 ---------- */
.sessions {
  width: 232px; flex: 0 0 232px;
  border-right: 1px solid var(--c-border);
  background: #fff;
  display: flex; flex-direction: column;
  min-height: 0;
  transition: width var(--dur) var(--ease), flex-basis var(--dur) var(--ease), opacity var(--dur) var(--ease);
}
.sessions--hidden { width: 0; flex-basis: 0; opacity: 0; overflow: hidden; border-right: none; }
.sessions__head { padding: 12px; flex: 0 0 auto; }
/* 左列独立滚动区域：flex:1 撑满剩余高度 + min-height:0 允许被压缩，
   overscroll-behavior 切断滚动传递，避免滑到底后把滚动接力给整页（从而带动另一列）。 */
.sessions__list {
  flex: 1; min-height: 0; overflow-y: auto;
  overscroll-behavior: contain;
  -webkit-overflow-scrolling: touch;
  padding: 0 8px 12px; display: flex; flex-direction: column; gap: 4px;
}
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
.thread { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.thread__toolbar {
  display: flex; align-items: center; gap: 10px; flex: 0 0 auto;
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

/* 右列独立滚动区域：只滚消息，工具栏和输入框固定不动 */
.thread__scroll {
  flex: 1; min-height: 0; overflow-y: auto;
  overscroll-behavior: contain;
  -webkit-overflow-scrolling: touch;
}
.thread__inner { max-width: 900px; margin: 0 auto; padding: 20px 20px 8px; }
.suggests { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; }
.suggest {
  padding: 8px 12px; border-radius: 10px;
  border: 1px solid var(--c-border); background: #fff;
  color: var(--c-text); font-size: 13px; cursor: pointer;
  transition: all var(--dur) var(--ease);
}
.suggest:hover { border-color: var(--c-primary); color: var(--c-primary); transform: translateY(-1px); }

/* ---------- 追问预测 ---------- */
/* 左缩进 40px = 气泡头像 30px + gap 10px，让推荐项与回答卡片左边缘对齐 */
.followups { margin: 2px 0 18px 40px; animation: fade-up 240ms var(--ease) both; }
.followups__title { font-size: 12px; margin: 0 0 8px 2px; }
.followups__list { display: flex; flex-direction: column; gap: 8px; }
.followup {
  display: flex; align-items: stretch;
  border: 1px solid var(--c-border); border-radius: 12px;
  background: #fff; overflow: hidden;
  transition: border-color var(--dur) var(--ease), box-shadow var(--dur) var(--ease);
}
.followup:hover { border-color: var(--c-primary); box-shadow: var(--shadow-card); }
.followup__ask {
  flex: 1; min-width: 0; text-align: left;
  padding: 9px 12px; border: none; background: transparent;
  color: var(--c-text); font-family: inherit; font-size: 13px; line-height: 1.55;
  cursor: pointer;
}
.followup__ask:hover:not(:disabled) { color: var(--c-primary); }
.followup__ask:disabled { cursor: not-allowed; opacity: .55; }
.followup__fill {
  flex: 0 0 auto; width: 42px;
  border: none; border-left: 1px solid var(--c-border);
  background: #fff; color: var(--c-muted); font-size: 13px; cursor: pointer;
  transition: background var(--dur) var(--ease), color var(--dur) var(--ease);
}
.followup__fill:hover { background: var(--c-primary-soft); color: var(--c-primary); }


.draft__status {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 8px 14px; margin-bottom: 14px; border-radius: 999px;
  background: #fff; border: 1px solid var(--c-border);
  color: var(--c-muted); font-size: 13px;
  box-shadow: var(--shadow-card);
  animation: fade-up 220ms var(--ease) both;
}

/* ---------- 输入区 ---------- */
.composer { flex: 0 0 auto; padding: 10px 16px 14px; border-top: 1px solid var(--c-border); background: rgba(255, 255, 255, .82); backdrop-filter: blur(8px); }
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

/* 窄屏：会话列表与参数面板改为「浮层抽屉」。
   要点：
   1. 断点用 780px（不是 900px）。900px 时左导航已折叠，正文可用宽度足够并排显示 200px 侧栏，
      过早切成浮层只会白遮一层。
   2. 必须同时把默认状态改为收起（见脚本 isNarrow），否则一进页面就盖住正文。
   3. 用 top/bottom:0 而不是 height:100%，避免高度按视口计算导致溢出。
   4. .sessions--hidden 要重新声明宽度，因为它与 .sessions 同权重，会被本条规则覆盖掉。 */
@media (max-width: 780px) {
  .sessions {
    position: absolute; top: 0; bottom: 0; left: 0; z-index: 12;
    width: 264px; flex-basis: 264px;
    box-shadow: var(--shadow-pop);
  }
  .sessions--hidden { width: 0; flex-basis: 0; box-shadow: none; }

  .params-drawer {
    position: absolute; top: 0; bottom: 0; right: 0; z-index: 12;
    width: min(280px, 86vw); flex-basis: auto;
    box-shadow: var(--shadow-pop);
  }

  .chat__mask {
    display: block; position: absolute; inset: 0; z-index: 11;
    background: rgba(16, 24, 40, .28);
  }

  .thread__inner { padding: 16px 14px 8px; }
  .composer { padding: 8px 12px 12px; }
}

/* 极窄屏（手机竖屏）：进一步压缩留白，保证输入框按钮不被挤出 */
@media (max-width: 480px) {
  .thread__toolbar { gap: 8px; padding: 6px 10px; }
  .thread__inner { padding: 12px 10px 8px; }
  .composer { padding: 6px 10px 10px; }
  .composer__count { display: none; }
  .params-drawer { width: 88vw; }
  /* 手机屏太窄，缩进 40px 会让问题折行，直接顶格 */
  .followups { margin-left: 0; }
}
</style>
