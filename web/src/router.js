import { createRouter, createWebHistory } from 'vue-router'

const dashboardPaths = [
  '/practice',
  '/indices',
  '/industry-flow',
  '/dragon-tiger',
  '/market-monitor',
  '/x-monitor',
  '/us-ratings',
]

const routes = [
  {
    path: '/',
    redirect: '/practice',
  },
  ...dashboardPaths.map(path => ({
    path,
    component: () => import('./components/DashboardPage.vue'),
  })),
  {
    path: '/admin',
    component: () => import('./components/AdminPage.vue'),
  },
  {
    path: '/admin/settings/:group',
    component: () => import('./components/AdminPage.vue'),
  },
  {
    path: '/:pathMatch(.*)*',
    component: () => import('./components/NotFoundPage.vue'),
  },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
