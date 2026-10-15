import { getPublicBusinessInfo } from './api'

export interface BusinessContact {
  companyName: string
  address: string
  email: string
  /** Raw digits (e.g. "918870237237"), ready for wa.me / tel: links. */
  whatsappNumber: string | null
  /** Business-wide default — a unit's own house_rules (PublicUnitOut) takes
   * priority over this when set; see ApartmentDetailPage. */
  houseRules: string | null
}

/**
 * Last-known-good values, used only when OS10 is genuinely unreachable
 * (network error) — mirrors what used to be the website's own hardcoded
 * copy, so a backend outage degrades to the previous behavior instead of
 * showing broken contact info. Not a second source of truth in normal
 * operation: OS10's Business Settings (edited via Admin → Settings) wins
 * on every successful fetch.
 */
export const FALLBACK_BUSINESS_CONTACT: BusinessContact = {
  companyName: 'Sri Apartment Homestay',
  address: '163, 2nd Cross St, MGR Nagar South Extension, Ranga Nagar, Moolakulam, Puducherry – 605010',
  email: 'homestaypdy@gmail.com',
  whatsappNumber: '918870237237',
  houseRules: null,
}

export async function fetchBusinessContact(): Promise<BusinessContact> {
  try {
    const info = await getPublicBusinessInfo()
    return {
      companyName: info.company_name,
      address: info.address,
      email: info.contact_email,
      whatsappNumber: info.whatsapp_business_number,
      houseRules: info.house_rules,
    }
  } catch {
    return FALLBACK_BUSINESS_CONTACT
  }
}
