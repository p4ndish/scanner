import { createContext, useContext, useState, useCallback } from 'react'
import { CheckCircle2, XCircle, AlertTriangle, Info, X } from 'lucide-react'

const ToastContext = createContext(null)

const COLORS = {
  success: 'bg-emerald-600',
  error: 'bg-rose-600',
  warning: 'bg-amber-600',
  info: 'bg-slate-700',
}

const ICONS = {
  success: CheckCircle2,
  error: XCircle,
  warning: AlertTriangle,
  info: Info,
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])

  const remove = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const toast = useCallback((message, type = 'success', duration = 4000) => {
    const id = Date.now() + Math.random()
    setToasts((prev) => [...prev, { id, message, type }])
    if (duration > 0) {
      setTimeout(() => remove(id), duration)
    }
    return id
  }, [remove])

  const confirm = useCallback((message) => {
    return new Promise((resolve) => {
      const id = Date.now() + Math.random()
      setToasts((prev) => [...prev, { id, message, type: 'warning', isConfirm: true, resolve }])
    })
  }, [])

  return (
    <ToastContext.Provider value={{ toast, confirm }}>
      {children}
      {/* Toast container */}
      <div className="fixed bottom-4 right-4 z-[9999] space-y-2 max-w-sm">
        {toasts.map((t) => {
          const Icon = ICONS[t.type] || Info
          if (t.isConfirm) {
            return (
              <div key={t.id} className="bg-slate-900 border border-slate-700 rounded-xl p-4 shadow-2xl animate-[slideUp_0.2s_ease-out]">
                <div className="flex items-start gap-3">
                  <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
                  <div className="flex-1">
                    <p className="text-sm text-slate-200">{t.message}</p>
                    <div className="flex gap-2 mt-3">
                      <button
                        onClick={() => { t.resolve(true); remove(t.id) }}
                        className="px-3 py-1.5 bg-rose-600 hover:bg-rose-500 text-white rounded-lg text-xs font-medium transition-colors"
                      >
                        Confirm
                      </button>
                      <button
                        onClick={() => { t.resolve(false); remove(t.id) }}
                        className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 rounded-lg text-xs font-medium transition-colors"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )
          }
          return (
            <div
              key={t.id}
              className={`${COLORS[t.type] || COLORS.info} text-white rounded-xl p-4 shadow-2xl flex items-start gap-3 animate-[slideUp_0.2s_ease-out]`}
            >
              <Icon className="w-5 h-5 shrink-0 mt-0.5" />
              <p className="text-sm flex-1">{t.message}</p>
              <button onClick={() => remove(t.id)} className="shrink-0 opacity-70 hover:opacity-100 transition-opacity">
                <X className="w-4 h-4" />
              </button>
            </div>
          )
        })}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within ToastProvider')
  return ctx
}
