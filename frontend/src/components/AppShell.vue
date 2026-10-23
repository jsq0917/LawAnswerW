<template>
  <div class="shell">
    <!-- 左侧导航 240px；移动端折叠为抽屉 -->
    <aside class="nav" :class="{'nav--open': drawer}">
      <div class="nav__brand">
        <span class="nav__logo">L</span>
        <div class="col">
          <strong>LawAnswer</strong>
          <span class="faint" style="font-size: 11px">法律条文问答</span>
        </div>
      </div>

      <nav class="nav__items">
        <router-link
          v-for="item in items"
          :key="item.to"
          :to="item.to"
          class="nav__item"
          active-class="nav__item--active"
          @click="drawer = false"
        >
          <span class="nav__icon" v-html="item.icon"/>
          <span>{{ item.label }}</span>
        </router-link>
      </nav>

      <div class="nav__foot">
        <div class="nav__user" v-if="auth.user">
          <span class="nav__avatar">{{ auth.username.slice(0, 1).toUpperCase() }}</span>
          <span class="ellipsis">{{ auth.username }}</span>
        </div>
        <button class="nav__logout" type="button" @click="onLogout">退出登录</button>
      </div>
    </aside>

    <div v-if="drawer" class="nav__mask" @click="drawer = false"/>

    <main class="main">
      <header class="topbar">
        <button class="topbar__menu" type="button" aria-label="展开导航" @click="drawer = !drawer">☰</button>
        <h1 class="topbar__title">{{ title }}</h1>
        <slot name="actions"/>
      </header>
      <div class="main__body">
        <slot/>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import {ref} from 'vue'
import {useRouter} from 'vue-router'

import {useAuthStore} from '@/stores/auth'
import {useToast} from '@/composables/useToast'

defineProps<{title: string}>()

const auth = useAuthStore()
const router = useRouter()
const toast = useToast()
const drawer = ref(false)

const items = [
  {
    to: '/chat',
    label: '对话',
    icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 12a8 8 0 0 1-8 8H7l-4 3v-4.5A8 8 0 0 1 13 4a8 8 0 0 1 8 8Z"/></svg>',
  },
  {
    to: '/kb',
    label: '知识库',
    icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5V5.5Z"/><path d="M4 20.5A2.5 2.5 0 0 1 6.5 18H20v3H6.5A2.5 2.5 0 0 1 4 20.5Z"/></svg>',
  },
]

function onLogout() {
  auth.logout()
  toast.info('已退出登录')
  router.push({name: 'login'})
}
</script>

<style scoped>
.shell {
  display: flex;
  min-height: 100vh;
  background: var(--c-bg);
}

/* ---------- 左导航 ---------- */
.nav {
  display: flex;
  flex-direction: column;
  width: var(--w-nav);
  flex: 0 0 var(--w-nav);
  padding: 18px 14px;
  background: #fff;
  border-right: 1px solid var(--c-border);
  position: sticky;
  top: 0;
  height: 100vh;
  z-index: 20;
}
.nav__brand { display: flex; align-items: center; gap: 10px; padding: 0 6px 18px; }
.nav__logo {
  display: grid; place-items: center;
  width: 32px; height: 32px; border-radius: 10px;
  background: var(--c-primary); color: #fff; font-weight: 700;
}
.nav__items { display: flex; flex-direction: column; gap: 4px; }
.nav__item {
  display: flex; align-items: center; gap: 10px;
  height: 40px; padding: 0 12px; border-radius: 10px;
  color: var(--c-muted); font-weight: 500;
  transition: background var(--dur) var(--ease), color var(--dur) var(--ease);
}
.nav__item:hover { background: var(--c-bg); color: var(--c-text); }
.nav__item--active { background: var(--c-primary-soft); color: var(--c-primary); font-weight: 600; }
.nav__icon { display: inline-flex; }
.nav__foot { margin-top: auto; border-top: 1px solid var(--c-border); padding-top: 12px; }
.nav__user { display: flex; align-items: center; gap: 8px; padding: 4px 6px 10px; font-size: 13px; }
.nav__avatar {
  display: grid; place-items: center;
  width: 26px; height: 26px; border-radius: 50%;
  background: var(--c-accent-soft); color: #0B7D5B; font-size: 12px; font-weight: 700;
}
.nav__logout {
  width: 100%; height: 34px; border: 1px solid var(--c-border);
  border-radius: 9px; background: #fff; color: var(--c-muted);
  cursor: pointer; font-size: 13px;
  transition: all var(--dur) var(--ease);
}
.nav__logout:hover { border-color: var(--c-danger); color: var(--c-danger); }

/* ---------- 主区 ---------- */
.main { display: flex; flex-direction: column; flex: 1; min-width: 0; }
.topbar {
  display: flex; align-items: center; gap: 12px;
  height: var(--h-topbar); padding: 0 20px;
  background: rgba(255, 255, 255, .82);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--c-border);
  position: sticky; top: 0; z-index: 10;
}
.topbar__title { font-size: 15px; font-weight: 600; }
.topbar__menu {
  display: none; width: 32px; height: 32px;
  border: 1px solid var(--c-border); border-radius: 8px;
  background: #fff; cursor: pointer;
}
.main__body { flex: 1; min-height: 0; display: flex; flex-direction: column; }

.nav__mask { display: none; }

@media (max-width: 900px) {
  .nav {
    position: fixed; left: 0; top: 0; bottom: 0;
    transform: translateX(-100%);
    transition: transform var(--dur) var(--ease);
    box-shadow: var(--shadow-pop);
  }
  .nav--open { transform: translateX(0); }
  .nav__mask {
    display: block; position: fixed; inset: 0;
    background: rgba(16, 24, 40, .28); z-index: 15;
  }
  .topbar__menu { display: inline-flex; align-items: center; justify-content: center; }
}
</style>
