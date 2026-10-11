import { useAuth } from '../auth/AuthContext'
import { Brand } from '../components/AuthLayout'
import { Link } from 'react-router'

const upcoming = [
  ['02', '简历', '整理职业经历，在这里持续完善你的简历。'],
  ['03', '收藏岗位', '收集值得探索的岗位，不错过心仪的机会。'],
  ['04', '岗位分析', '分析岗位匹配度和技能差距，找到有依据的行动方向。'],
]

export function WorkspacePage() {
  const { logout, currentUser } = useAuth()
  return (
    <div className="site-shell workspace-shell">
      <header className="site-header">
        <Brand />
        <div className="workspace-actions">
          <span className="session-badge"><span className="accent-dot" /> 已登录</span>
          <button type="button" className="button secondary" onClick={() => { void logout() }}>
            退出登录
          </button>
        </div>
      </header>
      <main className="workspace-main">
        <section className="workspace-welcome" aria-labelledby="page-title">
          <p className="eyebrow">你的职业工作台</p>
          <h1 id="page-title" tabIndex={-1}>在这里，<br /><span>开启下一程。</span></h1>
          <p className="story-description">
            欢迎来到 Career Copilot。从职业档案开始，梳理经历、优势与目标，规划你的下一步。
          </p>
          <p className="workspace-status">账号状态：{currentUser?.status === 'active' ? '正常' : '—'}</p>
        </section>
        <section className="upcoming-section" aria-labelledby="upcoming-title">
          <div className="section-heading">
            <h2 id="upcoming-title">职业工具</h2>
            <span>从职业档案开始</span>
          </div>
          <div className="upcoming-grid">
            <article className="upcoming-card profile-feature-card">
              <div className="card-topline"><span className="card-number">01</span><span className="coming-label">已开放</span></div>
              <h3>职业档案</h3>
              <p>梳理你的经历、优势与目标，为下一步打好基础。</p>
              <Link className="button secondary" to="/app/profile">查看职业档案</Link>
            </article>
            {upcoming.map(([number, title, description]) => (
              <article className="upcoming-card" key={title}>
                <div className="card-topline">
                  <span className="card-number">{number}</span>
                  <span className="coming-label">即将推出</span>
                </div>
                <h3>{title}</h3>
                <p>{description}</p>
              </article>
            ))}
          </div>
        </section>
      </main>
      <footer className="site-footer">
        <span>用心打磨工具，陪你稳步前行。</span>
        <span>Career Copilot · 持续构建中</span>
      </footer>
    </div>
  )
}
