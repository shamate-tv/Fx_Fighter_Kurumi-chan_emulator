import { useStore } from '../store/useStore'

export function Toast() {
  const toast = useStore((s) => s.toast)
  const dismiss = useStore((s) => s.dismissToast)

  if (!toast) return null

  return (
    <div className="toast-layer">
      <div className={`toast ${toast.kind}`} onClick={dismiss} role="status">
        <span>{toast.kind === 'ok' ? '✓' : toast.kind === 'err' ? '✕' : 'ℹ'}</span>
        <span>{toast.text}</span>
      </div>
    </div>
  )
}
