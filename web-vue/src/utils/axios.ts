import axios, { type AxiosRequestConfig, type AxiosResponse } from 'axios'
import { ERROR_CODE_TYPE } from '@/types/error-code-type'
import { ElMessage } from 'element-plus'

export interface AppAxiosRequestConfig extends AxiosRequestConfig {
  /**
   * When ``true`` the response interceptor will suppress the global
   * ``ElMessage.error`` toast for this request. Callers should handle the
   * rejection locally (e.g. surface the failure inside a specific widget).
   */
  silent?: boolean
}

const service = axios.create({
  baseURL: '/api',
  timeout: 60000
})

service.interceptors.request.use(
  config => config,
  err => {
    console.error(err)
    return Promise.reject(err)
  }
)

const isSilent = (config: AxiosRequestConfig | undefined): boolean =>
  Boolean((config as AppAxiosRequestConfig | undefined)?.silent)

service.interceptors.response.use(
  // The interceptor narrows the resolved value to ``res.data`` so callers
  // receive the raw payload. The ``as unknown as AxiosResponse`` cast keeps
  // axios' runtime contract while our module augmentation above corrects the
  // public-facing type.
  (res: AxiosResponse) => {
    const body = res.data
    // v1 API contract (see ``papervault/errors.py``): success responses are
    // plain domain payloads (e.g. ``{items, meta}``, ``{keywords, ...}``),
    // errors always come wrapped as ``{error: {code, message, details?}}``.
    // A 2xx response that still carries an ``error`` key means the backend
    // encountered a soft failure it chose to signal in-band — surface it
    // as a rejection so callers can handle it uniformly with the axios
    // error branch below.
    if (body && typeof body === 'object' && 'error' in body) {
      const error = (body as any).error
      const message =
        error?.message || error?.code || ERROR_CODE_TYPE('default')
      if (!isSilent(res.config)) ElMessage.error(message)
      return Promise.reject(body)
    }
    return body as unknown as AxiosResponse
  },
  err => {
    // Log the raw failure so DevTools always shows what really came back
    // (status, body shape, headers). The interceptor's job is just to
    // surface a readable message; debugging info lives here.
    console.error(
      '[axios] request failed:',
      err?.config?.url,
      'status=',
      err?.response?.status,
      'body=',
      err?.response?.data
    )
    let { message } = err
    const status = err?.response?.status
    const body = err?.response?.data
    // Prefer the server's structured envelope: ``{error: {code, message}}``.
    // Fall back to a flat ``{message}`` body for endpoints that didn't
    // wrap their errors. Final fallback: axios' default message with a
    // "[503]" prefix so the status code is never lost.
    let serverMsg: string | undefined
    if (body && typeof body === 'object') {
      serverMsg =
        (body as any)?.error?.message ||
        (body as any)?.message ||
        (body as any)?.msg
    }
    if (typeof serverMsg === 'string' && serverMsg) {
      message = serverMsg
    } else if (status) {
      message = `[${status}] ${message || 'Request failed'}`
    } else if (message === 'Network Error') {
      message = '后端接口连接异常'
    } else if (message?.includes('timeout')) {
      message = '系统接口请求超时'
    } else if (message?.includes('Request failed with status code')) {
      const code = message.substr(message.length - 3)
      message = ERROR_CODE_TYPE(code)
    }
    if (!isSilent(err?.config)) {
      ElMessage.error({ message, duration: 5 * 1000 })
    }
    return Promise.reject(err)
  }
)

// Typed wrapper: the response interceptor already unwraps ``res.data`` and
// returns the raw payload, so callers should see ``Promise<T>`` instead of
// axios' default ``Promise<AxiosResponse<T>>``. We expose a thin generic
// helper rather than augmenting axios' module declarations - this avoids
// having to mirror axios' overloaded call signatures and keeps the
// public-facing type strictly aligned with the runtime contract.
const request = <T = unknown>(config: AppAxiosRequestConfig): Promise<T> =>
  service.request(config) as unknown as Promise<T>

export default request
