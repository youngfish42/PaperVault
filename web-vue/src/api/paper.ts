import request from '@/utils/axios'

export interface PaperItem {
  id: string
  conf: string
  year: string
  title: string
  url?: string | null
  authors: string[]
  abstract?: string | null
  code?: string | null
}

export interface PageMeta {
  page: number
  size: number
  total: number
}

export interface PaperSearchQuery {
  q?: string
  field?: 'title' | 'author' | 'any'
  conf?: string[]
  since?: number
  until?: number
  author?: string
  sort?: string
  page?: number
  size?: number
}

export interface PaperSearchResponse {
  items: PaperItem[]
  meta: PageMeta
}

export interface ConfYear {
  year: string
  count: number
}

export interface ConfItem {
  name: string
  total: number
  years: ConfYear[]
}

export interface ConfListResponse {
  items: ConfItem[]
  total: number
}

export interface SuggestResponse {
  keywords: string[]
  timecost_ms: number
  model: string
}

export const searchPapers = (params: PaperSearchQuery) =>
  request<PaperSearchResponse>({
    url: '/v1/papers',
    method: 'get',
    params,
    paramsSerializer: {
      indexes: null
    }
  }) as unknown as Promise<PaperSearchResponse>

export const listConfs = () =>
  request<ConfListResponse>({
    url: '/v1/confs',
    method: 'get'
  }) as unknown as Promise<ConfListResponse>

export const suggestKeywords = (query: string) =>
  request<SuggestResponse>({
    url: '/v1/suggest',
    method: 'post',
    data: { query }
  }) as unknown as Promise<SuggestResponse>
