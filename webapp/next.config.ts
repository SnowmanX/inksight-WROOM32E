import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    const backend = process.env.INKSIGHT_BACKEND_API_BASE?.replace(/\/$/, "") || "http://127.0.0.1:8080";
    return [
      // Proxy all /api requests to backend to avoid missing new routes.
      // Exclude /api/cloud-module/* — handled by local app/api/cloud-module/[...path]/route.ts
      // which forwards to the Waveshare bridge (port 9000).
      {
        source: "/api/:path((?!cloud-module).*)",
        destination: `${backend}/api/:path`,
      },
    ];
  },
};

export default nextConfig;
