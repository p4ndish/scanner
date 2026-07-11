import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import {
  Activity,
  CheckCircle,
  Clock,
  Globe,
  Radar,
  ShieldAlert,
  Brain,
  Code2,
} from 'lucide-react'

export default function Dashboard() {
  const [stats, setStats] = useState(null)
  const [recent, setRecent] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    async function load() {
      try {
        const [scans, matchesRes] = await Promise.all([
          api.get('/scans'),
          api.get('/matches/stats'),
        ])
        setRecent(scans.slice(0, 5))
        const totalMatches = scans.reduce((sum, s) => sum + (s.match_count || 0), 0)
        const active = scans.filter((s) => s.status === 'running' || s.status === 'queued').length
        const last = scans.find((s) => s.status === 'completed')
        setStats({
          total_scans: scans.length,
          total_matches: totalMatches,
          active_scans: active,
          last_scan_at: last?.completed_at || null,
          matches_by_provider: matchesRes?.by_provider || [],
          opencode_matches: matchesRes?.by_mode?.opencode || 0,
          llm_matches: matchesRes?.by_mode?.llm || 0,
        })
      } catch (e) {
        console.error(e)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-emerald-400" />
      </div>
    )
  }

  const cards = [
    { label: 'Total Scans', value: stats?.total_scans || 0, icon: Radar, color: 'text-blue-400', bg: 'bg-blue-400/10' },
    { label: 'Total Matches', value: stats?.total_matches || 0, icon: CheckCircle, color: 'text-emerald-400', bg: 'bg-emerald-400/10' },
    { label: 'Active Scans', value: stats?.active_scans || 0, icon: Activity, color: 'text-amber-400', bg: 'bg-amber-400/10' },
    { label: 'Providers', value: stats?.matches_by_provider?.length || 0, icon: Globe, color: 'text-purple-400', bg: 'bg-purple-400/10' },
  ]

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold">Dashboard</h1>
        <p className="text-slate-400 mt-1">Overview of your scanning activity</p>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {cards.map((c) => {
          const Icon = c.icon
          return (
            <div key={c.label} className="bg-slate-900 border border-slate-800 rounded-xl p-5">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-slate-400 text-sm">{c.label}</p>
                  <p className="text-2xl font-bold mt-1">{c.value}</p>
                </div>
                <div className={`p-3 rounded-lg ${c.bg}`}>
                  <Icon className={`w-5 h-5 ${c.color}`} />
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* Mode breakdown */}
      {(stats?.opencode_matches > 0 || stats?.llm_matches > 0) && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-slate-400 text-sm">OpenCode Matches</p>
                <p className="text-2xl font-bold mt-1 text-blue-400">{stats.opencode_matches}</p>
              </div>
              <div className="p-3 rounded-lg bg-blue-400/10">
                <Code2 className="w-5 h-5 text-blue-400" />
              </div>
            </div>
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-slate-400 text-sm">LLM Matches</p>
                <p className="text-2xl font-bold mt-1 text-purple-400">{stats.llm_matches}</p>
              </div>
              <div className="p-3 rounded-lg bg-purple-400/10">
                <Brain className="w-5 h-5 text-purple-400" />
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Recent scans */}
        <div className="lg:col-span-2 bg-slate-900 border border-slate-800 rounded-xl">
          <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between">
            <h2 className="font-semibold">Recent Scans</h2>
            <Link to="/scans" className="text-sm text-emerald-400 hover:text-emerald-300">
              View all
            </Link>
          </div>
          <div className="divide-y divide-slate-800">
            {recent.length === 0 && (
              <div className="px-6 py-8 text-center text-slate-500 text-sm">
                No scans yet. <Link to="/scans/new" className="text-emerald-400">Start your first scan</Link>.
              </div>
            )}
            {recent.map((s) => (
              <div key={s.id} className="px-6 py-4 flex items-center justify-between hover:bg-slate-800/50 transition-colors">
                <div>
                  <Link to={`/scans/${s.id}`} className="font-medium hover:text-emerald-400 transition-colors">
                    {s.name}
                  </Link>
                  <div className="text-xs text-slate-500 mt-1">
                    {s.target_ip ? (
                      <span className="font-mono">{s.target_ip}</span>
                    ) : (
                      s.providers?.slice(0, 2).join(', ')
                    )}
                    {' · '}{s.match_count || 0} matches
                    {s.llm_mode && (
                      <span className="ml-2 text-purple-400">LLM mode</span>
                    )}
                  </div>
                </div>
                <StatusBadge status={s.status} />
              </div>
            ))}
          </div>
        </div>

        {/* Provider breakdown */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl">
          <div className="px-6 py-4 border-b border-slate-800">
            <h2 className="font-semibold">Matches by Provider</h2>
          </div>
          <div className="divide-y divide-slate-800">
            {(!stats?.matches_by_provider || stats.matches_by_provider.length === 0) && (
              <div className="px-6 py-8 text-center text-slate-500 text-sm">No data yet</div>
            )}
            {stats?.matches_by_provider?.map((p) => (
              <div key={p.provider} className="px-6 py-3 flex items-center justify-between">
                <span className="text-sm capitalize">{p.provider.replace('_', ' ')}</span>
                <span className="text-sm font-medium">{p.count}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function StatusBadge({ status }) {
  const map = {
    pending: { text: 'text-slate-400', bg: 'bg-slate-800', label: 'Pending', icon: Clock },
    queued: { text: 'text-blue-400', bg: 'bg-blue-400/10', label: 'Queued', icon: Clock },
    running: { text: 'text-amber-400', bg: 'bg-amber-400/10', label: 'Running', icon: Activity },
    completed: { text: 'text-emerald-400', bg: 'bg-emerald-400/10', label: 'Completed', icon: CheckCircle },
    failed: { text: 'text-red-400', bg: 'bg-red-400/10', label: 'Failed', icon: ShieldAlert },
    cancelled: { text: 'text-slate-400', bg: 'bg-slate-800', label: 'Cancelled', icon: Clock },
  }
  const cfg = map[status] || map.pending
  const Icon = cfg.icon
  return (
    <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium ${cfg.bg} ${cfg.text}`}>
      <Icon className="w-3.5 h-3.5 mr-1.5" />
      {cfg.label}
    </span>
  )
}
