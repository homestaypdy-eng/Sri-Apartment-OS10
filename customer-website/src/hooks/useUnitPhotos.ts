import { useEffect, useState } from 'react'
import { resolveUnitPhotos, type ResolvedPhoto } from '../lib/unitPhotos'
import type { PublicUnitOut } from '../lib/types'

/**
 * Resolves live OS10 photos (falling back to static content only when OS10
 * genuinely has none — see resolveUnitPhotos) for every unit in the given
 * list, keyed by unit id. Shared by every page that displays unit photos —
 * Home, Apartments, Apartment Detail, Gallery, Book — so an admin's photo
 * upload in OS10 shows up consistently everywhere, not just on some pages.
 */
export function useUnitPhotos(units: PublicUnitOut[]): Record<string, ResolvedPhoto[]> {
  const [photosByUnit, setPhotosByUnit] = useState<Record<string, ResolvedPhoto[]>>({})

  useEffect(() => {
    let cancelled = false
    Promise.all(units.map(async (u) => [u.id, await resolveUnitPhotos(u)] as const)).then((entries) => {
      if (!cancelled) setPhotosByUnit(Object.fromEntries(entries))
    })
    return () => {
      cancelled = true
    }
  }, [units])

  return photosByUnit
}
