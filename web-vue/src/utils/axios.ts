import axios, { type AxiosRequestConfig, type AxiosResponse } from 'axios'
import { ERROR_CODE_TYPE } from '@/types/error-code-type'
import { ElMessage } from 'element-plus'

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

service.interceptors.response.use(
  // The interceptor narrows the resolved value to ``res.data`` so callers
  // receive the raw payload. The ``as unknown as AxiosResponse`` cast keeps
  // axios' runtime contract while our module augmentation above corrects the
  // public-facing type.
  (res: AxiosResponse) => {
    const body = res.data
    if (
      body &&
      typeof body === 'object' &&
      'msg' in body &&
      body.msg !== 'success'
    ) {
      const message =
        (body as any)?.data?.message ||
        (body as any).msg ||
        ERROR_CODE_TYPE('default')
      ElMessage.error(message)
      return Promise.reject(body)
    }
    if (body && typeof body === 'object' && 'error' in body) {
      const error = (body as any).error
      const message =
        error?.message || error?.code || ERROR_CODE_TYPE('default')
      ElMessage.error(message)
      return Promise.reject(body)
    }
    return body as unknown as AxiosResponse
  },
  err => {
    console.error(err)
    let { message } = err
    if (message === 'Network Error') {
      message = '后端接口连接异常'
    } else if (message?.includes('timeout')) {
      message = '系统接口请求超时'
    } else if (message?.includes('Request failed with status code')) {
      const code = message.substr(message.length - 3)
      message = ERROR_CODE_TYPE(code)
    }
    ElMessage.error({ message, duration: 5 * 1000 })
    return Promise.reject(err)
  }
)

// Typed wrapper: the response interceptor already unwraps ``res.data`` and
// returns the raw payload, so callers should see ``Promise<T>`` instead of
// axios' default ``Promise<AxiosResponse<T>>``. We expose a thin generic
// helper rather than augmenting axios' module declarations - this avoids
// having to mirror axios' overloaded call signatures and keeps the
// public-facing type strictly aligned with the runtime contract.
const request = <T = unknown>(config: AxiosRequestConfig): Promise<T> =>
  service.request(config) as unknown as Promise<T>

export default request
