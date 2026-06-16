<script setup>
const navItems = [
  { path: '/', label: '仪表盘' },
  { path: '/profile', label: '个人资料' },
  { path: '/report', label: '健康报告' }
]
</script>

<template>
  <div class="app-shell">
    <nav class="top-nav">
      <router-link class="brand" to="/">糖尿病管理助手</router-link>
      <div class="nav-links">
        <router-link v-for="item in navItems" :key="item.path" :to="item.path" class="nav-link">
          {{ item.label }}
        </router-link>
      </div>
    </nav>
    <router-view />
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  background: #eef4fb;
}

.top-nav {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 24px;
  background: rgba(255, 255, 255, 0.94);
  border-bottom: 1px solid #e2e8f0;
  backdrop-filter: blur(12px);
}

.brand {
  color: #0f172a;
  font-weight: 700;
  text-decoration: none;
  white-space: nowrap;
}

.nav-links {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.nav-link {
  color: #475569;
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 13px;
  text-decoration: none;
}

.nav-link.router-link-exact-active {
  color: #2563eb;
  background: #dbeafe;
}

@media (max-width: 640px) {
  .top-nav {
    align-items: flex-start;
    flex-direction: column;
    padding: 10px 14px;
  }
}
</style>
