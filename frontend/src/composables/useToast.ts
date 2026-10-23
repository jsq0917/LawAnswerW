/** 统一提示：Element Plus ElMessage 的轻封装，保证文案风格一致。 */
import {ElMessage} from 'element-plus'

export function useToast() {
  return {
    success: (msg: string) => ElMessage({message: msg, type: 'success', duration: 2200}),
    error: (msg: string) => ElMessage({message: msg, type: 'error', duration: 3200}),
    warn: (msg: string) => ElMessage({message: msg, type: 'warning', duration: 2800}),
    info: (msg: string) => ElMessage({message: msg, type: 'info', duration: 2200}),
  }
}
