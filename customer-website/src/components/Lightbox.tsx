import { useEffect, useRef } from 'react'

export interface LightboxPhoto {
  src: string
  alt: string
}

/**
 * The one and only overlay/dialog component in this app (see the
 * WEBSITE-02 popup audit — everything else is either a page or a plain
 * floating button, never a competing modal). Used by GalleryPage and
 * ApartmentDetailPage so there's a single lightbox implementation, not
 * two similar-but-different ones.
 */
export function Lightbox({
  photos,
  index,
  onClose,
  onNavigate,
}: {
  photos: LightboxPhoto[]
  index: number
  onClose: () => void
  onNavigate: (nextIndex: number) => void
}) {
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const touchStartX = useRef<number | null>(null)
  const photo = photos[index]

  // Minimal swipe-to-navigate for touch devices — a fixed 40px threshold is
  // enough to distinguish an intentional horizontal swipe from a tap or a
  // vertical scroll attempt, without pulling in a gesture library for a
  // single left/right case.
  const onTouchStart = (e: React.TouchEvent) => {
    touchStartX.current = e.touches[0].clientX
  }
  const onTouchEnd = (e: React.TouchEvent) => {
    if (touchStartX.current === null) return
    const delta = e.changedTouches[0].clientX - touchStartX.current
    touchStartX.current = null
    if (Math.abs(delta) < 40) return
    if (delta < 0) onNavigate((index + 1) % photos.length)
    else onNavigate((index - 1 + photos.length) % photos.length)
  }

  useEffect(() => {
    // Capture the trigger BEFORE moving focus to the close button — doing
    // it after would just capture the close button itself, leaving close-
    // restoration a no-op (confirmed live: focus was landing on <body>).
    const previouslyFocused = document.activeElement as HTMLElement | null
    closeButtonRef.current?.focus()

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key === 'ArrowRight') onNavigate((index + 1) % photos.length)
      if (e.key === 'ArrowLeft') onNavigate((index - 1 + photos.length) % photos.length)
      if (e.key === 'Tab') {
        // Simple focus trap: this dialog only ever contains the three
        // buttons below, so cycling between them is enough — no need for
        // a general-purpose focus-trap utility for a 3-control dialog.
        e.preventDefault()
        const focusables = [closeButtonRef.current].filter(Boolean) as HTMLElement[]
        focusables[0]?.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    document.body.style.overflow = 'hidden'

    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = ''
      previouslyFocused?.focus()
    }
  }, [index, photos.length, onClose, onNavigate])

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Photo ${index + 1} of ${photos.length}: ${photo.alt}`}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
    >
      <button
        ref={closeButtonRef}
        type="button"
        onClick={onClose}
        aria-label="Close"
        className="absolute top-4 right-4 flex h-10 w-10 items-center justify-center rounded-full bg-white/10 text-xl text-white hover:bg-white/20"
      >
        ✕
      </button>

      <button
        type="button"
        onClick={() => onNavigate((index - 1 + photos.length) % photos.length)}
        aria-label="Previous photo"
        className="absolute left-2 top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-full bg-white/10 text-2xl text-white hover:bg-white/20 sm:left-6"
      >
        ‹
      </button>

      <img src={photo.src} alt={photo.alt} className="max-h-[85vh] max-w-full rounded object-contain" />

      <button
        type="button"
        onClick={() => onNavigate((index + 1) % photos.length)}
        aria-label="Next photo"
        className="absolute right-2 top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-full bg-white/10 text-2xl text-white hover:bg-white/20 sm:right-6"
      >
        ›
      </button>

      <div className="absolute bottom-4 left-1/2 max-w-[90vw] -translate-x-1/2 text-center">
        <p className="text-xs text-white/80">{photo.alt}</p>
        <p className="mt-1 text-xs text-white/50">
          {index + 1} / {photos.length}
        </p>
      </div>
    </div>
  )
}
