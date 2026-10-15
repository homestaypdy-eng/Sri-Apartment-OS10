import { useState } from 'react'
import { Helmet } from '../components/Helmet'
import { ResponsiveImage } from '../components/ResponsiveImage'
import { Lightbox } from '../components/Lightbox'
import { AsyncBoundary } from '../components/AsyncBoundary'
import { useAsync } from '../hooks/useAsync'
import { useUnitPhotos } from '../hooks/useUnitPhotos'
import { getPublicUnits } from '../lib/api'
import type { PublicUnitOut } from '../lib/types'

export function GalleryPage() {
  const state = useAsync(getPublicUnits, [])

  return (
    <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
      <Helmet
        title="Gallery — Sri Apartment Homestay Pondicherry"
        description="Photos of our accommodations in Moolakulam, Puducherry."
        canonicalPath="/gallery"
      />
      <h1 className="text-center font-serif text-4xl text-brand-green">Gallery</h1>
      <div className="mt-10">
        <AsyncBoundary state={state} retry={state.status === 'error' ? state.retry : () => {}} loadingLabel="Loading photos…">
          {(units) => <GalleryGrid units={units} />}
        </AsyncBoundary>
      </div>
    </div>
  )
}

function GalleryGrid({ units }: { units: PublicUnitOut[] }) {
  const photosByUnit = useUnitPhotos(units)
  const allPhotos = units.flatMap((u) => photosByUnit[u.id] ?? [])
  const [openIndex, setOpenIndex] = useState<number | null>(null)

  return (
    <>
      <div className="columns-2 gap-3 sm:columns-3">
        {allPhotos.map((p, i) => (
          <button
            key={p.src}
            type="button"
            onClick={() => setOpenIndex(i)}
            className="mb-3 block w-full focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-gold"
            aria-label={`Open photo: ${p.alt}`}
          >
            <ResponsiveImage src={p.src} alt={p.alt} width={400} height={300} className="w-full rounded object-cover" />
          </button>
        ))}
      </div>

      {openIndex !== null && <Lightbox photos={allPhotos} index={openIndex} onClose={() => setOpenIndex(null)} onNavigate={setOpenIndex} />}
    </>
  )
}
