import { useEffect } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router'
import { useAuth } from './auth/AuthContext'
import { ProtectedRoute, PublicRoute } from './auth/RouteGuards'
import { LoginPage } from './pages/LoginPage'
import { RegisterPage } from './pages/RegisterPage'
import { WorkspacePage } from './pages/WorkspacePage'
import { NotFoundPage } from './pages/NotFoundPage'
import { CareerProfilePage } from './pages/CareerProfilePage'
import { ResumesPage } from './pages/ResumesPage'

export default function App() {
  const { status, currentUser } = useAuth()
  const { pathname } = useLocation()
  useEffect(() => {
    const titles: Record<string, string> = { '/login': '登录', '/register': '创建账号', '/app': '工作台', '/app/profile': '职业档案', '/app/resumes': '简历' }
    document.title = `${titles[pathname] ?? '页面未找到'} · Career Copilot`
    document.getElementById('page-title')?.focus()
  }, [pathname])
  return <Routes>
    <Route path="/" element={<Navigate to={status === 'authenticated' ? '/app' : '/login'} replace />} />
    <Route path="/login" element={<PublicRoute><LoginPage /></PublicRoute>} />
    <Route path="/register" element={<PublicRoute><RegisterPage /></PublicRoute>} />
    <Route path="/app" element={<ProtectedRoute><WorkspacePage /></ProtectedRoute>} />
    <Route path="/app/profile" element={<ProtectedRoute><CareerProfilePage key={currentUser?.id} /></ProtectedRoute>} />
    <Route path="/app/resumes" element={<ProtectedRoute><ResumesPage key={currentUser?.id} /></ProtectedRoute>} />
    <Route path="*" element={<NotFoundPage />} />
  </Routes>
}
