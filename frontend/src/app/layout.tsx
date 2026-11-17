import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "@/context/auth-context";
import { BrutalistNavbar } from "@/components/navbar";

export const metadata: Metadata = {
  title: "Aurora — Ephemeral Code Execution",
  description:
    "Multi-tenant serverless control plane for executing untrusted code in isolated Firecracker-style microVM sandboxes, backed by DynamoDB Single-Table and Redis.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-[#F4F4F0] text-black flex flex-col">
        <AuthProvider>
          <BrutalistNavbar />
          <main className="flex-1">{children}</main>
          <footer className="border-t-2 border-black bg-white px-6 py-3 text-[11px] font-mono uppercase tracking-wider flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 text-neutral-700">
            <div>Aurora — Ephemeral Code Execution</div>
            <div className="flex items-center gap-4">
              <span>DynamoDB Single-Table</span>
              <span>Redis Cache-Aside</span>
              <span>JWT HS256</span>
            </div>
          </footer>
        </AuthProvider>
      </body>
    </html>
  );
}
