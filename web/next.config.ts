import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Phase 1A: no images optimisation needed (we only ship the 48 KB Pai
  // thumbnail under /demo). Disabling next/image AVIF/WebP pipeline keeps
  // the build deterministic and removes a Sharp install requirement.
  images: { unoptimized: true },
};

export default nextConfig;
