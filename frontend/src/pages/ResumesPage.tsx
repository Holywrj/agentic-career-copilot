import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router'
import { ApiError } from '../api/client'
import { deleteResume, listResumes, resumeErrorMessage, uncertainResumeWrite, uploadResume } from '../api/resumes'
import type { Resume } from '../api/resumes'
import { useAuth } from '../auth/AuthContext'
import { Brand } from '../components/AuthLayout'
import { filePreflight, formatResumeDate, formatResumeSize, mergeConfirmedResume, resumeStatus, uploadFileIdentity } from './resumePresentation'

type Activity = { kind: 'idle' | 'loading' | 'uploading' | 'refreshing' } | { kind: 'deleting'; id: string }
type RecoveryReason = 'upload' | 'delete' | 'missing' | 'delete-storage'
type Notice =
  | { kind: 'success' | 'error'; text: string }
  | { kind: 'recovery'; reason: RecoveryReason; status: 'pending' | 'refreshed' | 'failed'; detail: string }
type ReadContext = { kind: 'normal' } | { kind: 'confirmed'; action: 'upload' | 'delete' } | { kind: 'recovery'; reason: RecoveryReason; detail: string }

const recoveryCopy: Record<RecoveryReason, string> = {
  upload: '无法确认刚才的上传是否完成。再次上传可能产生重复简历。',
  delete: '无法确认刚才的删除是否完成。',
  missing: '这份简历当前已不可见，正在更新列表。',
  'delete-storage': '删除时简历服务暂时不可用；列表信息不能确认文件是否仍可用。',
}

export function ResumesPage() {
  const { requestAuthenticated, logout } = useAuth()
  const [list, setList] = useState<{ items: Resume[] | null; error: string | null }>({ items: null, error: null })
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [retryCandidate, setRetryCandidate] = useState<string | null>(null)
  const [activity, setActivity] = useState<Activity>({ kind: 'loading' })
  const [notice, setNotice] = useState<Notice | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const operation = useRef<AbortController | null>(null)
  const current = useCallback((active: AbortController) => operation.current === active && !active.signal.aborted, [])
  const finish = useCallback((active: AbortController) => {
    if (operation.current === active) { operation.current = null; setActivity({ kind: 'idle' }) }
  }, [])

  const read = useCallback(async (active: AbortController, context: ReadContext) => {
    try {
      const latest = await listResumes(requestAuthenticated, active.signal)
      if (!current(active)) return
      setList({ items: latest.items, error: null })
      if (context.kind === 'recovery') setNotice({ kind: 'recovery', reason: context.reason, detail: context.detail, status: 'refreshed' })
    } catch (failure) {
      if (!current(active) || (failure instanceof ApiError && failure.kind === 'cancelled')) return
      const prefix = context.kind === 'confirmed'
        ? context.action === 'upload' ? '上传已确认，但列表刷新失败。' : '删除已确认，但列表刷新失败。'
        : ''
      setList((previous) => ({ ...previous, error: prefix + resumeErrorMessage(failure) }))
      if (context.kind === 'recovery') setNotice({ kind: 'recovery', reason: context.reason, detail: context.detail, status: 'failed' })
    }
  }, [requestAuthenticated, current])

  useEffect(() => {
    const active = new AbortController()
    operation.current = active
    void read(active, { kind: 'normal' }).finally(() => finish(active))
    return () => { operation.current?.abort(); operation.current = null }
  }, [read, finish])

  // Ref ownership blocks a second submission synchronously, before React has
  // rendered disabled controls. The Provider alone owns Session correctness.
  function begin(next: Activity): AbortController | null {
    if (operation.current) return null
    const active = new AbortController()
    operation.current = active
    setActivity(next)
    return active
  }

  function reload() {
    const active = begin({ kind: list.items === null ? 'loading' : 'refreshing' })
    if (!active) return
    setList((previous) => ({ ...previous, error: null }))
    const context: ReadContext = notice?.kind === 'recovery'
      ? { kind: 'recovery', reason: notice.reason, detail: notice.detail } : { kind: 'normal' }
    if (notice?.kind === 'recovery') setNotice({ ...notice, status: 'pending' })
    void read(active, context).finally(() => finish(active))
  }

  async function recover(active: AbortController, reason: RecoveryReason, detail = '') {
    setActivity({ kind: 'refreshing' })
    setNotice({ kind: 'recovery', reason, detail, status: 'pending' })
    await read(active, { kind: 'recovery', reason, detail })
  }

  const busy = activity.kind !== 'idle'
  const recoveryBlocked = notice?.kind === 'recovery' && notice.status !== 'refreshed'
  const canWrite = list.items !== null && !list.error && !recoveryBlocked && !busy
  const selectionError = selectedFile ? filePreflight(selectedFile) : null
  const retryUpload = notice?.kind === 'recovery' && notice.reason === 'upload'
    && selectedFile !== null && retryCandidate === uploadFileIdentity(selectedFile)

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canWrite || !selectedFile || selectionError || operation.current) return
    if (retryUpload && !window.confirm('先前上传可能已完成，再次上传可能产生重复简历，确定继续？')) return
    const active = begin({ kind: 'uploading' })
    if (!active) return
    setNotice(null)
    setRetryCandidate(null)
    try {
      const confirmed = await uploadResume(requestAuthenticated, selectedFile, active.signal)
      if (!current(active)) return
      setList((previous) => ({ items: mergeConfirmedResume(previous.items ?? [], confirmed), error: null }))
      setSelectedFile(null)
      if (fileInput.current) fileInput.current.value = ''
      setNotice({ kind: 'success', text: `简历已上传：${confirmed.original_filename}。` })
      setActivity({ kind: 'refreshing' })
      await read(active, { kind: 'confirmed', action: 'upload' })
    } catch (failure) {
      if (!current(active) || (failure instanceof ApiError && failure.kind === 'cancelled')) return
      if (uncertainResumeWrite(failure)) {
        setRetryCandidate(uploadFileIdentity(selectedFile))
        await recover(active, 'upload', failure instanceof ApiError && failure.status === 503 ? '简历服务暂时不可用，请稍后重试。' : '')
      }
      else setNotice({ kind: 'error', text: failure instanceof ApiError && failure.code === 'invalid_request'
        ? '无法上传这份文件，请确认选择的是有效的 PDF。' : resumeErrorMessage(failure) })
    } finally { finish(active) }
  }

  async function remove(item: Resume) {
    if (!canWrite || operation.current || !window.confirm(`确定删除简历“${item.original_filename}”吗？删除后无法恢复。`)) return
    const active = begin({ kind: 'deleting', id: item.id })
    if (!active) return
    setNotice(null)
    try {
      await deleteResume(requestAuthenticated, item.id, active.signal)
      if (!current(active)) return
      setList((previous) => ({ items: previous.items?.filter((resume) => resume.id !== item.id) ?? [], error: null }))
      setNotice({ kind: 'success', text: `简历已删除：${item.original_filename}。` })
      setActivity({ kind: 'refreshing' })
      await read(active, { kind: 'confirmed', action: 'delete' })
    } catch (failure) {
      if (!current(active) || (failure instanceof ApiError && failure.kind === 'cancelled')) return
      if (failure instanceof ApiError && failure.kind === 'resume_not_found') await recover(active, 'missing')
      else if (failure instanceof ApiError && failure.kind === 'resume_storage_unavailable') await recover(active, 'delete-storage')
      else if (uncertainResumeWrite(failure)) await recover(active, 'delete', failure instanceof ApiError && failure.status === 503 ? '简历服务暂时不可用，请稍后重试。' : '')
      else setNotice({ kind: 'error', text: resumeErrorMessage(failure) })
    } finally { finish(active) }
  }

  return <div className="site-shell resumes-shell">
    <header className="site-header">
      <Brand />
      <div className="workspace-actions">
        <Link className="button secondary" to="/app">返回工作台</Link>
        <button type="button" className="button secondary" onClick={() => { void logout() }}>退出登录</button>
      </div>
    </header>
    <main className="resumes-main">
      <p className="eyebrow">保存你的职业经历</p>
      <h1 id="page-title" tabIndex={-1}>我的简历</h1>
      <p className="form-intro">上传并管理原始 PDF，方便后续使用。当前仅保存文件，尚不解析简历内容。</p>
      <form className="resume-upload" aria-label="上传简历" aria-busy={activity.kind === 'uploading'} onSubmit={upload}>
        <div className="field">
          <label htmlFor="resume-file">选择 PDF</label>
          <input ref={fileInput} id="resume-file" type="file" accept="application/pdf,.pdf" disabled={busy}
            aria-invalid={!!selectionError} aria-describedby={`resume-file-help${selectionError ? ' resume-file-error' : ''}`}
            onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)} />
          <p id="resume-file-help" className="field-help">支持 PDF · 最大 5 MiB。服务器也会检查文件内容和上传请求大小。</p>
          {selectedFile && <p className="resume-selection">已选择：<span>{selectedFile.name}</span> · {formatResumeSize(selectedFile.size)}</p>}
          {selectionError && <p id="resume-file-error" className="notice error" role="alert">{selectionError}</p>}
        </div>
        <button className="button primary" type="submit" disabled={!canWrite || !selectedFile || !!selectionError}>
          {activity.kind === 'uploading' ? '正在上传…' : retryUpload ? '再次上传此文件' : '上传简历'}
        </button>
      </form>
      <div aria-live="polite" aria-atomic="true">
        {notice?.kind === 'success' && <p className="notice success">{notice.text}</p>}
      </div>
      {notice?.kind === 'error' && <p className="notice error" role="alert">{notice.text}</p>}
      {notice?.kind === 'recovery' && <section className="notice warning resume-recovery" aria-labelledby="resume-recovery-title">
        <h2 id="resume-recovery-title">{notice.reason === 'missing' ? '列表已发生变化' : notice.reason === 'delete-storage' ? '需要刷新当前状态' : '操作结果待确认'}</h2>
        <p role="status">{recoveryCopy[notice.reason]} {notice.detail}
          {notice.status === 'pending' ? ' 正在刷新服务器当前列表…' : notice.status === 'failed'
            ? ' 暂时无法刷新服务器当前列表，请先重新读取列表。'
            : ' 已刷新服务器当前列表；这不是刚才操作完成的凭据。'}
        </p>
        <p>当前列表仅显示最近 100 份简历；某条记录未出现，不能证明它已被删除或上传未完成。</p>
      </section>}
      <section className="resume-list-section" aria-labelledby="resume-list-title" aria-busy={busy}>
        <div className="section-heading">
          <h2 id="resume-list-title">已保存的简历</h2>
          <button type="button" className="button secondary" disabled={busy} onClick={reload}>{list.error ? '重新读取列表' : '刷新列表'}</button>
        </div>
        <p className="field-help">最多显示最近 100 份简历{list.items?.length === 100 ? '，较早的简历可能未显示' : ''}。</p>
        {activity.kind === 'loading' && <p role="status">正在加载简历列表…</p>}
        {activity.kind === 'refreshing' && <p role="status">正在刷新简历列表…</p>}
        {activity.kind === 'uploading' && <p role="status">正在上传简历，请稍候…</p>}
        {activity.kind === 'deleting' && <p role="status">正在删除简历，请稍候…</p>}
        {list.error && <p className="notice error" role="alert">{list.error}{list.items !== null && ' 当前显示此前已读取或已确认的数据。'}</p>}
        {list.items?.length === 0 && !list.error && <p className="resume-empty">还没有简历。选择一份 PDF，保存你的第一份简历。</p>}
        {list.items !== null && list.items.length > 0 && <ul className="resume-list">
          {list.items.map((item) => <li className="resume-item" key={item.id}>
            <div className="resume-item-content">
              <h3>{item.original_filename}</h3>
              <dl className="resume-meta">
                <div><dt>大小</dt><dd>{formatResumeSize(item.size_bytes)}</dd></div>
                <div><dt>上传时间</dt><dd><time dateTime={item.created_at}>{formatResumeDate(item.created_at)}</time></dd></div>
                <div><dt>状态</dt><dd>{resumeStatus(item.status)}</dd></div>
                {item.media_type === null && <div><dt>类型</dt><dd>未记录</dd></div>}
              </dl>
            </div>
            <button type="button" className="button secondary resume-delete" disabled={!canWrite} aria-label={`删除简历 ${item.original_filename}`}
              onClick={() => { void remove(item) }}>{activity.kind === 'deleting' && activity.id === item.id ? '正在删除…' : '删除'}</button>
          </li>)}
        </ul>}
      </section>
    </main>
  </div>
}
