import type { ReactNode } from 'react'
import { Link } from 'react-router'

export function Brand() {
  return (
    <Link className="brand" to="/" aria-label="Career Copilot 首页">
      <span className="brand-mark" aria-hidden="true">c<span>c</span></span>
      <span translate="no">Career Copilot</span>
    </Link>
  )
}

export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="site-shell auth-shell">
      <header className="site-header">
        <Brand />
        <span className="header-note">看清方向，走好下一步。</span>
      </header>
      <main className="auth-main">
        <section className="product-story" aria-labelledby="product-title">
          <p className="eyebrow">
            <span className="accent-dot" /> 陪你思考职业的下一步
          </p>
          <h2 id="product-title">看清方向，<br />开启<span>下一程。</span></h2>
          <p className="story-description">
            把你的经历、目标与机会汇聚在这里，一起规划职业的下一步。
          </p>
          <div className="product-preview">
            <p className="preview-label">功能预览 <span>· 持续完善中</span></p>
            <div className="preview-grid">
              <div className="preview-card">
                <span className="preview-symbol" aria-hidden="true">◎</span>
                <h3>职业档案</h3>
                <p>梳理经历，明确方向。</p>
              </div>
              <div className="preview-card">
                <span className="preview-symbol" aria-hidden="true">▤</span>
                <h3>简历</h3>
                <p>让每一段经历都有清晰的表达。</p>
              </div>
              <div className="preview-card">
                <span className="preview-symbol" aria-hidden="true">↗</span>
                <h3>岗位匹配与技能差距</h3>
                <p>了解机会，找到值得提升的技能。</p>
              </div>
            </div>
          </div>
        </section>
        <section className="auth-card" aria-labelledby="page-title">{children}</section>
      </main>
      <footer className="site-footer">
        <span>多一份思考，让下一步更有方向。</span>
        <span>Career Copilot · 持续构建中</span>
      </footer>
    </div>
  )
}
