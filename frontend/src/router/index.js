import { createRouter, createWebHistory } from 'vue-router'

import Dashboard from '../views/Dashboard.vue'
import ProfileView from '../views/ProfileView.vue'
import ReportView from '../views/ReportView.vue'

const routes = [
  { path: '/', name: 'dashboard', component: Dashboard, meta: { title: '仪表盘' } },
  { path: '/profile', name: 'profile', component: ProfileView, meta: { title: '个人资料' } },
  { path: '/report', name: 'report', component: ReportView, meta: { title: '健康报告' } }
]

export const router = createRouter({
  history: createWebHistory(),
  routes
})
