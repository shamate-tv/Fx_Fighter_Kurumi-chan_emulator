import { useStore } from '../store/useStore'

/** 重连提示：久留美发现你回来了（黑底红字警告框）。 */
export function ResumeModal() {
  const notice = useStore((s) => s.resumeNotice)
  const dismiss = useStore((s) => s.dismissResumeNotice)

  if (!notice) return null

  const minutes = Math.max(1, Math.round(notice.offline_seconds / 60))

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal resume-modal">
        <div className="modal-title">⚠ 久留美：终于连上网了...钱还在吗？</div>
        <p className="modal-line">离线 {minutes} 分钟，游戏时间已暂停。</p>
        {notice.crossed_weekend ? (
          <p className="modal-line danger-text">周末休市，度过了短暂的平静（未扣费）。</p>
        ) : (
          <p className="modal-line muted">你离开的这段时间，利息没有滚动。</p>
        )}
        <div className="modal-actions">
          <button className="primary-btn" onClick={dismiss}>
            继续
          </button>
        </div>
      </div>
    </div>
  )
}
