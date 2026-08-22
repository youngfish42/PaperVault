import { nextTick } from 'vue'
import type { Router } from 'vue-router'

declare global {
  interface Window {
    // Baidu Tongji tracker queue.
    _hmt: unknown[]
    // Injected at container start by docker/entrypoint.sh (see index.html placeholder).
    __PAPERVAULT_BA_ID__?: string
  }
}

function trackPageView() {
  window._hmt.push(['_trackPageview', window.location.href])
}

export function installBaiduAnalytics(router: Router) {
  if (typeof window === 'undefined') return

  const rawTrackingId =
    window.__PAPERVAULT_BA_ID__ || import.meta.env.VITE_BAIDU_ANALYTICS_ID || ''
  const trackingId = rawTrackingId === '__BA_ID__' ? '' : rawTrackingId.trim()

  if (!trackingId) return

  window._hmt = window._hmt || []

  const script = document.createElement('script')
  script.async = true
  script.src = `https://hm.baidu.com/hm.js?${encodeURIComponent(trackingId)}`
  document.head.appendChild(script)

  // hm.js 在加载时会自动上报当前页面，因此跳过首次导航，避免 PV 重复计数。
  let isFirstNavigation = true
  router.afterEach(async (_to, _from, failure) => {
    if (failure) return
    await nextTick()
    if (isFirstNavigation) {
      isFirstNavigation = false
      return
    }
    trackPageView()
  })
}
