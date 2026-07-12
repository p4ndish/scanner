import { NavLink, useLocation } from 'react-router-dom'
import { useAuth } from '../lib/auth'
import {
  LayoutDashboard,
  PlusCircle,
  List,
  Search,
  LogOut,
  Radar,
  ShieldCheck,
} from 'lucide-react'

const nav = [
  { path: '/', label: 'Dashboard', icon: LayoutDashboard },
  { path: '/scans/new', label: 'New Scan', icon: PlusCircle },
  { path: '/scans', label: 'Scans', icon: List },
  { path: '/results', label: 'Results', icon: Search },
  { path: '/verification', label: 'Verification', icon: ShieldCheck },
]

export default function Layout({ children }) {
  const { logout, user } = useAuth()
  const location = useLocation()

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className="w-64 bg-slate-900 border-r border-slate-800 flex flex-col">
        <div className="h-16 flex items-center px-6 border-b border-slate-800">
          <Radar className="w-6 h-6 text-emerald-400 mr-3" />
          <span className="font-bold text-lg tracking-tight">OpenCode Scanner</span>
        </div>

        <nav className="flex-1 px-3 py-4 space-y-1">
          {nav.map((item) => {
            const Icon = item.icon
            const active = location.pathname === item.path
            return (
              <NavLink
                key={item.path}
                to={item.path}
                className={`flex items-center px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                  active
                    ? 'bg-emerald-500/10 text-emerald-400'
                    : 'text-slate-400 hover:text-slate-100 hover:bg-slate-800'
                }`}
              >
                <Icon className="w-5 h-5 mr-3" />
                {item.label}
              </NavLink>
            )
          })}
        </nav>

        <div className="p-4 border-t border-slate-800">
          <div className="flex items-center justify-between">
            <div className="text-sm text-slate-400 truncate max-w-[140px]">
              {user?.username || 'User'}
            </div>
            <button
              onClick={logout}
              className="p-2 text-slate-400 hover:text-red-400 hover:bg-slate-800 rounded-lg transition-colors"
              title="Logout"
            >
              <LogOut className="w-4 h-4" />
            </button>
          </div>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-auto bg-slate-950">
        <div className="max-w-7xl mx-auto px-6 py-8">{children}</div>
      </main>
    </div>
  )
}
