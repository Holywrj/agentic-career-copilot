import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router'
import { ApiError } from '../api/client'
import {
  careerProfileErrorMessage, createCareerProfile, getCareerProfile, uncertainWrite, updateCareerProfile,
} from '../api/careerProfile'
import type { CareerProfile, ProfileFields } from '../api/careerProfile'
import { useAuth } from '../auth/AuthContext'
import { Brand } from '../components/AuthLayout'
import { StringListEditor } from '../components/StringListEditor'

interface Draft { summary: string; skills: string[]; roles: string[] }
interface Editor { canonical: CareerProfile | null; draft: Draft }
type RecoveryReason = 'conflict' | 'already-exists' | 'uncertain' | 'missing'
interface Recovery {
  reason: RecoveryReason
  status: 'loading' | 'failed' | 'ready'
  latest: CareerProfile | null
  error: string | null
}

function toDraft(profile: CareerProfile | null): Draft {
  return { summary: profile?.career_summary ?? '', skills: [...(profile?.skills ?? [])], roles: [...(profile?.target_roles ?? [])] }
}

function fields(draft: Draft): ProfileFields {
  return { career_summary: draft.summary === '' ? null : draft.summary, skills: [...draft.skills], target_roles: [...draft.roles] }
}

function isCancelled(error: unknown) { return error instanceof ApiError && error.kind === 'cancelled' }
function isMissing(error: unknown) { return error instanceof ApiError && error.kind === 'career_profile_not_found' }

const recoveryMessages: Record<RecoveryReason, string> = {
  conflict: '档案已在其他位置修改。当前草稿已保留，请比较最新服务器版本后再决定。',
  'already-exists': '档案已在其他位置创建。当前草稿已保留，请比较最新服务器版本后再决定。',
  uncertain: '写入结果不确定：请求可能已在服务器完成，但客户端无法确认。当前草稿已保留；下面的服务器状态不能证明刚才保存成功。',
  missing: '服务器上已找不到原档案。当前草稿已保留，请检查服务器状态后再决定。',
}

export function CareerProfilePage() {
  const { requestAuthenticated, logout } = useAuth()
  const [editor, setEditor] = useState<Editor | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [recovery, setRecovery] = useState<Recovery | null>(null)
  const [skillEntry, setSkillEntry] = useState('')
  const [roleEntry, setRoleEntry] = useState('')
  const operation = useRef<AbortController | null>(null)

  // Controller identity guards late results even when an underlying requester
  // cannot cancel transport. AuthProvider separately guards Session generations.
  const load = useCallback(() => {
    operation.current?.abort()
    const active = new AbortController()
    operation.current = active
    void getCareerProfile(requestAuthenticated, active.signal).then((profile) => {
      if (operation.current === active && !active.signal.aborted) setEditor({ canonical: profile, draft: toDraft(profile) })
    }).catch((failure: unknown) => {
      if (operation.current !== active || active.signal.aborted || isCancelled(failure)) return
      if (isMissing(failure)) setEditor({ canonical: null, draft: toDraft(null) })
      else setLoadError(careerProfileErrorMessage(failure))
    }).finally(() => {
      if (operation.current === active) operation.current = null
    })
  }, [requestAuthenticated])

  useEffect(() => {
    void load()
    return () => { operation.current?.abort(); operation.current = null }
  }, [load])

  const dirty = editor !== null && JSON.stringify(editor.draft) !== JSON.stringify(toDraft(editor.canonical))
  const hasEntry = skillEntry !== '' || roleEntry !== ''
  const protectEdits = dirty || hasEntry || pending || recovery !== null
  useEffect(() => {
    if (!protectEdits) return
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', beforeUnload)
    return () => window.removeEventListener('beforeunload', beforeUnload)
  }, [protectEdits])

  function allowLeave() {
    return !protectEdits || window.confirm('当前有未保存的草稿或尚未确认的写入结果。离开将丢失当前草稿，确定离开？')
  }

  async function reconcile(reason: RecoveryReason, active: AbortController) {
    setRecovery({ reason, status: 'loading', latest: null, error: null })
    try {
      let latest: CareerProfile | null
      try { latest = await getCareerProfile(requestAuthenticated, active.signal) }
      catch (failure) { if (isMissing(failure)) latest = null; else throw failure }
      if (operation.current === active && !active.signal.aborted) setRecovery({ reason, status: 'ready', latest, error: null })
    } catch (failure) {
      if (operation.current === active && !active.signal.aborted && !isCancelled(failure)) {
        setRecovery({ reason, status: 'failed', latest: null, error: careerProfileErrorMessage(failure) })
      }
    }
  }

  async function retryReconciliation() {
    if (operation.current || !recovery) return
    const active = new AbortController()
    operation.current = active
    setPending(true)
    try { await reconcile(recovery.reason, active) }
    finally { if (operation.current === active) { operation.current = null; setPending(false) } }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (operation.current || !editor || recovery || (editor.canonical && !dirty && !hasEntry)) return
    setError(null)
    setSuccess(null)
    if (hasEntry) { setError('列表输入框还有未添加的内容，请先添加或清空，再保存档案。'); return }
    const summaryCount = Array.from(editor.draft.summary.replace(/\r\n?/g, '\n').normalize('NFC').trim()).length
    if (summaryCount > 2000) { setError('职业概述最多 2000 个 Unicode 字符，请缩短后保存。'); return }
    const active = new AbortController()
    operation.current = active
    setPending(true)
    try {
      const data = fields(editor.draft)
      const profile = editor.canonical
        ? await updateCareerProfile(requestAuthenticated, { ...data, expected_revision: editor.canonical.revision }, active.signal)
        : await createCareerProfile(requestAuthenticated, data, active.signal)
      if (operation.current !== active || active.signal.aborted) return
      setEditor({ canonical: profile, draft: toDraft(profile) })
      setSuccess('职业档案已保存，当前显示服务器确认的数据。')
    } catch (failure) {
      if (operation.current !== active || active.signal.aborted || isCancelled(failure)) return
      if (failure instanceof ApiError && failure.kind === 'career_profile_conflict') await reconcile('conflict', active)
      else if (failure instanceof ApiError && failure.kind === 'career_profile_already_exists') await reconcile('already-exists', active)
      else if (isMissing(failure)) await reconcile('missing', active)
      else if (uncertainWrite(failure)) {
        setError(careerProfileErrorMessage(failure))
        await reconcile('uncertain', active)
      } else setError(careerProfileErrorMessage(failure))
    } finally {
      if (operation.current === active) { operation.current = null; setPending(false) }
    }
  }

  function resolveRecovery(keepDraft: boolean) {
    if (operation.current || !editor || recovery?.status !== 'ready') return
    if (!keepDraft && (dirty || hasEntry) && !window.confirm('使用服务器状态会替换当前草稿和未添加的列表输入，确定继续？')) return
    const latest = recovery.latest
    setEditor({ canonical: latest, draft: keepDraft ? editor.draft : toDraft(latest) })
    if (!keepDraft) { setSkillEntry(''); setRoleEntry('') }
    setRecovery(null)
    setError(null)
    setSuccess(keepDraft
      ? latest ? '已采用服务器最新 revision 作为编辑基线，草稿已保留。下一次保存仍会完整替换职业概述、技能和目标岗位。'
        : '已确认当前服务器尚无档案，草稿已保留。你可以主动创建档案。'
      : '已使用服务器状态重新开始编辑。')
  }

  function edit(patch: Partial<Draft>) {
    setEditor((current) => current ? { ...current, draft: { ...current.draft, ...patch } } : current)
    setSuccess(null)
    setError(null)
  }

  const summaryCount = editor ? Array.from(editor.draft.summary.replace(/\r\n?/g, '\n').normalize('NFC').trim()).length : 0
  return <div className="site-shell profile-shell">
    <header className="site-header" onClickCapture={(event) => {
      if ((event.target as HTMLElement).closest('a') && !allowLeave()) event.preventDefault()
    }}>
      <Brand />
      <div className="workspace-actions">
        <Link className="button secondary" to="/app">返回工作台</Link>
        <button type="button" className="button secondary" onClick={() => { if (allowLeave()) void logout() }}>退出登录</button>
      </div>
    </header>
    <main className="profile-main">
      <p className="eyebrow">你的职业方向</p>
      <h1 id="page-title" tabIndex={-1}>职业档案</h1>
      <p className="form-intro">梳理职业背景、经验与目标，为下一步打好基础。</p>
      {!editor && <div role="status">{loadError ? null : '正在加载职业档案…'}</div>}
      {loadError && <div><p className="notice error" role="alert">{loadError}</p>
        <button type="button" className="button secondary" onClick={() => { if (!operation.current) { setLoadError(null); void load() } }}>重新加载档案</button>
      </div>}
      {editor && <>
        <p className="profile-meta">{editor.canonical ? `当前编辑基线：revision ${editor.canonical.revision}` : '尚未创建档案。可以填写后创建，也可以创建空档案。'}{dirty && ' · 有未保存的修改'}</p>
        {recovery && <section className="profile-recovery notice warning" aria-labelledby="recovery-title" aria-busy={recovery.status === 'loading'}>
          <h2 id="recovery-title">{recovery.reason === 'uncertain' ? '写入结果待确认' : '需要确认服务器状态'}</h2>
          <p role="status">{recoveryMessages[recovery.reason]}</p>
          {recovery.status === 'loading' && <p role="status">正在读取最新服务器档案…</p>}
          {recovery.status === 'failed' && <><p role="alert">{recovery.error} 草稿仍保留，读取成功前不能保存。</p>
            <button type="button" className="button secondary" disabled={pending} onClick={() => { void retryReconciliation() }}>重新读取服务器档案</button>
          </>}
          {recovery.status === 'ready' && <>
            <h3>最新服务器状态{recovery.latest ? ` · revision ${recovery.latest.revision}` : ''}</h3>
            {recovery.latest ? <dl className="server-profile">
              <dt>职业概述</dt><dd>{recovery.latest.career_summary ?? '未填写'}</dd>
              <dt>技能</dt><dd>{recovery.latest.skills.length ? <ul>{recovery.latest.skills.map((skill) => <li key={skill}>{skill}</li>)}</ul> : '未添加'}</dd>
              <dt>目标岗位</dt><dd>{recovery.latest.target_roles.length ? <ul>{recovery.latest.target_roles.map((role) => <li key={role}>{role}</li>)}</ul> : '未添加'}</dd>
            </dl> : <p>当前服务器尚无职业档案。这不保证先前请求不会稍后完成。</p>}
            <p>这不是自动合并。保留草稿会采用当前服务器 revision 作为新基线；下一次主动保存仍会完整替换职业概述、技能和目标岗位。服务器此后仍可能变化，届时会再次检查 revision。</p>
            <div className="profile-decisions">
              <button type="button" className="button secondary" disabled={pending} onClick={() => resolveRecovery(false)}>使用服务器状态重新编辑</button>
              <button type="button" className="button secondary" disabled={pending} onClick={() => resolveRecovery(true)}>保留草稿，以服务器状态继续编辑</button>
            </div>
          </>}
        </section>}
        <form className="profile-form" aria-label="编辑职业档案" aria-busy={pending} aria-describedby={error ? 'profile-error' : undefined} onSubmit={save}>
          <h2>{recovery ? '当前草稿（尚未确认保存）' : '编辑档案'}</h2>
          <div className="field">
            <label htmlFor="career-summary">职业概述</label>
            <textarea id="career-summary" rows={6} value={editor.draft.summary} disabled={pending}
              onChange={(event) => edit({ summary: event.target.value })} aria-describedby="summary-help summary-count" />
            <p id="summary-help" className="field-help">描述你的职业背景、经验概述与职业方向。最多 2000 个 Unicode 字符，最终校验以服务器为准。</p>
            <p id="summary-count" className="field-help">规范化后字符数：{summaryCount} / 2000</p>
          </div>
          <StringListEditor id="profile-skills" label="技能" values={editor.draft.skills} entry={skillEntry}
            onEntryChange={(value) => { setSkillEntry(value); setSuccess(null) }} onChange={(skills) => edit({ skills })} maxItems={100} maxLength={80} disabled={pending} />
          <StringListEditor id="profile-roles" label="目标岗位" values={editor.draft.roles} entry={roleEntry}
            onEntryChange={(value) => { setRoleEntry(value); setSuccess(null) }} onChange={(roles) => edit({ roles })} maxItems={10} maxLength={120} disabled={pending} />
          <div aria-live="assertive" aria-atomic="true">{error && <p className="notice error" id="profile-error">{error}</p>}</div>
          <div aria-live="polite" aria-atomic="true">{success && <p className="notice success">{success}</p>}</div>
          {hasEntry && <p className="field-help">列表输入尚未加入草稿，保存前请添加或清空。</p>}
          <button type="submit" className="button primary" disabled={pending || recovery !== null || (!!editor.canonical && !dirty && !hasEntry)}>
            {pending ? '正在处理…' : editor.canonical ? '保存档案' : '创建档案'}
          </button>
        </form>
      </>}
    </main>
  </div>
}
