import axios, { type AxiosResponse } from 'axios'
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
  (res: AxiosResponse) => {
    const body = res.data
    if (body && typeof body === 'object' && 'msg' in body && body.msg !== 'success') {
      const message = (body as any)?.data?.message || (body as any).msg || ERROR_CODE_TYPE('default')
      ElMessage.error(message)
      return Promise.reject(body)
    }
    if (body && typeof body === 'object' && 'error' in body) {
      const error = (body as any).error
      const message = error?.message || error?.code || ERROR_CODE_TYPE('default')
      ElMessage.error(message)
      return Promise.reject(body)
    }
    return Promise.resolve(body)
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

export default service
