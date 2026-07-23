import { useEffect, useState, useCallback } from 'react'
import { api } from '../lib/api'
import { useToast } from '../lib/toast'
import { useDebouncedValue } from '../lib/useDebounce'
import {
  ShieldCheck,
  Trash2,
  RefreshCw,
  Play,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  HelpCircle,
  WifiOff,
  Filter,
  Download,
  Globe,
  Square,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from 'lucide-react'
import MultiSelect from '../components/MultiSelect'

const STATUS_COLORS = {
  pending: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
  legitimate: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
  model_listed: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30',
  honeypot: 'bg-rose-500/10 text-rose-400 border-rose-500/30',
  unreachable: 'bg-slate-700 text-slate-400 border-slate-600',
}

const STATUS_ICONS = {
  pending: HelpCircle,
  legitimate: CheckCircle2,
  model_listed: CheckCircle2,
  honeypot: XCircle,
  unreachable: WifiOff,
}

const PER_PAGE_OPTIONS = [10, 25, 50, 100]

export default function Verification() {
  const { toast, confirm: toastConfirm } = useToast()
  const [matches, setMatches] = useState([])
  const [pagination, setPagination] = useState({ total: 0, page: 1, per_page: 25, pages: 0 })
  const [filters, setFilters] = useState({ provider: '', service: '', model_type: '', verified_status: '', canary: '', math: '', consistency: '' })
  const [ipInput, setIpInput] = useState('')
  const debouncedIp = useDebouncedValue(ipInput, 400)
  const [providers, setProviders] = useState([])
  const [loading, setLoading] = useState(true)
  const [stats, setStats] = useState({ by_verified: {} })
  const [progress, setProgress] = useState(null)
  const [selectedIds, setSelectedIds] = useState(new Set())
  const [deleteConfirm, setDeleteConfirm] = useState(null)
  const [useProxy, setUseProxy] = useState(false)
  const [proxyCount, setProxyCount] = useState(0)

  useEffect(() => {
    api.get('/proxies').then((res) => setProxyCount((res || []).length)).catch(() => {})
  }, [])

  const verifiedCounts = stats.by_verified || {}
  const pendingCount = verifiedCounts.pending || 0
  const legitCount = verifiedCounts.legitimate || 0
  const honeypotCount = verifiedCounts.honeypot || 0
  const unreachableCount = verifiedCounts.unreachable || 0

  const load = useCallback(async (page = pagination.page, perPage = pagination.per_page, f = filters, ip = debouncedIp, silent = false) => {
    if (!silent) setLoading(true)
    try {
      const params = new URLSearchParams()
      if (f.provider) params.set('provider', f.provider)
      if (f.service) params.set('service', f.service)
      if (f.model_type) params.set('model_type', f.model_type)
      if (f.verified_status) params.set('verified_status', f.verified_status)
      if (ip.trim()) params.set('ip', ip.trim())
      if (f.canary) params.set('canary', f.canary)
      if (f.math) params.set('math', f.math)
      if (f.consistency) params.set('consistency', f.consistency)
      params.set('page', String(page))
      params.set('per_page', String(perPage))
      const res = await api.get(`/matches?${params.toString()}`)
      setMatches(res.items || [])
      setPagination({
        total: res.total,
        page: res.page,
        per_page: res.per_page,
        pages: res.pages,
      })
    } catch (e) {
      console.error(e)
    } finally {
      if (!silent) setLoading(false)
    }
  }, [filters, pagination.page, pagination.per_page])

  const loadProviders = useCallback(async () => {
    try {
      const res = await api.get('/matches/providers')
      setProviders(res.providers || [])
    } catch (e) {
      console.error(e)
    }
  }, [])

  const loadStats = useCallback(async () => {
    try {
      const res = await api.get('/matches/stats')
      setStats(res)
    } catch (e) {
      console.error(e)
    }
  }, [])

  const loadProgress = useCallback(async () => {
    try {
      const res = await api.get('/matches/verification-status')
      setProgress(res)
    } catch (e) {
      console.error(e)
    }
  }, [])

  useEffect(() => {
    loadProviders()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    load(1, pagination.per_page)
    loadStats()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters])

  useEffect(() => {
    loadProgress()
    const interval = setInterval(() => {
      loadProgress()
      loadStats()
    }, 3000)
    return () => clearInterval(interval)
  }, [loadProgress, loadStats])

  // Auto-refresh the table (silently) while a verify runs or just finished, so
  // re-verified statuses update live without a manual reload.
  useEffect(() => {
    const st = progress?.state
    if (st === 'running' || st === 'queued' || st === 'completed' || st === 'cancelled') {
      load(pagination.page, pagination.per_page, filters, debouncedIp, true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progress?.done, progress?.state])

  function goToPage(page) {
    if (page < 1 || page > pagination.pages) return
    load(page, pagination.per_page)
  }

  function changePerPage(perPage) {
    load(1, perPage)
  }

  function toggleSelect(id) {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleSelectAll() {
    if (selectedIds.size === matches.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(matches.map((m) => m.id)))
    }
  }

  async function startVerification() {
    try {
      const res = await api.post('/matches/verify', {
        provider: filters.provider || undefined,
        service: filters.service || undefined,
        verified_status: 'pending',
        use_proxy: useProxy,
      })
      toast(`Verification queued for ${res.total.toLocaleString()} matches`)
      loadProgress()
    } catch (e) {
      toast('Failed to start: ' + e.message, 'error')
    }
  }

  async function stopVerification() {
    try {
      await api.post('/matches/verify/cancel', {})
      toast('Stop requested — verify will halt after the current chunk')
      loadProgress()
    } catch (e) {
      toast('Failed to stop: ' + e.message, 'error')
    }
  }

  async function reverifyUnreachable() {
    try {
      const res = await api.post('/matches/reverify', { all_unreachable: true, use_proxy: useProxy })
      toast(`Re-verification queued for ${res.total.toLocaleString()} unreachable matches`)
      loadProgress()
    } catch (e) {
      toast('Failed to re-verify: ' + e.message, 'error')
    }
  }

  async function reverifyAll() {
    const ok = await toastConfirm('This will reset ALL verified matches back to pending and re-run verification with the improved logic.')
    if (!ok) return
    try {
      const res = await api.post('/matches/reverify-all', { use_proxy: useProxy })
      toast(`Re-verify all queued for ${res.total.toLocaleString()} matches`)
      loadProgress()
    } catch (e) {
      toast('Failed to re-verify all: ' + e.message, 'error')
    }
  }

  async function reverifyFiltered() {
    const ok = await toastConfirm('Re-verify matches matching current filters (canary/math/consistency)?')
    if (!ok) return
    try {
      const res = await api.post('/matches/reverify-filtered', {
        provider: filters.provider || undefined,
        service: filters.service || undefined,
        verified_status: filters.verified_status || undefined,
        canary: filters.canary || undefined,
        math: filters.math || undefined,
        consistency: filters.consistency || undefined,
        use_proxy: useProxy,
      })
      toast(`Re-verify filtered queued for ${res.total.toLocaleString()} matches`)
      loadProgress()
    } catch (e) {
      toast('Failed to re-verify filtered: ' + e.message, 'error')
    }
  }

  async function exportFiltered() {
    try {
      const params = new URLSearchParams()
      if (filters.provider) params.set('provider', filters.provider)
      if (filters.service) params.set('service', filters.service)
      if (filters.model_type) params.set('model_type', filters.model_type)
      if (filters.verified_status) params.set('verified_status', filters.verified_status)
      if (filters.ip.trim()) params.set('ip', filters.ip.trim())
      if (filters.canary) params.set('canary', filters.canary)
      if (filters.math) params.set('math', filters.math)
      if (filters.consistency) params.set('consistency', filters.consistency)
      const res = await api.get(`/matches/export?${params.toString()}`)
      const blob = new Blob([JSON.stringify(res.matches, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `matches_export_${Date.now()}.json`
      a.click()
      URL.revokeObjectURL(url)
      toast(`Exported ${res.count.toLocaleString()} matches`)
    } catch (e) {
      toast('Export failed: ' + e.message, 'error')
    }
  }

  async function deleteSelected() {
    if (selectedIds.size === 0) return
    try {
      await api.delete('/matches/bulk', { match_ids: Array.from(selectedIds) })
      setSelectedIds(new Set())
      load(1, pagination.per_page)
      loadStats()
    } catch (e) {
      toast('Delete failed: ' + e.message, 'error')
    }
  }

  const SERVICE_OPTIONS = [
    { value: '', label: 'All services' },
    { value: 'ollama', label: 'Ollama' },
    { value: 'vllm_compat', label: 'vLLM / OpenAI-compat' },
    { value: 'llamacpp', label: 'llama.cpp' },
    { value: 'kobold', label: 'Kobold' },
    { value: 'textgen', label: 'TextGen' },
    { value: 'lm_studio', label: 'LM Studio' },
    { value: 'anythingllm', label: 'AnythingLLM' },
    { value: 'openwebui', label: 'Open WebUI' },
    { value: 'opencode', label: 'OpenCode' },
    { value: 'automatic1111', label: 'Automatic1111 (SD)' },
    { value: 'comfyui', label: 'ComfyUI' },
    { value: 'invokeai', label: 'InvokeAI' },
    { value: 'fooocus', label: 'Fooocus' },
    { value: 'coqui_tts', label: 'Coqui TTS' },
    { value: 'bark_tts', label: 'Bark TTS' },
    { value: 'piper_tts', label: 'Piper TTS' },
  ]

  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false)
  const [bulkDeleteFilters, setBulkDeleteFilters] = useState({
    scope: 'all', // all | provider | service | verified_status | selected
    provider: '',
    service: '',
    verified_status: '',
  })
  const [bulkDeleteCount, setBulkDeleteCount] = useState(null)

  async function previewBulkDelete() {
    const payload = {}
    if (bulkDeleteFilters.scope === 'selected') {
      if (selectedIds.size === 0) return
      payload.match_ids = Array.from(selectedIds)
    } else if (bulkDeleteFilters.scope === 'provider') {
      if (!bulkDeleteFilters.provider) return
      payload.provider = bulkDeleteFilters.provider
    } else if (bulkDeleteFilters.scope === 'service') {
      if (!bulkDeleteFilters.service) return
      payload.service = bulkDeleteFilters.service
    } else if (bulkDeleteFilters.scope === 'verified_status') {
      if (!bulkDeleteFilters.verified_status) return
      payload.verified_status = bulkDeleteFilters.verified_status
    }
    // 'all' scope sends empty payload (deletes everything)

    try {
      // Count by running a filtered list query with per_page=1
      const params = new URLSearchParams()
      if (payload.provider) params.set('provider', payload.provider)
      if (payload.service) params.set('service', payload.service)
      if (payload.verified_status) params.set('verified_status', payload.verified_status)
      params.set('per_page', '1')
      const res = await api.get(`/matches?${params.toString()}`)
      setBulkDeleteCount(res.total)
    } catch (e) {
      console.error(e)
    }
  }

  useEffect(() => {
    if (bulkDeleteOpen) {
      previewBulkDelete()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bulkDeleteOpen, bulkDeleteFilters, selectedIds.size])

  async function executeBulkDelete() {
    const payload = {}
    if (bulkDeleteFilters.scope === 'selected') {
      payload.match_ids = Array.from(selectedIds)
    } else if (bulkDeleteFilters.scope === 'provider') {
      payload.provider = bulkDeleteFilters.provider
    } else if (bulkDeleteFilters.scope === 'service') {
      payload.service = bulkDeleteFilters.service
    } else if (bulkDeleteFilters.scope === 'verified_status') {
      payload.verified_status = bulkDeleteFilters.verified_status
    }

    try {
      await api.delete('/matches/bulk', payload)
      setBulkDeleteOpen(false)
      setSelectedIds(new Set())
      load(1, pagination.per_page)
      loadStats()
    } catch (e) {
      toast('Delete failed: ' + e.message, 'error')
    }
  }

  const isRunning = progress && progress.state === 'running'
  const progressPct = progress && progress.total > 0
    ? Math.round((progress.done / progress.total) * 100)
    : 0

  const startItem = (pagination.page - 1) * pagination.per_page + 1
  const endItem = Math.min(pagination.page * pagination.per_page, pagination.total)

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Verification</h1>
          <p className="text-slate-400 text-sm mt-1">
            Honeypot detection via 3-check LLM probing
          </p>
        </div>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center gap-2 text-amber-400 mb-1">
            <HelpCircle className="w-4 h-4" />
            <span className="text-xs font-medium uppercase tracking-wider">Pending</span>
          </div>
          <div className="text-2xl font-bold">{pendingCount.toLocaleString()}</div>
        </div>
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center gap-2 text-emerald-400 mb-1">
            <CheckCircle2 className="w-4 h-4" />
            <span className="text-xs font-medium uppercase tracking-wider">Legitimate</span>
          </div>
          <div className="text-2xl font-bold">{legitCount.toLocaleString()}</div>
        </div>
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center gap-2 text-rose-400 mb-1">
            <XCircle className="w-4 h-4" />
            <span className="text-xs font-medium uppercase tracking-wider">Honeypot</span>
          </div>
          <div className="text-2xl font-bold">{honeypotCount.toLocaleString()}</div>
        </div>
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center gap-2 text-slate-400 mb-1">
            <WifiOff className="w-4 h-4" />
            <span className="text-xs font-medium uppercase tracking-wider">Unreachable</span>
          </div>
          <div className="text-2xl font-bold">{unreachableCount.toLocaleString()}</div>
        </div>
      </div>

      {/* Progress Bar */}
      {progress && (isRunning || progress.state === 'cancelled') && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium text-slate-300 flex items-center gap-2">
              {progress.state === 'cancelled' ? 'Verification cancelled' : 'Verifying...'}
              {progress.using_proxy && (
                <span className="text-[10px] bg-cyan-500/10 text-cyan-400 px-1.5 py-0.5 rounded uppercase">via proxy</span>
              )}
            </span>
            <div className="flex items-center gap-3">
              <span className="text-sm text-slate-400">
                {progress.done.toLocaleString()} / {progress.total.toLocaleString()} ({progressPct}%)
              </span>
              {isRunning && (
                <button
                  onClick={stopVerification}
                  className="inline-flex items-center px-2.5 py-1 bg-rose-600 hover:bg-rose-500 border border-rose-500 rounded-lg text-xs font-medium transition-colors"
                >
                  <Square className="w-3 h-3 mr-1" />
                  Stop
                </button>
              )}
            </div>
          </div>
          <div className="w-full bg-slate-800 rounded-full h-2">
            <div
              className={progress.state === 'cancelled' ? 'bg-rose-500 h-2 rounded-full transition-all duration-500' : 'bg-emerald-500 h-2 rounded-full transition-all duration-500'}
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <div className="flex gap-4 mt-2 text-xs text-slate-500">
            <span className="text-emerald-400">Legitimate: {progress.legitimate || 0}</span>
            <span className="text-rose-400">Honeypot: {progress.honeypot || 0}</span>
            <span className="text-slate-400">Unreachable: {progress.unreachable || 0}</span>
          </div>
        </div>
      )}

      {/* Action Bar */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => setUseProxy((v) => !v)}
          title={proxyCount === 0 ? 'Add proxies on the Proxies page first' : 'Route verification requests through your proxy pool'}
          className={`inline-flex items-center px-3 py-2 border rounded-lg text-sm font-medium transition-colors ${
            useProxy
              ? 'bg-cyan-500/10 border-cyan-500/30 text-cyan-400'
              : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-slate-200'
          }`}
        >
          <Globe className="w-4 h-4 mr-2" />
          Use proxy {proxyCount > 0 && `(${proxyCount})`}
          {useProxy && <span className="ml-1 text-[10px] uppercase">on</span>}
        </button>
        <button
          onClick={startVerification}
          disabled={isRunning || pendingCount === 0}
          className="inline-flex items-center px-3 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed border border-emerald-500 rounded-lg text-sm font-medium transition-colors"
        >
          <Play className="w-4 h-4 mr-2" />
          Verify All Pending
        </button>
        <button
          onClick={reverifyUnreachable}
          disabled={isRunning || unreachableCount === 0}
          className="inline-flex items-center px-3 py-2 bg-slate-800 hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed border border-slate-700 rounded-lg text-sm font-medium transition-colors"
        >
          <RefreshCw className="w-4 h-4 mr-2" />
          Re-verify Unreachable
        </button>
        <button
          onClick={reverifyAll}
          disabled={isRunning}
          className="inline-flex items-center px-3 py-2 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 disabled:cursor-not-allowed border border-violet-500 rounded-lg text-sm font-medium transition-colors"
        >
          <RefreshCw className="w-4 h-4 mr-2" />
          Re-verify All
        </button>
        <button
          onClick={reverifyFiltered}
          disabled={isRunning}
          className="inline-flex items-center px-3 py-2 bg-amber-600 hover:bg-amber-500 disabled:opacity-50 disabled:cursor-not-allowed border border-amber-500 rounded-lg text-sm font-medium transition-colors"
        >
          <RefreshCw className="w-4 h-4 mr-2" />
          Re-verify Filtered
        </button>
        <button
          onClick={() => setBulkDeleteOpen(true)}
          className="inline-flex items-center px-3 py-2 bg-rose-600 hover:bg-rose-500 border border-rose-500 rounded-lg text-sm font-medium transition-colors"
        >
          <Trash2 className="w-4 h-4 mr-2" />
          Bulk Delete
        </button>
        <button
          onClick={exportFiltered}
          className="inline-flex items-center px-3 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg text-sm font-medium transition-colors"
        >
          <Download className="w-4 h-4 mr-2" />
          Export JSON
        </button>
      </div>

      {/* Bulk Delete Dialog */}
      {bulkDeleteOpen && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 max-w-lg w-full mx-4 space-y-4">
            <div className="flex items-center gap-3">
              <AlertTriangle className="w-6 h-6 text-amber-400" />
              <h3 className="text-lg font-bold">Bulk Delete</h3>
            </div>
            <p className="text-sm text-slate-400">
              Choose what to delete. This action cannot be undone.
            </p>

            {/* Scope selector */}
            <div className="space-y-3">
              <label className="block text-sm font-medium text-slate-300">Delete scope</label>
              <select
                value={bulkDeleteFilters.scope}
                onChange={(e) => setBulkDeleteFilters((f) => ({ ...f, scope: e.target.value }))}
                className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
              >
                <option value="all">All matches</option>
                <option value="provider">By provider</option>
                <option value="service">By service</option>
                <option value="verified_status">By verified status</option>
                {selectedIds.size > 0 && (
                  <option value="selected">Selected rows ({selectedIds.size})</option>
                )}
              </select>

              {bulkDeleteFilters.scope === 'provider' && (
                <select
                  value={bulkDeleteFilters.provider}
                  onChange={(e) => setBulkDeleteFilters((f) => ({ ...f, provider: e.target.value }))}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
                >
                  <option value="">Select provider...</option>
                  {providers.map((p) => (
                    <option key={p} value={p === 'unknown' ? '' : p}>{p}</option>
                  ))}
                </select>
              )}

              {bulkDeleteFilters.scope === 'service' && (
                <select
                  value={bulkDeleteFilters.service}
                  onChange={(e) => setBulkDeleteFilters((f) => ({ ...f, service: e.target.value }))}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
                >
                  <option value="">Select service...</option>
                  {SERVICE_OPTIONS.filter((o) => o.value).map((opt) => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                  ))}
                </select>
              )}

              {bulkDeleteFilters.scope === 'verified_status' && (
                <select
                  value={bulkDeleteFilters.verified_status}
                  onChange={(e) => setBulkDeleteFilters((f) => ({ ...f, verified_status: e.target.value }))}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
                >
                  <option value="">Select status...</option>
                  <option value="pending">Pending</option>
                  <option value="legitimate">Legitimate</option>
                  <option value="model_listed">Model listed</option>
                  <option value="honeypot">Honeypot</option>
                  <option value="unreachable">Unreachable</option>
                </select>
              )}
            </div>

            {/* Count preview */}
            {bulkDeleteCount !== null && (
              <div className="bg-rose-500/10 border border-rose-500/30 rounded-lg p-3">
                <p className="text-sm text-rose-400 font-medium">
                  {bulkDeleteCount.toLocaleString()} matches will be deleted
                </p>
              </div>
            )}

            <div className="flex gap-3 justify-end pt-2">
              <button
                onClick={() => setBulkDeleteOpen(false)}
                className="px-4 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg text-sm font-medium transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={executeBulkDelete}
                disabled={bulkDeleteCount === 0 || bulkDeleteCount === null}
                className="px-4 py-2 bg-rose-600 hover:bg-rose-500 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors"
              >
                Delete {bulkDeleteCount !== null ? bulkDeleteCount.toLocaleString() : ''} Matches
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-3">
        <div className="flex flex-wrap gap-3 items-center">
          <Filter className="w-4 h-4 text-slate-500 shrink-0" />
          <MultiSelect
            value={filters.provider}
            onChange={(v) => setFilters((f) => ({ ...f, provider: v }))}
            options={providers.filter((p) => p && p !== 'unknown').map((p) => ({ value: p, label: p }))}
            placeholder="All providers"
            allLabel="All providers"
            className="w-44"
          />
          <MultiSelect
            value={filters.service}
            onChange={(v) => setFilters((f) => ({ ...f, service: v }))}
            options={SERVICE_OPTIONS.filter((o) => o.value).map((o) => ({ value: o.value, label: o.label }))}
            placeholder="All services"
            allLabel="All services"
            className="w-44"
          />
          <MultiSelect
            value={filters.model_type}
            onChange={(v) => setFilters((f) => ({ ...f, model_type: v }))}
            options={[
              { value: 'chat', label: 'Chat / LLM' },
              { value: 'image', label: 'Image' },
              { value: 'audio', label: 'Audio / TTS' },
              { value: 'video', label: 'Video' },
              { value: 'embeddings', label: 'Embeddings' },
            ]}
            placeholder="All model types"
            allLabel="All model types"
            className="w-44"
          />
          <MultiSelect
            value={filters.verified_status}
            onChange={(v) => setFilters((f) => ({ ...f, verified_status: v }))}
            options={[
              { value: 'pending', label: 'Pending' },
              { value: 'legitimate', label: 'Legitimate' },
              { value: 'model_listed', label: 'Model listed' },
              { value: 'honeypot', label: 'Honeypot' },
              { value: 'unreachable', label: 'Unreachable' },
            ]}
            placeholder="All statuses"
            allLabel="All statuses"
            className="w-44"
          />
          <input
            type="text" placeholder="Search IP or IP:Port..."
            value={filters.ip}
            onChange={(e) => setFilters((f) => ({ ...f, ip: e.target.value }))}
            className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm w-44 focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
          />
          <select
            value={filters.canary}
            onChange={(e) => setFilters((f) => ({ ...f, canary: e.target.value }))}
            className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
          >
            <option value="">Canary: All</option>
            <option value="pass">Canary: Pass</option>
            <option value="fail">Canary: Fail</option>
          </select>
          <select
            value={filters.math}
            onChange={(e) => setFilters((f) => ({ ...f, math: e.target.value }))}
            className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
          >
            <option value="">Math: All</option>
            <option value="pass">Math: Pass</option>
            <option value="fail">Math: Fail</option>
          </select>
          <select
            value={filters.consistency}
            onChange={(e) => setFilters((f) => ({ ...f, consistency: e.target.value }))}
            className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
          >
            <option value="">Consist: All</option>
            <option value="pass">Consist: Pass</option>
            <option value="fail">Consist: Fail</option>
          </select>
        </div>
      </div>
      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="bg-slate-800/50 text-slate-400 text-xs uppercase">
              <tr>
                <th className="px-4 py-3">
                  <input
                    type="checkbox"
                    checked={matches.length > 0 && selectedIds.size === matches.length}
                    onChange={toggleSelectAll}
                    className="rounded border-slate-600 bg-slate-800 text-emerald-500 focus:ring-emerald-500/50"
                  />
                </th>
                <th className="px-6 py-3">IP:Port</th>
                <th className="px-6 py-3">Service</th>
                <th className="px-6 py-3">Provider</th>
                <th className="px-6 py-3">Score</th>
                <th className="px-6 py-3">Status</th>
                <th className="px-6 py-3">Details</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {loading && (
                <tr>
                  <td colSpan={7} className="px-6 py-8 text-center">
                    <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-emerald-400 mx-auto" />
                  </td>
                </tr>
              )}
              {!loading && matches.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-6 py-8 text-center text-slate-500">
                    No matches found.
                  </td>
                </tr>
              )}
              {matches.map((m) => {
                const StatusIcon = STATUS_ICONS[m.verified_status] || HelpCircle
                const details = m.verification_details || {}
                return (
                  <tr key={m.id} className="hover:bg-slate-800/30 transition-colors">
                    <td className="px-4 py-3">
                      <input
                        type="checkbox"
                        checked={selectedIds.has(m.id)}
                        onChange={() => toggleSelect(m.id)}
                        className="rounded border-slate-600 bg-slate-800 text-emerald-500 focus:ring-emerald-500/50"
                      />
                    </td>
                    <td className="px-6 py-3 font-mono text-emerald-400">
                      {m.ip}:{m.port}
                    </td>
                    <td className="px-6 py-3">
                      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border bg-slate-800 text-slate-300 border-slate-700">
                        {m.service}
                      </span>
                    </td>
                    <td className="px-6 py-3 text-slate-400 capitalize">{m.provider?.replace('_', ' ') || '—'}</td>
                    <td className="px-6 py-3">
                      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-emerald-500/10 text-emerald-400">
                        {m.score}
                      </span>
                    </td>
                    <td className="px-6 py-3">
                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium border ${STATUS_COLORS[m.verified_status] || STATUS_COLORS.pending}`}>
                        <StatusIcon className="w-3 h-3" />
                        {m.verified_status}
                      </span>
                    </td>
                    <td className="px-6 py-3">
                      {details.responses && (
                        <div className="text-xs text-slate-500 space-y-0.5">
                          <div className={details.canary_pass ? 'text-emerald-400' : 'text-rose-400'}>
                            Canary: {details.canary_pass ? 'Pass' : 'Fail'}
                          </div>
                          <div className={details.math_pass ? 'text-emerald-400' : 'text-rose-400'}>
                            Math: {details.math_pass ? 'Pass' : 'Fail'}
                          </div>
                          <div className={details.consistency_pass ? 'text-emerald-400' : 'text-rose-400'}>
                            Consistency: {details.consistency_pass ? 'Pass' : 'Fail'}
                          </div>
                        </div>
                      )}
                      {!details.responses && (
                        <span className="text-xs text-slate-600">Not checked</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {pagination.pages > 1 && (
          <div className="px-6 py-4 border-t border-slate-800 flex items-center justify-between">
            <div className="flex items-center gap-4">
              <span className="text-sm text-slate-400">
                Page <span className="font-medium text-slate-200">{pagination.page}</span> of {pagination.pages}
              </span>
              <select
                value={pagination.per_page}
                onChange={(e) => changePerPage(Number(e.target.value))}
                className="bg-slate-800 border border-slate-700 rounded-lg px-2 py-1 text-xs text-slate-300 focus:outline-none"
              >
                {PER_PAGE_OPTIONS.map((n) => (
                  <option key={n} value={n}>{n} / page</option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-1">
              <button
                onClick={() => goToPage(1)}
                disabled={pagination.page <= 1}
                className="p-2 rounded-lg bg-slate-800 border border-slate-700 text-slate-400 hover:text-slate-200 hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                title="First page"
              >
                <ChevronsLeft className="w-4 h-4" />
              </button>
              <button
                onClick={() => goToPage(pagination.page - 1)}
                disabled={pagination.page <= 1}
                className="p-2 rounded-lg bg-slate-800 border border-slate-700 text-slate-400 hover:text-slate-200 hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              {Array.from({ length: Math.min(5, pagination.pages) }, (_, i) => {
                let start = Math.max(1, pagination.page - 2)
                let end = Math.min(pagination.pages, start + 4)
                if (end - start < 4) start = Math.max(1, end - 4)
                const page = start + i
                if (page > pagination.pages) return null
                return (
                  <button
                    key={page}
                    onClick={() => goToPage(page)}
                    className={`min-w-[2rem] px-2 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                      page === pagination.page
                        ? 'bg-emerald-600 text-white'
                        : 'bg-slate-800 border border-slate-700 text-slate-400 hover:text-slate-200 hover:bg-slate-700'
                    }`}
                  >
                    {page}
                  </button>
                )
              })}
              <button
                onClick={() => goToPage(pagination.page + 1)}
                disabled={pagination.page >= pagination.pages}
                className="p-2 rounded-lg bg-slate-800 border border-slate-700 text-slate-400 hover:text-slate-200 hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
              <button
                onClick={() => goToPage(pagination.pages)}
                disabled={pagination.page >= pagination.pages}
                className="p-2 rounded-lg bg-slate-800 border border-slate-700 text-slate-400 hover:text-slate-200 hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                title="Last page"
              >
                <ChevronsRight className="w-4 h-4" />
              </button>
              <span className="text-xs text-slate-500 ml-2">Go to:</span>
              <input
                type="number"
                min={1}
                max={pagination.pages}
                placeholder={String(pagination.page)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    const p = parseInt(e.target.value)
                    if (p >= 1 && p <= pagination.pages) goToPage(p)
                    e.target.value = ''
                  }
                }}
                className="w-16 bg-slate-800 border border-slate-700 rounded-lg px-2 py-1 text-xs text-slate-300 focus:outline-none focus:ring-2 focus:ring-emerald-500/50 text-center"
              />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
