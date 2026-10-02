const LOCAL_API_BASE_URL = "http://localhost:8000/api/v1";

export function resolveApiBaseUrl(configuredUrl: string | undefined): string {
  const normalizedUrl = configuredUrl?.trim();
  if (normalizedUrl) return normalizedUrl;

  if (import.meta.env.DEV === true) {
    return LOCAL_API_BASE_URL;
  }

  throw new Error("VITE_API_BASE_URL must be configured for production builds.");
}
