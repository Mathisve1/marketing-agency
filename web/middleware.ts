// Yuvo Studio — Phase 3D minimal path-exposing middleware.
//
// PURPOSE (and the ONLY thing this does): expose the requested
// path+query to the agency server layout via an `x-pathname` request
// header, so the auth gate can build a deep-link-preserving
// `?next=` value instead of the hardcoded `/agency`.
//
// SAFETY: this middleware performs NO authentication, NO redirect, NO
// cookie read/write, NO data access. It only copies the request URL
// into a request header and calls NextResponse.next(). It therefore
// cannot lock anyone out, cannot change routing, and is scoped to
// `/agency` only (the client portal preserves its own next correctly
// and is intentionally untouched).

import { NextResponse, type NextRequest } from "next/server";

export function middleware(req: NextRequest) {
  const requestHeaders = new Headers(req.headers);
  requestHeaders.set(
    "x-pathname",
    req.nextUrl.pathname + req.nextUrl.search,
  );
  return NextResponse.next({ request: { headers: requestHeaders } });
}

// Scoped strictly to the agency tree. Both entries are needed because
// `/agency/:path*` does not match the bare `/agency` segment.
export const config = {
  matcher: ["/agency", "/agency/:path*"],
};
