import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useToast } from '../lib/toast'
import { Globe, Plus, Trash2, X, CheckCircle, XCircle } from 'lucide-react'

export default function Proxies() {
  const { toast, confirm: toastConfirm } = useToast()
  const [proxies, setProxies] = useState([])
  const [loading, setLoading] = useState(true)
  const [showDialog, setShowDialog] = useState(false)
  const [testingId, setTestingId] = useState(null)

  // form
  const [name, setName] = useState('')
  const [scheme, setScheme] = useState('http')
  const [host, setHost] = useState('')
  const [port, setPort] = useState(8080)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [saving, setSaving] = useState(false)

  async function load() {
    setLoading(true)
    try {
      const res = await api.get('/proxies')
      setProxies(res || [])
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
    setName(''); setScheme('http'); setHost(''); setPort(8080)
    setUsername(''); setPassword('')
  }

  async function saveProxy() {
    if (!name.trim() || !host.trim()) {
      toast('Name and host are required', 'error')
      return
    }
    setSaving(true)
    try {
      await api.post('/proxies', {
        name: name.trim(), scheme, host: host.trim(), port: Number(port),
        username: username.trim() || null, password: password || null,
      })
      toast('Proxy added')
      setShowDialog(false)
      resetForm()
      load()
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setSaving(false)
    }
  }

  async function deleteProxy(id, name) {
    const ok = await toastConfirm(`Delete proxy "${name}"?`)
    if (!ok) return
    try {
      await api.delete(`/proxies/${id}`)
      toast('Proxy deleted')
      load()
    } catch (e) {
      toast(e.message, 'error')
    }
  }

  async function testProxy(id) {
    setTestingId(id)
    try {
      const res = await api.post(`/proxies/${id}/test`, {})
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
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Proxies</h1>
          <p className="text-slate-400 text-sm mt-1">
            Verification requests route through these (round-robin) when "Use proxy" is enabled.
          </p>
        </div>
        <button
          onClick={() => setShowDialog(true)}
          className="inline-flex items-center px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-sm font-medium transition-colors"
        >
          <Plus className="w-4 h-4 mr-2" />
          Add Proxy
        </button>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="bg-slate-800/50 text-slate-400 text-xs uppercase">
              <tr>
                <th className="px-6 py-3">Name</th>
                <th className="px-6 py-3">Endpoint</th>
                <th className="px-6 py-3">Auth</th>
                <th className="px-6 py-3">Status</th>
                <th className="px-6 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {proxies.map((p) => (
                <tr key={p.id} className="hover:bg-slate-800/30 transition-colors">
                  <td className="px-6 py-4 font-medium">{p.name}</td>
                  <td className="px-6 py-4 font-mono text-slate-400">
                    {p.scheme}://{p.host}:{p.port}
                  </td>
                  <td className="px-6 py-4">
                    {p.username ? (
                      <span className="text-xs bg-slate-800 text-slate-400 px-1.5 py-0.5 rounded">{p.username}</span>
                    ) : (
                      <span className="text-xs text-slate-600">none</span>
                    )}
                  </td>
                  <td className="px-6 py-4">
                    {p.last_test_ok === null ? (
                      <span className="text-xs text-slate-500">untested</span>
                    ) : p.last_test_ok ? (
                      <span className="inline-flex items-center text-xs bg-emerald-500/10 text-emerald-400 px-1.5 py-0.5 rounded" title={p.last_test_message}>
                        <CheckCircle className="w-3 h-3 mr-1" /> {p.last_test_message}
                      </span>
                    ) : (
                      <span className="inline-flex items-center text-xs bg-rose-500/10 text-rose-400 px-1.5 py-0.5 rounded" title={p.last_test_message}>
                        <XCircle className="w-3 h-3 mr-1" /> failed
                      </span>
                    )}
                  </td>
                  <td className="px-6 py-4 text-right">
                    <div className="flex items-center justify-end gap-2">
                      <button
                        onClick={() => testProxy(p.id)}
                        disabled={testingId === p.id}
                        className="inline-flex items-center px-2.5 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg text-xs font-medium transition-colors disabled:opacity-50"
                      >
                        {testingId === p.id ? (
                          <div className="animate-spin rounded-full h-3.5 w-3.5 border-b-2 border-emerald-400" />
                        ) : 'Test'}
                      </button>
                      <button
                        onClick={() => deleteProxy(p.id, p.name)}
                        className="text-slate-500 hover:text-red-400 transition-colors p-1.5"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {proxies.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-6 py-12 text-center">
                    <Globe className="w-10 h-10 text-slate-700 mx-auto mb-3" />
                    <p className="text-slate-400">No proxies configured.</p>
                    <p className="text-xs text-slate-500 mt-1">
                      Add HTTP or SOCKS5 proxies to route verification through different IPs.
                    </p>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Add proxy dialog */}
      {showDialog && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 max-w-lg w-full mx-4 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-bold">Add Proxy</h3>
              <button onClick={() => !saving && setShowDialog(false)} className="text-slate-500 hover:text-slate-300 transition-colors">
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2">
                <label className="block text-xs font-medium text-slate-400 mb-1">Name</label>
                <input type="text" value={name} onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. US residential 1"
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50" />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Scheme</label>
                <select value={scheme} onChange={(e) => setScheme(e.target.value)}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50">
                  <option value="http">http</option>
                  <option value="https">https</option>
                  <option value="socks5">socks5</option>
                  <option value="socks5h">socks5h (remote DNS)</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Port</label>
                <input type="number" value={port} onChange={(e) => setPort(e.target.value)}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50" />
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-slate-400 mb-1">Host</label>
                <input type="text" value={host} onChange={(e) => setHost(e.target.value)}
                  placeholder="proxy.example.com or 1.2.3.4"
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50" />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Username <span className="text-slate-600">(optional)</span></label>
                <input type="text" value={username} onChange={(e) => setUsername(e.target.value)}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50" />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Password <span className="text-slate-600">(optional)</span></label>
                <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50" />
              </div>
              <p className="col-span-2 text-xs text-slate-500">Credentials encrypted at rest. Password never shown again.</p>
            </div>

            <div className="flex gap-3 justify-end pt-2">
              <button onClick={() => !saving && setShowDialog(false)} disabled={saving}
                className="px-4 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg text-sm font-medium transition-colors disabled:opacity-50">
                Cancel
              </button>
              <button onClick={saveProxy} disabled={saving}
                className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-lg text-sm font-medium transition-colors">
                {saving ? 'Saving...' : 'Save Proxy'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
