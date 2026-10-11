import { MAX_RESUME_BYTES, RESUME_LIST_LIMIT, resumeTimestamp } from '../api/resumes'
import type { Resume } from '../api/resumes'

export function uploadFileIdentity(file: File): string {
  return JSON.stringify([file.name, file.size, file.lastModified])
}

export function filePreflight(file: File): string | null {
  if (!file.name.toLowerCase().endsWith('.pdf')) return '只支持 PDF 文件。'
  if (file.size === 0) return '文件为空，请选择有效的 PDF。'
  if (file.size > MAX_RESUME_BYTES) return '文件不能超过 5 MiB。'
  return null
}

export function formatResumeSize(bytes: number | null): string {
  if (bytes === null) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${Math.round(bytes / (1024 * 1024) * 10) / 10} MB`
}

export function formatResumeDate(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(resumeTimestamp(value))
}

export function resumeStatus(status: string): string {
  return status === 'uploaded' ? '已上传' : '状态暂不可识别'
}

export function mergeConfirmedResume(items: Resume[], confirmed: Resume): Resume[] {
  return [confirmed, ...items.filter((item) => item.id !== confirmed.id)].slice(0, RESUME_LIST_LIMIT)
}
