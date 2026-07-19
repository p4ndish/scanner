import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '../lib/api'
import Results from './Results'
import { ArrowLeft, Brain, Code2, Database } from 'lucide-react'

export default function ImportDetail() {
  const { id } = useParams()
  const [meta, setMeta] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    async function load() {
      setLoading(true)
      try {
        const res = await api.get('/matches/imports')
        const found = (res.imports || []).find((i) => String(i.id) === String(id))
        setMeta(found || null)
      } catch (e) {
        console.error(e)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [id])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-emerald-400" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <Link to="/imports" className="inline-flex items-center text-sm text-slate-400 hover:text-emerald-400 transition-colors">
        <ArrowLeft className="w-4 h-4 mr-1" />
        Back to Imports
      </Link>

      {/* Import header */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <Database className="w-6 h-6 text-emerald-400" />
              {meta ? meta.name : `Import #${id}`}
            </h1>
            <p className="text-slate-400 text-sm mt-1">
              <span className="font-mono">#{id}</span>
              {meta && (
                <>
                  {' · '}
                  {(meta.match_count || 0).toLocaleString()} hosts
                  {meta.created_at && <> · {new Date(meta.created_at).toLocaleString()}</>}
                </>
              )}
            </p>
          </div>
          {meta && (
            <div>
              {meta.llm_mode ? (
                <span className="inline-flex items-center text-xs bg-purple-500/10 text-purple-400 px-2 py-1 rounded">
                  <Brain className="w-3 h-3 mr-1" /> LLM mode
                </span>
              ) : (
                <span className="inline-flex items-center text-xs bg-blue-500/10 text-blue-400 px-2 py-1 rounded">
                  <Code2 className="w-3 h-3 mr-1" /> opencode mode
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Hosts for this import (reuses Results, locked to scanId) */}
      <Results scanId={id} />
    </div>
  )
}
