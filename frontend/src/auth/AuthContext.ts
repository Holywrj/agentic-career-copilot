import { createContext, useContext } from 'react'
import type { Credentials, CurrentUser } from '../api/auth'
import type { AuthenticatedRequest } from '../api/client'

export interface Session { accessToken: string; user: CurrentUser; expiresAt: number }
export type AuthState =
  | { status: 'anonymous'; session: null }
  | { status: 'authenticated'; session: Session }
export type AuthNotice = 'signed-out' | 'logout-unconfirmed' | 'session-ended' | null

export interface AuthContextValue {
  status: AuthState['status']
  currentUser: CurrentUser | null
  loginPending: boolean
  notice: AuthNotice
  login: (credentials: Credentials) => Promise<void>
  cancelLogin: () => void
  logout: () => Promise<void>
  requestAuthenticated: AuthenticatedRequest
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth requires AuthProvider')
  return context
}
