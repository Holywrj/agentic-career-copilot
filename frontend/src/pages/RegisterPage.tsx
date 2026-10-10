import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { register } from '../api/auth'
import { ApiError, errorMessage } from '../api/client'
import { AuthLayout } from '../components/AuthLayout'

export function RegisterPage() {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const controller = useRef<AbortController | null>(null)
  const navigate = useNavigate()
  useEffect(() => () => { controller.current?.abort() }, [])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (controller.current) return
    const active = new AbortController()
    controller.current = active
    setPending(true)
    setError(null)
    const data = new FormData(event.currentTarget)
    const password = String(data.get('password'))
    // Code points rather than UTF-16 units; the backend owns final validation.
    const count = Array.from(password.normalize('NFC')).length
    if (count < 15 || Array.from(password).length > 128 || /^\s*$/u.test(password)) {
      setError('密码需包含 15–128 个字符，且不能只有空格。')
      controller.current = null
      setPending(false)
      return
    }
    try {
      await register({ username: String(data.get('username')), password }, active.signal)
      if (!active.signal.aborted) navigate('/login', { replace: true, state: { registered: true } })
    } catch (failure) {
      if (!active.signal.aborted && !(failure instanceof ApiError && failure.kind === 'cancelled')) setError(errorMessage(failure))
    } finally {
      if (!active.signal.aborted) { controller.current = null; setPending(false) }
    }
  }

  return <AuthLayout>
    <p className="eyebrow">从这里开始</p>
    <h1 id="page-title" tabIndex={-1}>开启职业的下一程</h1>
    <p className="form-intro">创建账号，拥有你的职业工作台。</p>
    <form
      aria-label="创建账号"
      aria-busy={pending}
      onSubmit={submit}
      aria-describedby={error ? 'register-error' : undefined}
    >
      <div className="field">
        <label htmlFor="username">用户名</label>
        <input
          id="username" name="username" autoComplete="username" autoCapitalize="none"
          spellCheck={false} required disabled={pending} aria-describedby="username-help"
        />
        <p className="field-help" id="username-help">
          3–32 个字符，以英文字母或数字开头，可使用英文字母、数字、下划线（_）、点（.）和短横线（-）。首尾空格会被移除，字母会转为小写。
        </p>
      </div>
      <div className="field">
        <label htmlFor="password">密码</label>
        <input
          id="password" name="password" type="password" autoComplete="new-password"
          required disabled={pending} aria-describedby="password-help"
        />
        <p className="field-help" id="password-help">
          15–128 个字符，支持中文，不能只有空格，也不能包含控制字符。空格和字母大小写会被保留。
        </p>
      </div>
      <div aria-live="assertive" aria-atomic="true">
        {error && <p className="notice error" id="register-error">{error}</p>}
      </div>
      <button className="button primary" type="submit" disabled={pending}>
        {pending ? '正在创建账号…' : '创建账号'}<span aria-hidden="true">↗</span>
      </button>
    </form>
    <p className="form-switch">已有账号？ <Link to="/login">登录</Link></p>
    <p className="session-note">账号创建成功后，请登录进入工作台。</p>
  </AuthLayout>
}
