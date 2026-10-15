import { useEffect } from 'react'

/**
 * Injects a BreadcrumbList JSON-LD block for the current page — purely
 * navigational structured data (page names + real site URLs), never
 * pricing, ratings, or anything else that could go stale or mismatch
 * visible content. Mirrors Helmet's own mount/cleanup pattern: writes one
 * <script type="application/ld+json"> tag on mount, removes it on
 * unmount so client-side navigation never leaves a previous page's
 * breadcrumb trail attached to the next page.
 */
export function BreadcrumbSchema({ items }: { items: { name: string; path: string }[] }) {
  useEffect(() => {
    const script = document.createElement('script')
    script.type = 'application/ld+json'
    script.text = JSON.stringify({
      '@context': 'https://schema.org',
      '@type': 'BreadcrumbList',
      itemListElement: items.map((item, index) => ({
        '@type': 'ListItem',
        position: index + 1,
        name: item.name,
        item: new URL(item.path, window.location.origin).toString(),
      })),
    })
    document.head.appendChild(script)
    return () => {
      script.remove()
    }
  }, [items])

  return null
}
