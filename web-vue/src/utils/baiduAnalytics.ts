import { nextTick } from 'vue'
import type { Router } from 'vue-router'

declare global {
  interface Window {
    // Baidu Tongji tracker queue, initialised by the inline snippet in index.html.
    _hmt: unknown[]
  }
}

function trackPageView() {
  window._hmt.push(['_trackPageview', window.location.href])
}

/**
 * Hooks into Vue Router to report page views for SPA navigation.
 * The Baidu Tongji hm.js script is loaded by the inline snippet in index.html,
 * which already reports the initial page load, so we skip the first navigation.
 */
export function installBaiduAnalytics(router: Router) {
  if (typeof window === 'undefined' || !Array.isArray(window._hmt)) return

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
