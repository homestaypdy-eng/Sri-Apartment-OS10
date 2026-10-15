import { useEffect } from 'react'

/**
 * Minimal per-page SEO head manager — no extra dependency for something
 * this small. Sets title + the meta tags search engines and social
 * previews actually read, and restores the previous title on unmount so
 * client-side navigation doesn't leak one page's title into another.
 */
export function Helmet({
  title,
  description,
  canonicalPath,
  noindex,
}: {
  title: string
  description: string
  canonicalPath?: string
  /** Set on pages that should never appear in search results (currently
   * only the 404 page) — restores the default "index, follow" on unmount
   * so it can never leak onto whatever page the customer navigates to next. */
  noindex?: boolean
}) {
  useEffect(() => {
    const previousTitle = document.title
    document.title = title

    let robotsMeta: HTMLMetaElement | null = null
    let previousRobotsContent: string | null = null
    if (noindex) {
      robotsMeta = document.querySelector<HTMLMetaElement>('meta[name="robots"]')
      if (robotsMeta) {
        previousRobotsContent = robotsMeta.getAttribute('content')
      } else {
        robotsMeta = document.createElement('meta')
        robotsMeta.setAttribute('name', 'robots')
        document.head.appendChild(robotsMeta)
      }
      robotsMeta.setAttribute('content', 'noindex, follow')
    }

    const setMeta = (selector: string, attr: string, value: string) => {
      let el = document.querySelector<HTMLMetaElement>(selector)
      if (!el) {
        el = document.createElement('meta')
        const [, attrName, attrValue] = /\[(\w+)="([^"]+)"\]/.exec(selector) ?? []
        if (attrName && attrValue) el.setAttribute(attrName, attrValue)
        document.head.appendChild(el)
      }
      el.setAttribute(attr, value)
    }

    setMeta('meta[name="description"]', 'content', description)
    setMeta('meta[property="og:title"]', 'content', title)
    setMeta('meta[property="og:description"]', 'content', description)
    setMeta('meta[name="twitter:title"]', 'content', title)
    setMeta('meta[name="twitter:description"]', 'content', description)

    if (canonicalPath) {
      let link = document.querySelector<HTMLLinkElement>('link[rel="canonical"]')
      if (!link) {
        link = document.createElement('link')
        link.rel = 'canonical'
        document.head.appendChild(link)
      }
      link.href = new URL(canonicalPath, window.location.origin).toString()
    }

    return () => {
      document.title = previousTitle
      if (robotsMeta) {
        if (previousRobotsContent) robotsMeta.setAttribute('content', previousRobotsContent)
        else robotsMeta.remove()
      }
    }
  }, [title, description, canonicalPath, noindex])

  return null
}
