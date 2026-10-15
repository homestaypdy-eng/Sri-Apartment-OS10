import { getUnitPhotos } from './api'
import { getApartmentContent } from '../content/apartments'
import type { PublicUnitOut } from './types'

export interface ResolvedPhoto {
  src: string
  alt: string
}

/**
 * Fallback order (Phase 3 spec):
 *   1. OS10 public accommodation photos, if available
 *   2. Existing website static content for the matching accommodation
 *   3. Existing generic placeholder (caller's responsibility — an empty
 *      array here means "no photo found," not an error)
 */
export async function resolveUnitPhotos(unit: PublicUnitOut): Promise<ResolvedPhoto[]> {
  try {
    const os10Photos = await getUnitPhotos(unit.id)
    if (os10Photos.length > 0) {
      return os10Photos.map((p) => ({ src: p.url, alt: p.caption || `${unit.name} photo` }))
    }
  } catch {
    // OS10 photos unavailable (network/endpoint issue) — fall through to static content rather than showing nothing.
  }
  const content = getApartmentContent(unit.name)
  if (content && content.photos.length > 0) return content.photos
  return []
}
