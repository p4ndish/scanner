import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { Activity, CheckCircle, Clock, ShieldAlert, Trash2 } from 'lucide-react'

export default function ScanList() {
  const [scans, setScans] = useState([])
  const [loading, setLoading] = useState(true)

  async function load() {
    setLoading(true)
    try {
      const res = await api.get('/scans')
      setScans(res)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    const iv = setInterval(load, 5000)
    return () => clearInterval(iv)
  }, [])

  async function deleteScan(id) {
    if (!confirm('Delete this scan and all its matches?')) return
    try {
      await api.delete(`/scans/${id}`)
      load()
    } catch (e) {
      alert(e.message)
    }
  }

  if (loading && scans.length === 0) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-emerald-400" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Scans</h1>
        <Link
          to="/scans/new"
          className="bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
        >
          New Scan
        </Link>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="bg-slate-800/50 text-slate-400 text-xs uppercase">
              <tr>
                <th className="px-6 py-3">Name</th>
                <th className="px-6 py-3">Target</th>
                <th className="px-6 py-3">Status</th>
                <th className="px-6 py-3">Matches</th>
                <th className="px-6 py-3">Started</th>
                <th className="px-6 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {scans.map((s) => (
                <tr key={s.id} className="hover:bg-slate-800/30 transition-colors">
                  <td className="px-6 py-4">
                    <Link to={`/scans/${s.id}`} className="font-medium hover:text-emerald-400 transition-colors">
                      {s.name}
                    </Link>
                    <div className="text-xs text-slate-500 mt-0.5">{s.llm_mode ? 'LLM mode' : 'opencode mode'}</div>
                  </td>
                  <td className="px-6 py-4 text-slate-400">
                    {s.target_ip ? (
                      <span className="font-mono text-xs">{s.target_ip}</span>
                    ) : (
                      <>
                        {s.providers?.slice(0, 2).join(', ')}
                        {s.providers?.length > 2 && ` +${s.providers.length - 2}`}
                      </>
                    )}
                  </td>
                  <td className="px-6 py-4">
                    <StatusBadge status={s.status} />
                  </td>
                  <td className="px-6 py-4">{s.match_count || 0}</td>
                  <td className="px-6 py-4 text-slate-400">
                    {s.started_at ? new Date(s.started_at).toLocaleString() : '—'}
                  </td>
                  <td className="px-6 py-4 text-right">
                    <button
                      onClick={() => deleteScan(s.id)}
                      className="text-slate-500 hover:text-red-400 transition-colors"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </td>
                </tr>
              ))}
              {scans.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-6 py-8 text-center text-slate-500">
                    No scans yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
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
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${cfg.bg} ${cfg.text}`}>
      <Icon className="w-3 h-3 mr-1" />
      {cfg.label}
    </span>
  )
}
