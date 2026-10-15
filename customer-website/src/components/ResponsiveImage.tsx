/**
 * Thin wrapper enforcing the two things WEBSITE-00 flagged as missing on
 * the prototype: lazy loading and explicit dimensions (so images reserve
 * their box before they load, instead of shifting layout). `src` is a
 * Vite-resolved asset URL — a real file, never a base64 data URI.
 */
export function ResponsiveImage({
  src,
  alt,
  width,
  height,
  className,
  priority = false,
}: {
  src: string
  alt: string
  width: number
  height: number
  className?: string
  priority?: boolean
}) {
  return (
    <img
      src={src}
      alt={alt}
      width={width}
      height={height}
      loading={priority ? 'eager' : 'lazy'}
      decoding="async"
      fetchPriority={priority ? 'high' : undefined}
      className={className}
    />
  )
}
