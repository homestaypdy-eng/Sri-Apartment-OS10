import { useEffect, useState } from 'react'
import { fetchBusinessContact, FALLBACK_BUSINESS_CONTACT, type BusinessContact } from '../lib/businessContact'

/**
 * Live business contact info (phone/WhatsApp/email/address), sourced from
 * OS10 Business Settings — the single place an admin edits this. Starts
 * with the last-known-good fallback so contact links/text are never blank
 * during the initial fetch, then swaps to the live value once it resolves.
 */
export function useBusinessContact(): BusinessContact {
  const [contact, setContact] = useState<BusinessContact>(FALLBACK_BUSINESS_CONTACT)

  useEffect(() => {
    let cancelled = false
    fetchBusinessContact().then((c) => {
      if (!cancelled) setContact(c)
    })
    return () => {
      cancelled = true
    }
  }, [])

  return contact
}
