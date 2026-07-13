import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../lib/api'
import { useToast } from '../lib/toast'
import {
  Activity,
  CheckCircle,
  Clock,
  ShieldAlert,
  Terminal,
  Square,
} from 'lucide-react'

export default function ScanDetail() {
  const { toast, confirm: toastConfirm } = useToast()
  const { id } = useParams()
  const [scan, setScan] = useState(null)
  const [logs, setLogs] = useState([])
  const logEndRef = useRef(null)

  async function loadScan() {
    try {
      const [s, l] = await Promise.all([
        api.get(`/scans/${id}`),
        api.get(`/scans/${id}/logs`),
      ])
      setScan(s)
      setLogs(l)
    } catch (e) {
      console.error(e)
    }
  }

  useEffect(() => {
    loadScan()
    const iv = setInterval(loadScan, 5000)
    return () => clearInterval(iv)
  }, [id])

  // SSE connection for live events
  useEffect(() => {
    const token = localStorage.getItem('token')
    const es = new EventSource(`/api/scans/${id}/events?token=${encodeURIComponent(token)}`)
    es.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type === 'log') {
          loadScan()
        }
        if (msg.type === 'status' || msg.type === 'progress' || msg.type === 'done') {
          loadScan()
        }
      } catch {
        // ignore parse errors
      }
    }
    es.onerror = () => {
      // auto-reconnect
    }
    return () => es.close()
  }, [id])

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  async function cancelScan() {
    const ok = await toastConfirm('Cancel this scan?')
    if (!ok) return
    try {
      await api.post(`/scans/${id}/cancel`, {})
      loadScan()
    } catch (e) {
      toast(e.message, 'error')
    }
  }

  if (!scan) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-emerald-400" />
      </div>
    )
  }

  const canCancel = scan.status === 'queued' || scan.status === 'running'

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{scan.name}</h1>
          <p className="text-slate-400 text-sm mt-1">
            {scan.target_ip ? (
              <span className="font-mono">{scan.target_ip}</span>
            ) : (
              scan.providers?.join(', ')
            )}
            {' · '}{scan.ports?.join(', ')}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {canCancel && (
            <button
              onClick={cancelScan}
              className="inline-flex items-center px-3 py-1.5 rounded-lg text-sm font-medium bg-red-500/10 border border-red-500/30 text-red-400 hover:bg-red-500/20 transition-colors"
            >
              <Square className="w-3.5 h-3.5 mr-1.5" />
              Cancel
            </button>
          )}
          <StatusBadge status={scan.status} />
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <StatCard label="Candidates" value={scan.stats_json?.candidates_found ?? '—'} />
        <StatCard label="Matches" value={scan.stats_json?.matches_found ?? '—'} />
        <StatCard
          label="Duration"
          value={
            scan.stats_json?.scan_duration_seconds
              ? `${Math.round(scan.stats_json.scan_duration_seconds / 60)}m`
              : '—'
          }
        />
      </div>

      {/* Live terminal */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-800 flex items-center">
          <Terminal className="w-4 h-4 text-slate-400 mr-2" />
          <span className="text-sm font-medium text-slate-300">Live logs</span>
        </div>
        <div className="h-96 overflow-y-auto p-4 font-mono text-xs space-y-1 scrollbar-thin bg-slate-950">
          {logs.map((log, idx) => (
            <div key={log.id || idx} className="flex">
              <span className="text-slate-600 mr-3 shrink-0">
                {log.phase ? `[${log.phase}]` : '[info]'}
              </span>
              <span className="text-slate-300">{log.message}</span>
            </div>
          ))}
          <div ref={logEndRef} />
        </div>
      </div>

      {/* Matches */}
      {scan.matches && scan.matches.length > 0 && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
          <div className="px-6 py-4 border-b border-slate-800">
            <h2 className="font-semibold">Matches ({scan.matches.length})</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead className="bg-slate-800/50 text-slate-400 text-xs uppercase">
                <tr>
                  <th className="px-6 py-3">IP:Port</th>
                  <th className="px-6 py-3">Service</th>
                  <th className="px-6 py-3">Provider</th>
                  <th className="px-6 py-3">Score</th>
                  <th className="px-6 py-3">Methods</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {scan.matches.map((m) => (
                  <tr key={m.id} className="hover:bg-slate-800/30">
                    <td className="px-6 py-3 font-mono">
                      {m.ip}:{m.port}
                    </td>
                    <td className="px-6 py-3 capitalize">{m.service}</td>
                    <td className="px-6 py-3 text-slate-400 capitalize">{m.provider?.replace('_', ' ')}</td>
                    <td className="px-6 py-3">
                      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-emerald-500/10 text-emerald-400">
                        {m.score}
                      </span>
                    </td>
                    <td className="px-6 py-3 text-slate-400 text-xs">
                      {m.methods_hit?.join(', ')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function StatCard({ label, value }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
      <p className="text-slate-400 text-sm">{label}</p>
      <p className="text-2xl font-bold mt-1">{value}</p>
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
    <span className={`inline-flex items-center px-3 py-1.5 rounded-lg text-sm font-medium ${cfg.bg} ${cfg.text}`}>
      <Icon className="w-4 h-4 mr-2" />
      {cfg.label}
    </span>
  )
}
