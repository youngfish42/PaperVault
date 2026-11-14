import request from '@/utils/axios'

/**
 * Saved advanced-search queries (favorites), persisted server-side per
 * logged-in user. All routes require an authenticated session; anonymous
 * callers get a 401 ``UNAUTHORIZED`` envelope.
 */

export interface SavedQuery {
  id: number
  name: string
  dsl: string
  /** Result count recorded at save / last manual refresh; null = unknown. */
  last_count: number | null
  created_at: string
  updated_at: string
}

export interface SavedQueryListResponse {
  items: SavedQuery[]
  total: number
}

export interface SavedQueryCreatePayload {
  name: string
  dsl: string
  last_count?: number | null
}

export interface SavedQueryUpdatePayload {
  name?: string
  dsl?: string
  last_count?: number | null
}

export const listSavedQueries = () =>
  request<SavedQueryListResponse>({
    url: '/v1/saved_queries',
    method: 'get'
  })

export const createSavedQuery = (data: SavedQueryCreatePayload) =>
  request<SavedQuery>({
    url: '/v1/saved_queries',
    method: 'post',
    data
  })

export const updateSavedQuery = (id: number, data: SavedQueryUpdatePayload) =>
  request<SavedQuery>({
    url: `/v1/saved_queries/${id}`,
    method: 'patch',
    data
  })

export const deleteSavedQuery = (id: number) =>
  request<{ deleted: boolean }>({
    url: `/v1/saved_queries/${id}`,
    method: 'delete'
  })
