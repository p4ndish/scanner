import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useToast } from '../lib/toast'
import { Server, Plus, Trash2, X, CheckCircle, XCircle, KeyRound } from 'lucide-react'

export default function Machines() {
  const { toast, confirm: toastConfirm } = useToast()
  const [machines, setMachines] = useState([])
  const [loading, setLoading] = useState(true)
  const [showDialog, setShowDialog] = useState(false)
  const [testingId, setTestingId] = useState(null)

  // form state
  const [name, setName] = useState('')
  const [host, setHost] = useState('')
  const [port, setPort] = useState(22)
  const [username, setUsername] = useState('root')
  const [authType, setAuthType] = useState('key')
  const [secret, setSecret] = useState('')
  const [useSudo, setUseSudo] = useState(false)
  const [saving, setSaving] = useState(false)

  async function load() {
    setLoading(true)
    try {
      const res = await api.get('/machines')
      setMachines(res || [])
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  function resetForm() {
    setName(''); setHost(''); setPort(22); setUsername('root')
    setAuthType('key'); setSecret(''); setUseSudo(false)
  }

  async function saveMachine() {
    if (!name.trim() || !host.trim()) {
      toast('Name and host are required', 'error')
      return
    }
    if (!secret.trim()) {
      toast(authType === 'key' ? 'Private key is required' : 'Password is required', 'error')
      return
    }
    setSaving(true)
    try {
      await api.post('/machines', {
        name: name.trim(), host: host.trim(), port: Number(port),
        username: username.trim(), auth_type: authType, secret, use_sudo: useSudo,
      })
      toast('Machine added')
      setShowDialog(false)
      resetForm()
      load()
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setSaving(false)
    }
  }

  async function deleteMachine(id, name) {
    const ok = await toastConfirm(`Delete machine "${name}"?`)
    if (!ok) return
    try {
      await api.delete(`/machines/${id}`)
      toast('Machine deleted')
      load()
    } catch (e) {
      toast(e.message, 'error')
    }
  }

  async function testMachine(id) {
    setTestingId(id)
    try {
      const res = await api.post(`/machines/${id}/test`, {})
      toast(res.message, res.ok ? 'success' : 'error')
      load()
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setTestingId(null)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-emerald-400" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Scan Machines</h1>
          <p className="text-slate-400 text-sm mt-1">
            Remote SSH hosts to run scans from. Different source IPs = different vantage points.
          </p>
        </div>
        <button
          onClick={() => setShowDialog(true)}
          className="inline-flex items-center px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-sm font-medium transition-colors"
        >
          <Plus className="w-4 h-4 mr-2" />
          Add Machine
        </button>
      </div>

      {/* Table */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="bg-slate-800/50 text-slate-400 text-xs uppercase">
              <tr>
                <th className="px-6 py-3">Name</th>
                <th className="px-6 py-3">Host</th>
                <th className="px-6 py-3">Auth</th>
                <th className="px-6 py-3">Sudo</th>
                <th className="px-6 py-3">Status</th>
                <th className="px-6 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {machines.map((m) => (
                <tr key={m.id} className="hover:bg-slate-800/30 transition-colors">
                  <td className="px-6 py-4 font-medium">{m.name}</td>
                  <td className="px-6 py-4 font-mono text-slate-400">
                    {m.username}@{m.host}:{m.port}
                  </td>
                  <td className="px-6 py-4">
                    <span className="inline-flex items-center text-xs bg-slate-800 text-slate-400 px-1.5 py-0.5 rounded">
                      <KeyRound className="w-3 h-3 mr-1" />
                      {m.auth_type}
                    </span>
                  </td>
                  <td className="px-6 py-4">
                    {m.use_sudo ? (
                      <span className="text-xs text-amber-400">sudo</span>
                    ) : (
                      <span className="text-xs text-slate-600">no</span>
                    )}
                  </td>
                  <td className="px-6 py-4">
                    {m.last_test_ok === null ? (
                      <span className="text-xs text-slate-500">untested</span>
                    ) : m.last_test_ok ? (
                      <span className="inline-flex items-center text-xs bg-emerald-500/10 text-emerald-400 px-1.5 py-0.5 rounded">
                        <CheckCircle className="w-3 h-3 mr-1" /> OK
                      </span>
                    ) : (
                      <span className="inline-flex items-center text-xs bg-rose-500/10 text-rose-400 px-1.5 py-0.5 rounded" title={m.last_test_message}>
                        <XCircle className="w-3 h-3 mr-1" /> failed
                      </span>
                    )}
                    {m.last_tested_at && (
                      <span className="text-xs text-slate-600 ml-2">
                        {new Date(m.last_tested_at).toLocaleDateString()}
                      </span>
                    )}
                  </td>
                  <td className="px-6 py-4 text-right">
                    <div className="flex items-center justify-end gap-2">
                      <button
                        onClick={() => testMachine(m.id)}
                        disabled={testingId === m.id}
                        className="inline-flex items-center px-2.5 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg text-xs font-medium transition-colors disabled:opacity-50"
                      >
                        {testingId === m.id ? (
                          <div className="animate-spin rounded-full h-3.5 w-3.5 border-b-2 border-emerald-400" />
                        ) : (
                          <>Test</>
                        )}
                      </button>
                      <button
                        onClick={() => deleteMachine(m.id, m.name)}
                        className="text-slate-500 hover:text-red-400 transition-colors p-1.5"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {machines.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center">
                    <Server className="w-10 h-10 text-slate-700 mx-auto mb-3" />
                    <p className="text-slate-400">No machines yet.</p>
                    <p className="text-xs text-slate-500 mt-1">
                      Add a remote host to run scans from a different IP.
                    </p>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Add machine dialog */}
      {showDialog && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 max-w-lg w-full mx-4 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-bold">Add Scan Machine</h3>
              <button
                onClick={() => !saving && setShowDialog(false)}
                className="text-slate-500 hover:text-slate-300 transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2">
                <label className="block text-xs font-medium text-slate-400 mb-1">Name</label>
                <input
                  type="text" value={name} onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. China Unicom box"
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Host</label>
                <input
                  type="text" value={host} onChange={(e) => setHost(e.target.value)}
                  placeholder="116.136.189.12"
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Port</label>
                <input
                  type="number" value={port} onChange={(e) => setPort(e.target.value)}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Username</label>
                <input
                  type="text" value={username} onChange={(e) => setUsername(e.target.value)}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Auth type</label>
                <select
                  value={authType} onChange={(e) => setAuthType(e.target.value)}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
                >
                  <option value="key">Private key</option>
                  <option value="password">Password</option>
                </select>
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-slate-400 mb-1">
                  {authType === 'key' ? 'Private key (paste full key including BEGIN/END lines)' : 'Password'}
                </label>
                {authType === 'key' ? (
                  <textarea
                    value={secret} onChange={(e) => setSecret(e.target.value)}
                    rows={5} placeholder="-----BEGIN OPENSSH PRIVATE KEY-----&#10;..."
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
                  />
                ) : (
                  <input
                    type="password" value={secret} onChange={(e) => setSecret(e.target.value)}
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
                  />
                )}
                <p className="text-xs text-slate-500 mt-1">
                  Encrypted at rest (Fernet). Never shown again after saving.
                </p>
              </div>
              <div className="col-span-2">
                <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
                  <input
                    type="checkbox" checked={useSudo} onChange={(e) => setUseSudo(e.target.checked)}
                    className="rounded bg-slate-800 border-slate-600 text-emerald-500 focus:ring-emerald-500/50"
                  />
                  Run masscan with sudo
                  <span className="text-xs text-slate-500">(enable if the SSH user is not root and lacks raw-socket access)</span>
                </label>
              </div>
            </div>

            <div className="flex gap-3 justify-end pt-2">
              <button
                onClick={() => !saving && setShowDialog(false)}
                disabled={saving}
                className="px-4 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={saveMachine}
                disabled={saving}
                className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-lg text-sm font-medium transition-colors"
              >
                {saving ? 'Saving...' : 'Save Machine'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
