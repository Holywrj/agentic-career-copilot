import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { ApiError, errorMessage } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { AuthLayout } from '../components/AuthLayout'

export function LoginPage() {
  const { login, cancelLogin, loginPending, notice } = useAuth()
  const [error, setError] = useState<string | null>(null)
  const submitting = useRef(false)
  const mounted = useRef(true)
  const navigate = useNavigate()
  const location = useLocation()
  const registered = location.state?.registered === true
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; cancelLogin() }
  }, [cancelLogin])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting.current || loginPending) return
    submitting.current = true
    setError(null)
    const data = new FormData(event.currentTarget)
    try {
      await login({ username: String(data.get('username')), password: String(data.get('password')) })
      if (mounted.current) navigate('/app', { replace: true })
    } catch (failure) {
      if (mounted.current && !(failure instanceof ApiError && failure.kind === 'cancelled')) setError(errorMessage(failure))
    } finally {
      submitting.current = false
    }
  }

  return <AuthLayout>
    <p className="eyebrow">你的个人工作台</p>
    <h1 id="page-title" tabIndex={-1}>欢迎回来</h1>
    <p className="form-intro">登录，继续探索你的职业方向。</p>
    <div className="form-notices" aria-live="polite" aria-atomic="true">
      {registered && <p className="notice success">账号创建成功，请登录以继续。</p>}
      {notice === 'signed-out' && <p className="notice">你已在当前浏览器退出登录。</p>}
      {notice === 'session-ended' && <p className="notice">登录已失效，请重新登录。</p>}
      {notice === 'logout-unconfirmed' && <p className="notice warning">已在当前浏览器退出，但暂时无法确认服务器上的登录会话是否已撤销。</p>}
    </div>
    <form
      aria-label="登录"
      aria-busy={loginPending}
      onSubmit={submit}
      aria-describedby={error ? 'login-error' : undefined}
    >
      <div className="field">
        <label htmlFor="username">用户名</label>
        <input
          id="username" name="username" autoComplete="username"
          autoCapitalize="none" spellCheck={false} required disabled={loginPending}
        />
      </div>
      <div className="field">
        <label htmlFor="password">密码</label>
        <input
          id="password" name="password" type="password"
          autoComplete="current-password" required disabled={loginPending}
        />
      </div>
      <div aria-live="assertive" aria-atomic="true">
        {error && <p className="notice error" id="login-error">{error}</p>}
      </div>
      <button className="button primary" type="submit" disabled={loginPending}>
        {loginPending ? '正在登录…' : '登录'}<span aria-hidden="true">↗</span>
      </button>
    </form>
    <p className="form-switch">还没有账号？ <Link to="/register">创建账号</Link></p>
    <p className="session-note">当前版本刷新页面或打开新标签页后，需要重新登录。</p>
  </AuthLayout>
}
