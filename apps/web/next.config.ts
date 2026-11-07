import type { NextConfig } from "next";

// The browser only ever talks to this origin (same-origin /api/v1 + cookies).
// On Vercel in services mode the top-level vercel.json rewrites route /api/*
// to the api service, so no proxy is needed. API_ORIGIN only matters for
// local dev, where the FastAPI app runs separately on localhost:8080.
const apiOrigin = process.env.API_ORIGIN;

const nextConfig: NextConfig = {
  async rewrites() {
    return apiOrigin ? [{ source: "/api/:path*", destination: `${apiOrigin}/api/:path*` }] : [];
  },
};

export default nextConfig;
