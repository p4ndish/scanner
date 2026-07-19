import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { Play, Settings2, ChevronDown, ChevronUp, Server, Globe, Cpu, MemoryStick } from 'lucide-react'

const ALL_PROVIDERS = [
  'aws', 'google_cloud', 'microsoft_azure', 'oracle_cloud', 'digitalocean',
  'akamai_linode', 'vultr', 'cloudflare', 'ibm_cloud',
  'alibaba_cloud', 'tencent_cloud', 'huawei_cloud', 'baidu_cloud',
  'ucloud', 'kingsoft_cloud', 'volcengine', 'jd_cloud',
  'china_telecom_cloud', 'china_unicom_cloud', 'china_unicom_residential', 'china_mobile_cloud',
  'naver_cloud', 'sakura_internet', 'kt_cloud',
  'ovh_cloud', 'hetzner', 'scaleway', 'ionos',
]

const PRESETS = {
  'Known LLM ports': ['11434', '8080', '8000', '1234', '5000', '5001', '7860', '8888', '3001'],
  'Known opencode ports': ['4096', '3000', '8080'],
  'Web defaults': ['80', '443', '3000', '8080', '8443'],
  'Full sweep': ['1-65535'],
}

export default function NewScan() {
  const navigate = useNavigate()

  // Scan type: 'cloud' | 'single_ip'
  const [scanType, setScanType] = useState('cloud')

  // Common fields
  const [name, setName] = useState('')
  const [llmMode, setLlmMode] = useState(true)
  const [ports, setPorts] = useState(PRESETS['Known LLM ports'])
  const [customPorts, setCustomPorts] = useState('')
  const [fullSweep, setFullSweep] = useState('')
  const [rate, setRate] = useState(5000)
  const [workers, setWorkers] = useState(8)
  const [parallel, setParallel] = useState(4)
  const [retry, setRetry] = useState(1)
  const [scoreThreshold, setScoreThreshold] = useState(5)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // Cloud-specific
  const [selectedProviders, setSelectedProviders] = useState([])

  // Single-IP specific
  const [targetIp, setTargetIp] = useState('')

  // System recommendations
  const [sysInfo, setSysInfo] = useState(null)

  // Remote machines
  const [machines, setMachines] = useState([])
  const [selectedMachineId, setSelectedMachineId] = useState(null) // null = local

  useEffect(() => {
    api.get('/system/info')
      .then((info) => {
        setSysInfo(info)
        // Auto-fill defaults based on scan type
        const rec = info.recommendations?.single_ip
        if (rec) {
          setRate(rec.rate)
          setWorkers(rec.workers)
          setParallel(rec.parallel)
        }
      })
      .catch(() => {
        // ignore
      })
    api.get('/machines').then(setMachines).catch(() => {})
  }, [])

  // Update defaults when scan type changes
  useEffect(() => {
    if (!sysInfo) return
    if (scanType === 'single_ip') {
      const rec = sysInfo.recommendations?.single_ip
      if (rec) {
        setRate(rec.rate)
        setWorkers(rec.workers)
        setParallel(rec.parallel)
      }
      setFullSweep('') // default no full sweep for single IP unless user wants
    } else {
      const rec = sysInfo.recommendations?.cloud
      if (rec) {
        setRate(rec.rate)
        setWorkers(rec.workers)
        setParallel(rec.parallel)
      }
      setFullSweep('3000-65535')
    }
  }, [scanType, sysInfo])

  function toggleProvider(p) {
    setSelectedProviders((prev) =>
      prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p]
    )
  }

  function applyPreset(key) {
    setPorts(PRESETS[key])
    setCustomPorts('')
  }

  function applyRecommendation(type) {
    if (!sysInfo) return
    const rec = sysInfo.recommendations?.[type]
    if (!rec) return
    setRate(rec.rate)
    setWorkers(rec.workers)
    setParallel(rec.parallel)
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')

    if (scanType === 'cloud' && selectedProviders.length === 0) {
      setError('Select at least one provider')
      return
    }
    if (scanType === 'single_ip' && !targetIp.trim()) {
      setError('Enter a target IP address')
      return
    }
    if (scanType === 'single_ip') {
      // Basic IP validation
      const ipRegex = /^(\d{1,3}\.){3}\d{1,3}$/
      if (!ipRegex.test(targetIp.trim())) {
        setError('Enter a valid IPv4 address')
        return
      }
    }

    setLoading(true)
    try {
      const finalPorts = customPorts.trim()
        ? customPorts.split(',').map((p) => p.trim())
        : ports

      const payload = {
        name: name || (scanType === 'single_ip' ? `Scan ${targetIp}` : `${selectedProviders.join(', ')} scan`),
        providers: scanType === 'cloud' ? selectedProviders : [],
        target_ip: scanType === 'single_ip' ? targetIp.trim() : null,
        ports: finalPorts,
        llm_mode: llmMode,
        rate,
        workers,
        parallel,
        retry,
        score_threshold: scoreThreshold,
        full_sweep: fullSweep.trim() || null,
        machine_id: scanType === 'cloud' && selectedMachineId ? Number(selectedMachineId) : null,
      }

      const res = await api.post('/scans', payload)
      navigate(`/scans/${res.id}`)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="text-2xl font-bold mb-6">New Scan</h1>

      <form onSubmit={handleSubmit} className="space-y-6">
        {error && (
          <div className="bg-red-500/10 border border-red-500/20 text-red-400 px-4 py-3 rounded-lg text-sm">
            {error}
          </div>
        )}

        {/* Scan Type Toggle */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
          <label className="block text-sm font-medium text-slate-300 mb-3">Scan target</label>
          <div className="grid grid-cols-2 gap-3">
            <button
              type="button"
              onClick={() => setScanType('cloud')}
              className={`flex items-center justify-center px-4 py-3 rounded-lg border text-sm font-medium transition-colors ${
                scanType === 'cloud'
                  ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                  : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-slate-200'
              }`}
            >
              <Globe className="w-4 h-4 mr-2" />
              Cloud Providers
            </button>
            <button
              type="button"
              onClick={() => setScanType('single_ip')}
              className={`flex items-center justify-center px-4 py-3 rounded-lg border text-sm font-medium transition-colors ${
                scanType === 'single_ip'
                  ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                  : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-slate-200'
              }`}
            >
              <Server className="w-4 h-4 mr-2" />
              Single IP
            </button>
          </div>
        </div>

        {/* Run-on machine selector (cloud scans only — CLI has no single-IP mode) */}
        {scanType === 'cloud' && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
            <label className="block text-sm font-medium text-slate-300 mb-1">Run on</label>
            <p className="text-xs text-slate-500 mb-3">
              Local = run on this server. Pick a remote machine to scan from a different source IP.
            </p>
            <select
              value={selectedMachineId || ''}
              onChange={(e) => setSelectedMachineId(e.target.value ? Number(e.target.value) : null)}
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
            >
              <option value="">Local machine (this server)</option>
              {machines.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name} ({m.username}@{m.host}){m.last_test_ok === false ? ' — untested/failed' : ''}
                </option>
              ))}
            </select>
            {machines.length === 0 && (
              <p className="text-xs text-slate-600 mt-2">
                No remote machines registered. Add one under the Machines page.
              </p>
            )}
          </div>
        )}

        {/* Name */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
          <label className="block text-sm font-medium text-slate-300 mb-1">Scan name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={scanType === 'single_ip' ? 'e.g. Target audit' : 'e.g. Tencent Cloud LLM hunt'}
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50 focus:border-emerald-500"
          />
        </div>

        {/* Single IP Input */}
        {scanType === 'single_ip' && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
            <label className="block text-sm font-medium text-slate-300 mb-1">Target IP address</label>
            <input
              type="text"
              value={targetIp}
              onChange={(e) => setTargetIp(e.target.value)}
              placeholder="e.g. 192.168.1.1"
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50 focus:border-emerald-500"
            />
            <p className="text-xs text-slate-500 mt-1">
              Scan a single IPv4 address. Full sweep and fingerprinting are both enabled.
            </p>
          </div>
        )}

        {/* Providers */}
        {scanType === 'cloud' && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
            <label className="block text-sm font-medium text-slate-300 mb-3">Providers</label>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {ALL_PROVIDERS.map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => toggleProvider(p)}
                  className={`px-3 py-2 rounded-lg text-sm font-medium border transition-colors text-left ${
                    selectedProviders.includes(p)
                      ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                      : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <span className="capitalize">{p.replace('_', ' ')}</span>
                </button>
              ))}
            </div>
            {selectedProviders.length > 0 && (
              <div className="mt-3 text-xs text-slate-500">
                {selectedProviders.length} selected
              </div>
            )}
          </div>
        )}

        {/* Mode & Ports */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 space-y-4">
          <div className="flex items-center justify-between">
            <label className="text-sm font-medium text-slate-300">LLM mode</label>
            <button
              type="button"
              onClick={() => setLlmMode((v) => !v)}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                llmMode ? 'bg-emerald-600' : 'bg-slate-700'
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  llmMode ? 'translate-x-6' : 'translate-x-1'
                }`}
              />
            </button>
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-300 mb-2">Port preset</label>
            <div className="flex flex-wrap gap-2">
              {Object.keys(PRESETS).map((key) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => applyPreset(key)}
                  className="px-3 py-1.5 rounded-md text-xs font-medium bg-slate-800 border border-slate-700 text-slate-400 hover:text-slate-200 hover:border-slate-600 transition-colors"
                >
                  {key}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1">Ports (comma-separated)</label>
            <input
              type="text"
              value={customPorts || ports.join(',')}
              onChange={(e) => setCustomPorts(e.target.value)}
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50 focus:border-emerald-500"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1">Full sweep range (optional)</label>
            <input
              type="text"
              value={fullSweep}
              onChange={(e) => setFullSweep(e.target.value)}
              placeholder={scanType === 'single_ip' ? 'e.g. 1-65535 or leave empty' : 'e.g. 3000-65535 or leave empty'}
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50 focus:border-emerald-500"
            />
            <p className="text-xs text-slate-500 mt-1">
              After fingerprinting known ports, sweep this range on confirmed IPs only.
            </p>
          </div>
        </div>

        {/* System Info & Recommendations */}
        {sysInfo && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
            <div className="flex items-center mb-3">
              <Cpu className="w-4 h-4 text-emerald-400 mr-2" />
              <span className="text-sm font-medium text-slate-300">System resources</span>
            </div>
            <div className="flex flex-wrap gap-4 text-xs text-slate-400 mb-4">
              <span className="flex items-center">
                <Cpu className="w-3 h-3 mr-1" /> {sysInfo.cpu_count} cores
              </span>
              <span className="flex items-center">
                <MemoryStick className="w-3 h-3 mr-1" /> {sysInfo.memory_gb} GB RAM
              </span>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <button
                type="button"
                onClick={() => applyRecommendation(scanType)}
                className="px-3 py-2 rounded-lg text-xs font-medium bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/20 transition-colors text-left"
              >
                <div className="font-semibold mb-0.5">Use recommended settings for {scanType === 'single_ip' ? 'single IP' : 'cloud'}</div>
                <div className="opacity-80">
                  Rate {sysInfo.recommendations?.[scanType]?.rate?.toLocaleString()}/s · {sysInfo.recommendations?.[scanType]?.workers} workers · {sysInfo.recommendations?.[scanType]?.parallel} parallel
                </div>
              </button>
            </div>
          </div>
        )}

        {/* Advanced */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
          <button
            type="button"
            onClick={() => setAdvancedOpen((v) => !v)}
            className="w-full px-6 py-4 flex items-center justify-between text-left hover:bg-slate-800/50 transition-colors"
          >
            <span className="flex items-center text-sm font-medium text-slate-300">
              <Settings2 className="w-4 h-4 mr-2" />
              Advanced options
            </span>
            {advancedOpen ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
          </button>
          {advancedOpen && (
            <div className="px-6 pb-6 grid grid-cols-1 sm:grid-cols-2 gap-4 border-t border-slate-800 pt-4">
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Rate (pkt/s per instance)</label>
                <input type="number" value={rate} onChange={(e) => setRate(Number(e.target.value))} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Parallel instances</label>
                <input type="number" value={parallel} onChange={(e) => setParallel(Number(e.target.value))} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Port workers</label>
                <input type="number" value={workers} onChange={(e) => setWorkers(Number(e.target.value))} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Score threshold</label>
                <input type="number" value={scoreThreshold} onChange={(e) => setScoreThreshold(Number(e.target.value))} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">
                  Retries on empty batches <span className="text-slate-600">(lower = faster on sparse ranges like AWS)</span>
                </label>
                <input type="number" min={0} max={3} value={retry} onChange={(e) => setRetry(Number(e.target.value))} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm" />
              </div>
            </div>
          )}
        </div>

        <button
          type="submit"
          disabled={loading}
          className="w-full bg-emerald-600 hover:bg-emerald-500 text-white font-medium py-3 rounded-lg transition-colors disabled:opacity-50 flex items-center justify-center"
        >
          <Play className="w-4 h-4 mr-2" />
          {loading ? 'Starting scan...' : 'Start Scan'}
        </button>
      </form>
    </div>
  )
}
