import { useEffect, useState, useRef } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { useToast } from '../lib/toast'
import { Database, Upload, Brain, Code2, Trash2, X } from 'lucide-react'

export default function Imports() {
  const { toast, confirm: toastConfirm } = useToast()
  const [imports, setImports] = useState([])
  const [loading, setLoading] = useState(true)
  const [importing, setImporting] = useState(false)
  const [importStatus, setImportStatus] = useState('')
  const [importName, setImportName] = useState('')
  const [showDialog, setShowDialog] = useState(false)
  const fileInputRef = useRef(null)

  async function load() {
    setLoading(true)
    try {
      const res = await api.get('/matches/imports')
      setImports(res.imports || [])
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  async function handleFileChange(e) {
    const file = e.target.files[0]
    if (!file) return
    const sizeMB = (file.size / (1024 * 1024)).toFixed(1)
    setImporting(true)
    setImportStatus(`Uploading ${sizeMB} MB...`)
    try {
      const formData = new FormData()
      formData.append('file', file)
      if (importName.trim()) formData.append('name', importName.trim())
      const res = await fetch('/api/matches/import', {
        method: 'POST',
        headers: { Authorization: `Bearer ${localStorage.getItem('token')}` },
        body: formData,
      })
      setImportStatus('Processing...')
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Import failed')
      toast(`Imported ${data.imported.toLocaleString()} matches into a new batch`)
      setShowDialog(false)
      setImportName('')
      load()
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setImporting(false)
      setImportStatus('')
      e.target.value = ''
    }
  }

  async function deleteImport(id, name) {
    const ok = await toastConfirm(`Delete import "${name}" and all its hosts?`)
    if (!ok) return
    try {
      await api.delete(`/scans/${id}`)
      toast('Import deleted')
      load()
    } catch (e) {
      toast(e.message, 'error')
    }
  }

  const totalHosts = imports.reduce((sum, i) => sum + (i.match_count || 0), 0)

  if (loading && imports.length === 0) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-emerald-400" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <input type="file" accept=".json" ref={fileInputRef} onChange={handleFileChange} className="hidden" />

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Imports</h1>
          <p className="text-slate-400 text-sm mt-1">
            {imports.length} {imports.length === 1 ? 'batch' : 'batches'} · {totalHosts.toLocaleString()} total hosts
          </p>
        </div>
        <button
          onClick={() => setShowDialog(true)}
          className="inline-flex items-center px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-sm font-medium transition-colors"
        >
          <Upload className="w-4 h-4 mr-2" />
          New Import
        </button>
      </div>

      {/* Import dialog */}
      {showDialog && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 max-w-md w-full mx-4 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-bold">Import CLI Results</h3>
              <button
                onClick={() => !importing && setShowDialog(false)}
                className="text-slate-500 hover:text-slate-300 transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">
                Batch name <span className="text-slate-500 font-normal">(optional)</span>
              </label>
              <input
                type="text"
                value={importName}
                onChange={(e) => setImportName(e.target.value)}
                placeholder="e.g. DigitalOcean July 2026"
                className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
              />
              <p className="text-xs text-slate-500 mt-1">
                Leave blank for an auto-generated timestamp name.
              </p>
            </div>
            <div className="flex gap-3 justify-end pt-2">
              <button
                onClick={() => !importing && setShowDialog(false)}
                disabled={importing}
                className="px-4 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={importing}
                className="inline-flex items-center px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-lg text-sm font-medium transition-colors"
              >
                {importing ? (
                  <>
                    <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white mr-2" />
                    {importStatus || 'Importing...'}
                  </>
                ) : (
                  <>
                    <Upload className="w-4 h-4 mr-2" />
                    Choose File
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Table */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="bg-slate-800/50 text-slate-400 text-xs uppercase">
              <tr>
                <th className="px-6 py-3">ID</th>
                <th className="px-6 py-3">Name</th>
                <th className="px-6 py-3">Mode</th>
                <th className="px-6 py-3">Hosts</th>
                <th className="px-6 py-3">Imported</th>
                <th className="px-6 py-3">Date</th>
                <th className="px-6 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {imports.map((imp) => (
                <tr key={imp.id} className="hover:bg-slate-800/30 transition-colors">
                  <td className="px-6 py-4 font-mono text-slate-500">#{imp.id}</td>
                  <td className="px-6 py-4">
                    <Link to={`/imports/${imp.id}`} className="font-medium hover:text-emerald-400 transition-colors">
                      {imp.name}
                    </Link>
                  </td>
                  <td className="px-6 py-4">
                    {imp.llm_mode ? (
                      <span className="inline-flex items-center text-xs bg-purple-500/10 text-purple-400 px-1.5 py-0.5 rounded">
                        <Brain className="w-3 h-3 mr-1" /> LLM
                      </span>
                    ) : (
                      <span className="inline-flex items-center text-xs bg-blue-500/10 text-blue-400 px-1.5 py-0.5 rounded">
                        <Code2 className="w-3 h-3 mr-1" /> opencode
                      </span>
                    )}
                  </td>
                  <td className="px-6 py-4">
                    <span className="font-medium">{(imp.match_count || 0).toLocaleString()}</span>
                    {imp.skipped > 0 && (
                      <span className="text-xs text-slate-500 ml-1">({imp.skipped.toLocaleString()} dup)</span>
                    )}
                  </td>
                  <td className="px-6 py-4 text-slate-400">{(imp.imported || 0).toLocaleString()}</td>
                  <td className="px-6 py-4 text-slate-400">
                    {imp.created_at ? new Date(imp.created_at).toLocaleString() : '—'}
                  </td>
                  <td className="px-6 py-4 text-right">
                    <button
                      onClick={() => deleteImport(imp.id, imp.name)}
                      className="text-slate-500 hover:text-red-400 transition-colors"
                      title="Delete import"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </td>
                </tr>
              ))}
              {imports.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-6 py-12 text-center">
                    <Database className="w-10 h-10 text-slate-700 mx-auto mb-3" />
                    <p className="text-slate-400">No imports yet.</p>
                    <p className="text-xs text-slate-500 mt-1">
                      Click "New Import" to upload a CLI results.json file.
                    </p>
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
