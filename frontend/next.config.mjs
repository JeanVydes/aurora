/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Produce a self-contained server.js for the production Docker image
  output: "standalone",
};

export default nextConfig;
