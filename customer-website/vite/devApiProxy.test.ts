import { describe, expect, it } from 'vitest'
import { isAllowedDevApiRequest } from './devApiProxy.ts'

const UNIT = '231fb3ff-8e54-4c75-99ba-434c745d96a9'
const PHOTO = '00000000-0000-4000-8000-000000000000'
const IMAGE = `/api/v1/public/units/${UNIT}/photos/${PHOTO}/image`

describe('isAllowedDevApiRequest', () => {
  it.each([
    '/api/v1/public/units',
    `/api/v1/public/units/${UNIT}/photos`,
    IMAGE,
    '/api/v1/public/business-info',
    `/api/v1/public/availability?unit_id=${UNIT}&check_in=2026-11-10&check_out=2026-11-12`,
    `/api/v1/public/quote?unit_id=${UNIT}&check_in=2026-11-10&check_out=2026-11-12`,
    '/api/v1/customer-auth/oauth/status',
  ])('allows GET and HEAD %s', (url) => {
    expect(isAllowedDevApiRequest('GET', url)).toBe(true)
    expect(isAllowedDevApiRequest('HEAD', url)).toBe(true)
  })

  it('allows the public photo-image route but nothing that only resembles it', () => {
    expect(isAllowedDevApiRequest('GET', IMAGE)).toBe(true)
    expect(isAllowedDevApiRequest('head', IMAGE)).toBe(true)
    for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) expect(isAllowedDevApiRequest(method, IMAGE)).toBe(false)
    expect(isAllowedDevApiRequest('GET', `/api/v1/public/units/${UNIT}/photos/not-a-uuid/image`)).toBe(false)
    expect(isAllowedDevApiRequest('GET', `/api/v1/public/units/a1/photos/${PHOTO}/image`)).toBe(false)
    expect(isAllowedDevApiRequest('GET', `${IMAGE}/extra`)).toBe(false)
    expect(isAllowedDevApiRequest('GET', `/api/v1/public/units/${UNIT}/photos/${PHOTO}/download`)).toBe(false)
    expect(isAllowedDevApiRequest('GET', `/api/v1/units/${UNIT}/photos/${PHOTO}/download`)).toBe(false)
  })

  it.each(['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE'])('blocks %s on an allowed path', (method) => {
    expect(isAllowedDevApiRequest(method, '/api/v1/public/units')).toBe(false)
  })

  it.each([
    ['POST', '/api/v1/public/holds'],
    ['GET', '/api/v1/public/holds'],
    ['POST', `/api/v1/public/holds/${UNIT}/verify-payment`],
    ['POST', '/api/v1/public/promo/validate'],
    ['POST', '/api/v1/customer-auth/register'],
    ['POST', '/api/v1/customer-auth/login'],
    ['GET', '/api/v1/customer-auth/me'],
    ['GET', '/api/v1/customer-auth/oauth/google/start'],
    ['GET', '/api/v1/units'],
    ['GET', '/api/v1/public/units/'],
  ])('blocks %s %s', (method, url) => {
    expect(isAllowedDevApiRequest(method, url)).toBe(false)
  })

  it.each([
    '/api/v1/public/units/%2e%2e/admin',
    '/api/v1/public/%75nits',
    '/api/v1/public/units/../../v1/units',
    '/api/v1/public//units',
    '/api/v1/public\\units',
    `/api/v1/public/units/${UNIT.toUpperCase()}/photos`,
  ])('blocks path tricks: %s', (url) => {
    expect(isAllowedDevApiRequest('GET', url)).toBe(false)
  })

  it('blocks missing method or url', () => {
    expect(isAllowedDevApiRequest(undefined, '/api/v1/public/units')).toBe(false)
    expect(isAllowedDevApiRequest('GET', undefined)).toBe(false)
  })
})
