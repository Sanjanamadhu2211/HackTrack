import type { NextConfig } from "next";

// The browser only ever talks to this origin. /api/* is proxied to the
// Python FastAPI app so session cookies stay first-party (plan §5, deployment notes).
const apiOrigin = process.env.API_ORIGIN ?? "http://localhost:8080";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiOrigin}/api/:path*` }];
  },
};

export default nextConfig;
