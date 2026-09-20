// Shared test helper: builds a syntactically-valid (but unsigned) JWT whose
// payload AuthContext's own decodeJwt() can parse, so tests can exercise
// the real AuthProvider/useAuth code path for a given role rather than
// mocking the context away. The "signature" segment is never verified
// client-side (only decoded for display), so any string works here.
function base64UrlEncode(json: object): string {
  const base64 = btoa(JSON.stringify(json));
  return base64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function fakeToken(role: string): string {
  const header = base64UrlEncode({ alg: "none", typ: "JWT" });
  const payload = base64UrlEncode({ sub: "test-user-id", role, exp: Math.floor(Date.now() / 1000) + 3600 });
  return `${header}.${payload}.test-signature`;
}

export function loginAs(role: string): void {
  localStorage.setItem("access_token", fakeToken(role));
  localStorage.setItem("refresh_token", "irrelevant-for-tests");
}
