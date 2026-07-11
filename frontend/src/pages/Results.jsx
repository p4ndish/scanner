import { useEffect, useState, useRef } from 'react'
import { api } from '../lib/api'
import { Download, Filter, Upload, Brain, Code2 } from 'lucide-react'

export default function Results() {
  const [matches, setMatches] = useState([])
  const [filters, setFilters] = useState({ provider: '', service: '', min_score: '', llm_mode: '' })
  const [loading, setLoading] = useState(true)
  const [importing, setImporting] = useState(false)
  const fileInputRef = useRef(null)

  async function load() {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (filters.provider) params.set('provider', filters.provider)
      if (filters.service) params.set('service', filters.service)
      if (filters.min_score) params.set('min_score', filters.min_score)
      if (filters.llm_mode !== '') params.set('llm_mode', filters.llm_mode)
      const res = await api.get(`/matches?${params.toString()}`)
      setMatches(res)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [filters])

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
    setImporting(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const res = await fetch('/api/matches/import', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${localStorage.getItem('token')}`,
        },
        body: formData,
      })
      const data = await res.json()
      if (!res.ok) {
        throw new Error(data.detail || 'Import failed')
      }
      alert(`Imported ${data.imported} matches from CLI results!`)
      load()
    } catch (e) {
      alert(e.message)
    } finally {
      setImporting(false)
    }
  }

  function handleFileChange(e) {
    const file = e.target.files[0]
    if (file) {
      importCli(file)
    }
    e.target.value = ''
  }

  const opencodeCount = matches.filter((m) => !m.scan_job?.llm_mode).length
  const llmCount = matches.filter((m) => m.scan_job?.llm_mode).length

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Results</h1>
          <p className="text-slate-400 text-sm mt-1">
            {matches.length.toLocaleString()} total matches
            {opencodeCount > 0 && (
              <span className="ml-2 inline-flex items-center text-xs bg-blue-500/10 text-blue-400 px-2 py-0.5 rounded">
                <Code2 className="w-3 h-3 mr-1" /> {opencodeCount} opencode
              </span>
            )}
            {llmCount > 0 && (
              <span className="ml-2 inline-flex items-center text-xs bg-purple-500/10 text-purple-400 px-2 py-0.5 rounded">
                <Brain className="w-3 h-3 mr-1" /> {llmCount} LLM
              </span>
            )}
          </p>
        </div>
        <div className="flex gap-2">
          <input
            type="file"
            accept=".json"
            ref={fileInputRef}
            onChange={handleFileChange}
            className="hidden"
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={importing}
            className="inline-flex items-center px-3 py-2 bg-emerald-600 hover:bg-emerald-500 border border-emerald-500 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
          >
            <Upload className="w-4 h-4 mr-2" />
            {importing ? 'Importing...' : 'Import CLI Results'}
          </button>
          <button
            onClick={exportCSV}
            className="inline-flex items-center px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm font-medium hover:bg-slate-700 transition-colors"
          >
            <Download className="w-4 h-4 mr-2" />
            CSV
          </button>
          <button
            onClick={exportJSON}
            className="inline-flex items-center px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm font-medium hover:bg-slate-700 transition-colors"
          >
            <Download className="w-4 h-4 mr-2" />
            JSON
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-wrap gap-3 items-center">
        <Filter className="w-4 h-4 text-slate-500" />
        <input
          type="text"
          placeholder="Provider..."
          value={filters.provider}
          onChange={(e) => setFilters((f) => ({ ...f, provider: e.target.value }))}
          className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
        />
        <input
          type="text"
          placeholder="Service..."
          value={filters.service}
          onChange={(e) => setFilters((f) => ({ ...f, service: e.target.value }))}
          className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
        />
        <input
          type="number"
          placeholder="Min score"
          value={filters.min_score}
          onChange={(e) => setFilters((f) => ({ ...f, min_score: e.target.value }))}
          className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50 w-28"
        />
        <select
          value={filters.llm_mode}
          onChange={(e) => setFilters((f) => ({ ...f, llm_mode: e.target.value }))}
          className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
        >
          <option value="">All modes</option>
          <option value="false">OpenCode</option>
          <option value="true">LLM</option>
        </select>
      </div>

      {/* Table */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="bg-slate-800/50 text-slate-400 text-xs uppercase">
              <tr>
                <th className="px-6 py-3">IP:Port</th>
                <th className="px-6 py-3">Mode</th>
                <th className="px-6 py-3">Service</th>
                <th className="px-6 py-3">Provider</th>
                <th className="px-6 py-3">Region</th>
                <th className="px-6 py-3">Score</th>
                <th className="px-6 py-3">Methods</th>
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
                    <br />
                    <span className="text-xs">Run a scan or import CLI results.</span>
                  </td>
                </tr>
              )}
              {matches.map((m) => (
                <tr key={m.id} className="hover:bg-slate-800/30 transition-colors">
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
                  <td className="px-6 py-3 capitalize">{m.service}</td>
                  <td className="px-6 py-3 text-slate-400 capitalize">{m.provider?.replace('_', ' ')}</td>
                  <td className="px-6 py-3 text-slate-400 uppercase">{m.region}</td>
                  <td className="px-6 py-3">
                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-emerald-500/10 text-emerald-400">
                      {m.score}
                    </span>
                  </td>
                  <td className="px-6 py-3 text-slate-400 text-xs max-w-xs truncate">
                    {m.methods_hit?.join(', ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
