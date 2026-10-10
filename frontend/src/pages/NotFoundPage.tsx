import { Link } from 'react-router'
import { useAuth } from '../auth/AuthContext'
import { Brand } from '../components/AuthLayout'

export function NotFoundPage() {
  const { status } = useAuth()
  return (
    <div className="site-shell">
      <header className="site-header"><Brand /></header>
      <main className="not-found">
        <p className="eyebrow">404 · 页面未找到</p>
        <h1 id="page-title" tabIndex={-1}>这个页面不存在</h1>
        <p>回到熟悉的页面，继续你的下一步。</p>
        <Link className="button primary" to={status === 'authenticated' ? '/app' : '/login'}>
          {status === 'authenticated' ? '返回工作台' : '返回登录'}
        </Link>
      </main>
    </div>
  )
}
