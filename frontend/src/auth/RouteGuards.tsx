import type { ReactNode } from 'react'
import { Navigate } from 'react-router'
import { useAuth } from './AuthContext'

export function ProtectedRoute({ children }: { children: ReactNode }) {
  return useAuth().status === 'authenticated' ? children : <Navigate to="/login" replace />
}

export function PublicRoute({ children }: { children: ReactNode }) {
  return useAuth().status === 'anonymous' ? children : <Navigate to="/app" replace />
}
