import type { NextConfig } from "next";

// Production traffic is proxied by server.mjs (streaming-safe, WebSocket-aware). These rewrites make
// `next dev` behave the same way. No secrets ever reach the browser bundle.
const core = process.env.MG_CORE_URL ?? "http://localhost:8100";

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "same-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  // Docker image: .next/standalone (traced node_modules only) + server.mjs as the entrypoint
  output: "standalone",
  outputFileTracingRoot: __dirname,
  poweredByHeader: false,
  compress: false, // never buffer SSE through compression
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
  // Alleen voor `next dev`: in productie proxyt server.mjs zelf. Zouden de rewrites ook in de
  // productieconfig staan, dan probeert Next een WebSocket-upgrade (live AV1-kanaal) óók zelf
  // door te sturen en raakt de verbinding verminkt.
  async rewrites() {
    if (process.env.NODE_ENV === "production") return [];
    return [
      { source: "/api/v1/:path*", destination: `${core}/api/v1/:path*` },
      { source: "/api/encoder/:path*", destination: `${core}/api/v1/encoder/:path*` },
    ];
  },
};

export default nextConfig;
