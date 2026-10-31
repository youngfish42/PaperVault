import { nextTick } from 'vue'
import type { Router } from 'vue-router'

declare global {
  interface Window {
    dataLayer: unknown[]
    gtag: (...args: unknown[]) => void
    // Injected at container start by docker/entrypoint.sh (see index.html placeholder).
    __PAPERVAULT_GA_ID__?: string
  }
}

function trackPageView() {
  window.gtag('event', 'page_view', {
    page_title: document.title,
    page_location: window.location.href
  })
}

export function installGoogleAnalytics(router: Router) {
  if (typeof window === 'undefined') return

  const rawMeasurementId =
    window.__PAPERVAULT_GA_ID__ ||
    import.meta.env.VITE_GOOGLE_ANALYTICS_ID ||
    ''
  const measurementId =
    rawMeasurementId === '__GA_ID__' ? '' : rawMeasurementId.trim()

  if (!measurementId) return

  window.dataLayer = window.dataLayer || []
  window.gtag =
    window.gtag ||
    function () {
      // Google Tag distinguishes the native Arguments object from a rest array.
      // eslint-disable-next-line prefer-rest-params
      window.dataLayer.push(arguments)
    }

  window.gtag('js', new Date())
  window.gtag('config', measurementId, { send_page_view: false })

  const script = document.createElement('script')
  script.async = true
  script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(
    measurementId
  )}`
  document.head.appendChild(script)

  router.afterEach(async (_to, _from, failure) => {
    if (failure) return
    await nextTick()
    trackPageView()
  })
}
