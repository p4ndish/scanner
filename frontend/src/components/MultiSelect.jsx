import { useState, useRef, useEffect } from 'react'
import { ChevronDown, Check, X } from 'lucide-react'

/**
 * MultiSelect — a dropdown with checkboxes.
 *
 * Works with comma-separated strings so it drops into existing filter state
 * without changing its shape (filters.provider stays a string: "" or "aws,alibaba").
 *
 * Props:
 *   value:    comma-separated string of selected values ("" = all/none)
 *   onChange: receives the new comma-separated string
 *   options:  [{value, label}]
 *   placeholder: text when nothing selected (e.g. "All providers")
 *   allLabel:    text for the "select all / clear" row (e.g. "All providers")
 */
export function MultiSelect({ value, onChange, options, placeholder = 'All', allLabel, className = '' }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  const selected = value ? value.split(',').filter(Boolean) : []
  const selectedSet = new Set(selected)

  // close on outside click
  useEffect(() => {
    function onDoc(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  function toggle(v) {
    const next = new Set(selectedSet)
    if (next.has(v)) next.delete(v)
    else next.add(v)
    // preserve option order for a stable display
    const ordered = options.filter((o) => next.has(o.value)).map((o) => o.value)
    onChange(ordered.join(','))
  }

  function clear() {
    onChange('')
  }

  const labelFor = (v) => options.find((o) => o.value === v)?.label || v
  const summary = selected.length === 0
    ? placeholder
    : selected.length <= 2
      ? selected.map(labelFor).join(', ')
      : `${selected.length} selected`

  return (
    <div ref={ref} className={`relative ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={`flex items-center w-full bg-slate-800 border rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/50 transition-colors ${
          open ? 'border-emerald-500/50' : 'border-slate-700 hover:border-slate-600'
        }`}
      >
        <span className={`flex-1 text-left truncate ${selected.length ? 'text-slate-200' : 'text-slate-400'}`}>
          {summary}
        </span>
        {selected.length > 0 && (
          <span
            role="button"
            tabIndex={0}
            onClick={(e) => { e.stopPropagation(); clear() }}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); clear() } }}
            className="mr-1 text-slate-500 hover:text-slate-300 cursor-pointer"
            title="Clear"
          >
            <X className="w-3.5 h-3.5" />
          </span>
        )}
        <ChevronDown className={`w-4 h-4 text-slate-500 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute z-30 mt-1 min-w-full w-max max-h-72 overflow-y-auto bg-slate-800 border border-slate-700 rounded-lg shadow-xl py-1 scrollbar-thin">
          {allLabel && (
            <button
              type="button"
              onClick={clear}
              className={`flex items-center w-full px-3 py-1.5 text-sm hover:bg-slate-700/50 transition-colors ${
                selected.length === 0 ? 'text-emerald-400 font-medium' : 'text-slate-300'
              }`}
            >
              <span className="w-4 mr-2">{selected.length === 0 && <Check className="w-3.5 h-3.5" />}</span>
              {allLabel}
            </button>
          )}
          {options.map((o) => {
            const checked = selectedSet.has(o.value)
            return (
              <button
                key={o.value}
                type="button"
                onClick={() => toggle(o.value)}
                className={`flex items-center w-full px-3 py-1.5 text-sm hover:bg-slate-700/50 transition-colors text-left ${
                  checked ? 'text-emerald-400' : 'text-slate-300'
                }`}
              >
                <span className="w-4 mr-2 flex items-center justify-center">
                  {checked && <Check className="w-3.5 h-3.5" />}
                </span>
                <span className="truncate">{o.label}</span>
              </button>
            )
          })}
          {options.length === 0 && (
            <div className="px-3 py-2 text-xs text-slate-500">No options</div>
          )}
        </div>
      )}
    </div>
  )
}

export default MultiSelect
