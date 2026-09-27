import { computed, ref } from 'vue'
import request from '@/utils/axios'

/**
 * Shared auth state (GitHub / Zhihu OAuth + admin login, cookie session).
 *
 * The backend identity lives in the Flask session cookie; this composable
 * caches ``GET /api/v1/auth/me`` at module level so every consumer (nav bar,
 * Advanced Search favorites, …) sees the same login state without each
 * firing its own request. Call ``refresh`` after login/logout redirects
 * (the OAuth callback lands on ``/#/?login=success``) or ``logout`` to
 * clear it.
 */

export interface AuthUserProfile {
  provider?: string
  id?: string | number
  name?: string
  email?: string
  [key: string]: unknown
}

export interface AuthState {
  authenticated: boolean
  isAdmin?: boolean
  username?: string
  user?: AuthUserProfile
}

const authState = ref<AuthState | null>(null)
const fetchedOnce = ref(false)
let inflight: Promise<void> | null = null

const fetchAuth = async (): Promise<void> => {
  try {
    const data = await request<AuthState>({
      url: '/v1/auth/me',
      method: 'get',
      // A logged-out visitor is the normal case, not an error worth a toast.
      silent: true
    })
    authState.value = data.authenticated ? data : null
  } catch {
    authState.value = null
  } finally {
    fetchedOnce.value = true
  }
}

export const useAuth = () => {
  const ensureFetched = (): Promise<void> => {
    if (fetchedOnce.value) return Promise.resolve()
    if (!inflight) {
      inflight = fetchAuth().finally(() => {
        inflight = null
      })
    }
    return inflight
  }

  const refresh = async (): Promise<void> => {
    await fetchAuth()
  }

  const logout = async (): Promise<void> => {
    try {
      await request({ url: '/v1/auth/logout', method: 'post', silent: true })
    } finally {
      authState.value = null
    }
  }

  const currentUser = computed(() => authState.value)
  const isLoggedIn = computed(() => authState.value !== null)
  const displayName = computed(
    () => authState.value?.username || authState.value?.user?.name || ''
  )

  return {
    currentUser,
    isLoggedIn,
    displayName,
    ensureFetched,
    refresh,
    logout
  }
}
