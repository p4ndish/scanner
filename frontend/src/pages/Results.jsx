import { useEffect, useState, useRef } from 'react'
import { api } from '../lib/api'
import { useToast } from '../lib/toast'
import { Download, Filter, Upload, Brain, Code2, Zap, ChevronDown, ChevronRight, ShieldCheck, Trash2, AlertTriangle } from 'lucide-react'

const SERVICE_COLORS = {
  ollama: 'bg-purple-500/10 text-purple-400 border-purple-500/30',
  vllm_compat: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
  vllm: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
  llamacpp: 'bg-blue-500/10 text-blue-400 border-blue-500/30',
  kobold: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
  textgen: 'bg-pink-500/10 text-pink-400 border-pink-500/30',
  lm_studio: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30',
  anythingllm: 'bg-orange-500/10 text-orange-400 border-orange-500/30',
  openwebui: 'bg-indigo-500/10 text-indigo-400 border-indigo-500/30',
  opencode: 'bg-blue-500/10 text-blue-400 border-blue-500/30',
  automatic1111: 'bg-rose-500/10 text-rose-400 border-rose-500/30',
  comfyui: 'bg-teal-500/10 text-teal-400 border-teal-500/30',
  invokeai: 'bg-fuchsia-500/10 text-fuchsia-400 border-fuchsia-500/30',
  fooocus: 'bg-lime-500/10 text-lime-400 border-lime-500/30',
  coqui_tts: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/30',
  bark_tts: 'bg-orange-500/10 text-orange-400 border-orange-500/30',
  piper_tts: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
  generic: 'bg-slate-700 text-slate-400 border-slate-600',
  unknown: 'bg-slate-800 text-slate-500 border-slate-700',
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

const PER_PAGE_OPTIONS = [10, 25, 50, 100]

export default function Results() {
  const { toast, confirm: toastConfirm } = useToast()
  const [matches, setMatches] = useState([])
  const [pagination, setPagination] = useState({ total: 0, page: 1, per_page: 25, pages: 0 })
  const [filters, setFilters] = useState({ provider: '', service: '', min_score: '', max_score: '', model: '', llm_mode: '', verified_status: '' })
  const [providers, setProviders] = useState([])
  const [loading, setLoading] = useState(true)
  const [importing, setImporting] = useState(false)
  const [importStatus, setImportStatus] = useState('')
  const [verifying, setVerifying] = useState(false)
  const fileInputRef = useRef(null)

  // Bulk delete dialog state
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false)
  const [bulkDeleteFilters, setBulkDeleteFilters] = useState({
    scope: 'all',
    provider: '',
    service: '',
    verified_status: '',
  })
  const [bulkDeleteCount, setBulkDeleteCount] = useState(null)

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
  ]

  // Per-row expanded state: { [matchId]: true }
  const [expandedRows, setExpandedRows] = useState(new Set())

  // Per-row test state: { [matchId]: { models, selectedModel, testPrompt, testResponse, testLoading, modelLoading } }
  const [rowTestState, setRowTestState] = useState({})

  async function loadProviders() {
    try {
      const res = await api.get('/matches/providers')
      setProviders(res.providers || [])
    } catch (e) {
      console.error(e)
    }
  }

  async function load(page = pagination.page, perPage = pagination.per_page) {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (filters.provider) params.set('provider', filters.provider)
      if (filters.service) params.set('service', filters.service)
      if (filters.min_score) params.set('min_score', filters.min_score)
      if (filters.max_score) params.set('max_score', filters.max_score)
      if (filters.llm_mode !== '') params.set('llm_mode', filters.llm_mode)
      if (filters.verified_status) params.set('verified_status', filters.verified_status)
      if (filters.model.trim()) params.set('model', filters.model.trim())
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
      setLoading(false)
    }
  }

  useEffect(() => {
    loadProviders()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    load(1, pagination.per_page)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters])

  function goToPage(page) {
    if (page < 1 || page > pagination.pages) return
    load(page, pagination.per_page)
  }

  function changePerPage(perPage) {
    load(1, perPage)
  }

  function toggleRow(matchId) {
    setExpandedRows((prev) => {
      const next = new Set(prev)
      if (next.has(matchId)) {
        next.delete(matchId)
      } else {
        next.add(matchId)
        // Auto-fetch models when expanding
        const match = matches.find((m) => m.id === matchId)
        if (match && match.scan_job?.llm_mode && !rowTestState[matchId]?.models) {
          fetchModelsForRow(match)
        }
      }
      return next
    })
  }

  async function fetchModelsForRow(match) {
    const id = match.id
    setRowTestState((prev) => ({
      ...prev,
      [id]: { ...prev[id], modelLoading: true },
    }))
    try {
      const res = await api.get(`/matches/${id}/models`)
      const models = res.models || []
      setRowTestState((prev) => ({
        ...prev,
        [id]: {
          ...prev[id],
          models,
          selectedModel: models.length === 1 ? models[0].id : (prev[id]?.selectedModel || ''),
          modelLoading: false,
        },
      }))
    } catch (e) {
      setRowTestState((prev) => ({
        ...prev,
        [id]: { ...prev[id], modelLoading: false },
      }))
    }
  }

  async function runTestForRow(matchId) {
    const state = rowTestState[matchId]
    if (!state?.selectedModel) return
    const match = matches.find((m) => m.id === matchId)
    if (!match) return

    setRowTestState((prev) => ({
      ...prev,
      [matchId]: { ...prev[matchId], testLoading: true, testResponse: null, testError: null },
    }))
    try {
      const res = await api.post(`/matches/${matchId}/test`, {
        model: state.selectedModel,
        prompt: state.testPrompt || 'reply with h3ll0',
        max_tokens: 100,
      })
      setRowTestState((prev) => ({
        ...prev,
        [matchId]: { ...prev[matchId], testResponse: res, testLoading: false },
      }))
    } catch (e) {
      setRowTestState((prev) => ({
        ...prev,
        [matchId]: { ...prev[matchId], testLoading: false, testError: e.message },
      }))
    }
  }

  function updateRowState(matchId, updates) {
    setRowTestState((prev) => ({
      ...prev,
      [matchId]: { ...(prev[matchId] || {}), ...updates },
    }))
  }

  function exportCSV() {
    const rows = [
      ['IP', 'Port', 'Scheme', 'Service', 'Provider', 'Region', 'Score', 'Methods'].join(','),
      ...matches.map((m) =>
        [m.ip, m.port, m.scheme, m.service, m.provider, m.region, m.score, m.methods_hit?.join(';')].join(',')
      ),
    ]
    const blob = new Blob([rows.join('\n')], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'matches.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  function exportJSON() {
    const blob = new Blob([JSON.stringify(matches, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'matches.json'
    a.click()
    URL.revokeObjectURL(url)
  }

  async function importCli(file) {
    if (!file) return
    const sizeMB = (file.size / (1024 * 1024)).toFixed(1)
    setImporting(true)
    setImportStatus(`Uploading ${sizeMB} MB...`)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const res = await fetch('/api/matches/import', {
        method: 'POST',
        headers: { Authorization: `Bearer ${localStorage.getItem('token')}` },
        body: formData,
      })
      setImportStatus('Processing...')
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Import failed')
      toast(`Imported ${data.imported.toLocaleString()} matches from CLI results!`)
      load(1, pagination.per_page)
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setImporting(false)
      setImportStatus('')
    }
  }

  function handleFileChange(e) {
    const file = e.target.files[0]
    if (file) importCli(file)
    e.target.value = ''
  }

  async function startVerification() {
    setVerifying(true)
    try {
      const res = await api.post('/matches/verify', {
        provider: filters.provider || undefined,
        service: filters.service || undefined,
        verified_status: 'pending',
      })
      toast(`Verification queued for ${res.total.toLocaleString()} matches`)
    } catch (e) {
      toast('Failed to start verification: ' + e.message, 'error')
    } finally {
      setVerifying(false)
    }
  }

  async function previewBulkDelete() {
    const payload = {}
    if (bulkDeleteFilters.scope === 'provider') {
      if (!bulkDeleteFilters.provider) return
      payload.provider = bulkDeleteFilters.provider
    } else if (bulkDeleteFilters.scope === 'service') {
      if (!bulkDeleteFilters.service) return
      payload.service = bulkDeleteFilters.service
    } else if (bulkDeleteFilters.scope === 'verified_status') {
      if (!bulkDeleteFilters.verified_status) return
      payload.verified_status = bulkDeleteFilters.verified_status
    }
    try {
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
  }, [bulkDeleteOpen, bulkDeleteFilters])

  async function executeBulkDelete() {
    const payload = {}
    if (bulkDeleteFilters.scope === 'provider') {
      payload.provider = bulkDeleteFilters.provider
    } else if (bulkDeleteFilters.scope === 'service') {
      payload.service = bulkDeleteFilters.service
    } else if (bulkDeleteFilters.scope === 'verified_status') {
      payload.verified_status = bulkDeleteFilters.verified_status
    }
    try {
      await api.delete('/matches/bulk', payload)
      setBulkDeleteOpen(false)
      load(1, pagination.per_page)
    } catch (e) {
      toast('Delete failed: ' + e.message, 'error')
    }
  }

  function getModelTags(match) {
    const details = match.details_json || {}
    const tags = []
    if (details.ollama_version) tags.push(`Ollama ${details.ollama_version}`)
    if (details.openai_models?.model_count) tags.push(`${details.openai_models.model_count} models`)
    if (details.openai_model_id?.length) tags.push(...details.openai_model_id.slice(0, 2))
    if (details.kobold_model) tags.push('Kobold')
    if (details.openwebui_version) tags.push(`WebUI ${details.openwebui_version}`)
    if (details.anythingllm) tags.push('AnythingLLM')
    if (details.llamacpp_props) tags.push('llama.cpp')
    return tags
  }

  const startItem = (pagination.page - 1) * pagination.per_page + 1
  const endItem = Math.min(pagination.page * pagination.per_page, pagination.total)

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Results</h1>
          <p className="text-slate-400 text-sm mt-1">
            {pagination.total.toLocaleString()} total matches
            {pagination.total > 0 && (
              <span className="ml-2 text-slate-500">
                Showing {startItem}-{endItem}
              </span>
            )}
          </p>
        </div>
        <div className="flex gap-2">
          <input type="file" accept=".json" ref={fileInputRef} onChange={handleFileChange} className="hidden" />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={importing}
            className="inline-flex items-center px-3 py-2 bg-emerald-600 hover:bg-emerald-500 border border-emerald-500 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
          >
            <Upload className="w-4 h-4 mr-2" />
            {importing ? (importStatus || 'Importing...') : 'Import CLI Results'}
          </button>
          <button onClick={exportCSV} className="inline-flex items-center px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm font-medium hover:bg-slate-700 transition-colors">
            <Download className="w-4 h-4 mr-2" /> CSV
          </button>
          <button onClick={exportJSON} className="inline-flex items-center px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm font-medium hover:bg-slate-700 transition-colors">
            <Download className="w-4 h-4 mr-2" /> JSON
          </button>
          <button
            onClick={startVerification}
            disabled={verifying}
            className="inline-flex items-center px-3 py-2 bg-amber-600 hover:bg-amber-500 border border-amber-500 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
          >
            {verifying ? (
              <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white mr-2" />
            ) : (
              <ShieldCheck className="w-4 h-4 mr-2" />
            )}
            Verify Pending
          </button>
          <button
            onClick={() => setBulkDeleteOpen(true)}
            className="inline-flex items-center px-3 py-2 bg-rose-600 hover:bg-rose-500 border border-rose-500 rounded-lg text-sm font-medium transition-colors"
          >
            <Trash2 className="w-4 h-4 mr-2" />
            Bulk Delete
          </button>
        </div>
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
                  <option value="honeypot">Honeypot</option>
                  <option value="unreachable">Unreachable</option>
                </select>
              )}
            </div>

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
          <select
            value={filters.provider}
            onChange={(e) => setFilters((f) => ({ ...f, provider: e.target.value }))}
            className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
          >
            <option value="">All providers</option>
            {providers.map((p) => (
              <option key={p} value={p === 'unknown' ? '' : p}>{p}</option>
            ))}
          </select>
          <select
            value={filters.service}
            onChange={(e) => setFilters((f) => ({ ...f, service: e.target.value }))}
            className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
          >
            {SERVICE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
          <select
            value={filters.llm_mode}
            onChange={(e) => setFilters((f) => ({ ...f, llm_mode: e.target.value }))}
            className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
          >
            <option value="">All modes</option>
            <option value="false">OpenCode</option>
            <option value="true">LLM</option>
          </select>
          <select
            value={filters.verified_status}
            onChange={(e) => setFilters((f) => ({ ...f, verified_status: e.target.value }))}
            className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
          >
            <option value="">All statuses</option>
            <option value="pending">Pending</option>
            <option value="legitimate">Legitimate</option>
            <option value="honeypot">Honeypot</option>
            <option value="unreachable">Unreachable</option>
          </select>
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500">Score</span>
            <input
              type="number" placeholder="Min" value={filters.min_score}
              onChange={(e) => setFilters((f) => ({ ...f, min_score: e.target.value }))}
              className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm w-20 focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
            />
            <span className="text-slate-600">-</span>
            <input
              type="number" placeholder="Max" value={filters.max_score}
              onChange={(e) => setFilters((f) => ({ ...f, max_score: e.target.value }))}
              className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm w-20 focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
            />
          </div>
          <input
            type="text" placeholder="Filter by model name..."
            value={filters.model}
            onChange={(e) => setFilters((f) => ({ ...f, model: e.target.value }))}
            className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
          />
        </div>
      </div>

      {/* Table */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="bg-slate-800/50 text-slate-400 text-xs uppercase">
              <tr>
                <th className="px-4 py-3 w-8"></th>
                <th className="px-6 py-3">IP:Port</th>
                <th className="px-6 py-3">Mode</th>
                <th className="px-6 py-3">Service</th>
                <th className="px-6 py-3">Models / Tags</th>
                <th className="px-6 py-3">Provider</th>
                <th className="px-6 py-3">Score</th>
                <th className="px-6 py-3">Verified</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {loading && (
                <tr>
                  <td colSpan={8} className="px-6 py-8 text-center">
                    <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-emerald-400 mx-auto" />
                  </td>
                </tr>
              )}
              {!loading && matches.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-6 py-8 text-center text-slate-500">
                    No matches found.
                    <br />
                    <span className="text-xs">Run a scan or import CLI results.</span>
                  </td>
                </tr>
              )}
              {matches.map((m) => {
                const tags = getModelTags(m)
                const isExpanded = expandedRows.has(m.id)
                const tState = rowTestState[m.id] || {}
                return (
                  <>
                    <tr
                      key={m.id}
                      onClick={() => m.scan_job?.llm_mode && toggleRow(m.id)}
                      className={`hover:bg-slate-800/30 transition-colors ${m.scan_job?.llm_mode ? 'cursor-pointer' : ''}`}
                    >
                      <td className="px-4 py-3">
                        {m.scan_job?.llm_mode && (
                          isExpanded ? (
                            <ChevronDown className="w-4 h-4 text-slate-500" />
                          ) : (
                            <ChevronRight className="w-4 h-4 text-slate-500" />
                          )
                        )}
                      </td>
                      <td className="px-6 py-3 font-mono text-emerald-400">
                        {m.ip}:{m.port}
                      </td>
                      <td className="px-6 py-3">
                        {m.scan_job?.llm_mode ? (
                          <span className="inline-flex items-center text-xs bg-purple-500/10 text-purple-400 px-1.5 py-0.5 rounded">
                            <Brain className="w-3 h-3 mr-1" /> LLM
                          </span>
                        ) : (
                          <span className="inline-flex items-center text-xs bg-blue-500/10 text-blue-400 px-1.5 py-0.5 rounded">
                            <Code2 className="w-3 h-3 mr-1" /> opencode
                          </span>
                        )}
                      </td>
                      <td className="px-6 py-3">
                        <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${SERVICE_COLORS[m.service] || SERVICE_COLORS.unknown}`}>
                          {m.service}
                        </span>
                      </td>
                      <td className="px-6 py-3">
                        <div className="flex flex-wrap gap-1">
                          {tags.slice(0, 3).map((t, i) => (
                            <span key={i} className="text-xs bg-slate-800 text-slate-400 px-1.5 py-0.5 rounded">{t}</span>
                          ))}
                          {tags.length > 3 && <span className="text-xs text-slate-600">+{tags.length - 3}</span>}
                        </div>
                      </td>
                      <td className="px-6 py-3 text-slate-400 capitalize">{m.provider?.replace('_', ' ') || '—'}</td>
                      <td className="px-6 py-3">
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-emerald-500/10 text-emerald-400">
                          {m.score}
                        </span>
                      </td>
                      <td className="px-6 py-3">
                        {m.verified_status === 'legitimate' ? (
                          <span className="inline-flex items-center text-xs bg-emerald-500/10 text-emerald-400 px-1.5 py-0.5 rounded">
                            Legitimate
                          </span>
                        ) : m.verified_status === 'honeypot' ? (
                          <span className="inline-flex items-center text-xs bg-rose-500/10 text-rose-400 px-1.5 py-0.5 rounded">
                            Honeypot
                          </span>
                        ) : m.verified_status === 'unreachable' ? (
                          <span className="inline-flex items-center text-xs bg-slate-700 text-slate-400 px-1.5 py-0.5 rounded">
                            Unreachable
                          </span>
                        ) : (
                          <span className="inline-flex items-center text-xs bg-amber-500/10 text-amber-400 px-1.5 py-0.5 rounded">
                            Pending
                          </span>
                        )}
                      </td>
                    </tr>

                    {/* Expanded test panel */}
                    {isExpanded && m.scan_job?.llm_mode && (
                      <tr key={`${m.id}-expanded`}>
                        <td colSpan={8} className="px-0 py-0">
                          <div className="bg-slate-950/50 border-y border-slate-800/50 px-6 py-5 space-y-4">
                            {/* Models */}
                            <div>
                              <label className="block text-sm font-medium text-slate-300 mb-2">Select Model</label>
                              {tState.modelLoading ? (
                                <div className="flex items-center text-sm text-slate-500">
                                  <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-emerald-400 mr-2" />
                                  Fetching models...
                                </div>
                              ) : !tState.models || tState.models.length === 0 ? (
                                <p className="text-sm text-slate-500 mb-2">No models discovered. Enter manually:</p>
                              ) : (
                                <div className="flex flex-wrap gap-2">
                                  {tState.models.map((model) => (
                                    <button
                                      key={model.id}
                                      onClick={(e) => {
                                        e.stopPropagation()
                                        updateRowState(m.id, { selectedModel: model.id })
                                      }}
                                      className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                                        tState.selectedModel === model.id
                                          ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                                          : 'bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-750'
                                      }`}
                                    >
                                      <div>{model.name || model.id}</div>
                                      {model.quantization_level && (
                                        <div className="text-[10px] text-slate-500">{model.quantization_level}</div>
                                      )}
                                    </button>
                                  ))}
                                </div>
                              )}
                              {(!tState.models || tState.models.length === 0) && (
                                <input
                                  type="text"
                                  value={tState.selectedModel || ''}
                                  onChange={(e) => updateRowState(m.id, { selectedModel: e.target.value })}
                                  onClick={(e) => e.stopPropagation()}
                                  placeholder="e.g. llama3:latest"
                                  className="mt-2 w-full max-w-xs bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
                                />
                              )}
                            </div>

                            {/* Prompt */}
                            <div className="flex gap-3 items-end">
                              <div className="flex-1">
                                <label className="block text-sm font-medium text-slate-300 mb-2">Prompt</label>
                                <textarea
                                  value={tState.testPrompt || 'reply with h3ll0'}
                                  onChange={(e) => updateRowState(m.id, { testPrompt: e.target.value })}
                                  onClick={(e) => e.stopPropagation()}
                                  rows={2}
                                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50 resize-none"
                                />
                              </div>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation()
                                  runTestForRow(m.id)
                                }}
                                disabled={!tState.selectedModel || tState.testLoading}
                                className="bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-medium py-2.5 px-5 rounded-lg transition-colors flex items-center shrink-0"
                              >
                                {tState.testLoading ? (
                                  <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white" />
                                ) : (
                                  <>
                                    <Zap className="w-4 h-4 mr-2" /> Test
                                  </>
                                )}
                              </button>
                            </div>

                            {/* Response */}
                            {tState.testResponse && (
                              <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 space-y-2">
                                <div className="flex items-center justify-between">
                                  <span className="text-xs font-medium text-emerald-400">Response</span>
                                  {tState.testResponse.total_duration_ms && (
                                    <span className="text-xs text-slate-500">{tState.testResponse.total_duration_ms.toFixed(0)}ms</span>
                                  )}
                                </div>
                                <p className="text-sm text-slate-300 whitespace-pre-wrap">{tState.testResponse.response}</p>
                                {tState.testResponse.prompt_eval_count !== undefined && (
                                  <div className="text-xs text-slate-500 pt-2 border-t border-slate-800">
                                    Prompt eval: {tState.testResponse.prompt_eval_count} tokens · Response: {tState.testResponse.eval_count} tokens
                                  </div>
                                )}
                                {tState.testResponse.prompt_tokens !== undefined && (
                                  <div className="text-xs text-slate-500 pt-2 border-t border-slate-800">
                                    Prompt tokens: {tState.testResponse.prompt_tokens} · Completion: {tState.testResponse.completion_tokens}
                                  </div>
                                )}
                              </div>
                            )}

                            {/* Error */}
                            {tState.testError && (
                              <div className="bg-rose-500/10 border border-rose-500/30 rounded-lg p-4">
                                <div className="flex items-center gap-2 mb-1">
                                  <span className="text-xs font-medium text-rose-400">Test Failed</span>
                                </div>
                                <p className="text-sm text-rose-300">{tState.testError}</p>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
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
                onClick={() => goToPage(pagination.page - 1)}
                disabled={pagination.page <= 1}
                className="p-2 rounded-lg bg-slate-800 border border-slate-700 text-slate-400 hover:text-slate-200 hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                <ChevronDown className="w-4 h-4 rotate-90" />
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
                <ChevronDown className="w-4 h-4 -rotate-90" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
