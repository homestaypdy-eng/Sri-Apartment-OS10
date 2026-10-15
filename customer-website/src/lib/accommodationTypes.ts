/**
 * Mirrors the canonical unit_type vocabulary defined in OS10's backend
 * (backend/app/modules/properties/models.py: UNIT_TYPES) and the labels
 * used in the OS10 Admin's accommodation-type dropdown. Unit.unit_type is
 * an unconstrained string on the backend, so a value outside this list is
 * never an error — accommodationTypeLabel() below falls back to a
 * human-readable version of whatever string OS10 actually returns.
 */
export const ACCOMMODATION_TYPE_LABELS: Record<string, string> = {
  apartment: 'Apartment',
  villa: 'Villa',
  suite: 'Suite',
  room: 'Room',
  cottage: 'Cottage',
  resort_unit: 'Resort Unit',
  homestay: 'Homestay',
  other: 'Other',
}

export function accommodationTypeLabel(unitType: string): string {
  return (
    ACCOMMODATION_TYPE_LABELS[unitType] ??
    unitType
      .split('_')
      .filter(Boolean)
      .map((w) => w[0].toUpperCase() + w.slice(1))
      .join(' ')
  )
}

/** Plural form for filter-pill labels ("Apartments", "Resort Units", ...). */
export function accommodationTypeLabelPlural(unitType: string): string {
  const label = accommodationTypeLabel(unitType)
  return label.endsWith('s') ? label : `${label}s`
}
