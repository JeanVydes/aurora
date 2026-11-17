"use client";

import React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/context/auth-context";
import { Badge, Button } from "@/components/ui/brutalist";
import { Terminal, LogOut, User } from "lucide-react";

export function BrutalistNavbar() {
  const pathname = usePathname();
  const router = useRouter();
  const { user, quota, signOut } = useAuth();

  const linkClass = (href: string) =>
    `px-3 py-1.5 text-xs font-bold uppercase tracking-wider border-2 ${
      pathname === href
        ? "border-black bg-black text-white"
        : "border-transparent text-black hover:border-black"
    }`;

  return (
    <header className="border-b-2 border-black bg-white sticky top-0 z-40">
      <div className="px-6 h-14 flex items-center justify-between gap-4">
        <div className="flex items-center gap-6 min-w-0">
          <Link href="/" className="flex items-center gap-2.5 shrink-0">
            <div className="w-8 h-8 border-2 border-black bg-black text-white flex items-center justify-center font-bold">
              <Terminal className="w-4 h-4" />
            </div>
            <div>
              <div className="font-bold text-sm tracking-widest uppercase leading-none">
                Aurora
              </div>
              <div className="text-[10px] uppercase tracking-wider text-neutral-600 mt-0.5 hidden sm:block">
                Ephemeral Code Execution
              </div>
            </div>
          </Link>

          <nav className="flex items-center gap-1">
            <Link href="/" className={linkClass("/")}>
              Overview
            </Link>
            <Link href="/dashboard" className={linkClass("/dashboard")}>
              Console
            </Link>
          </nav>
        </div>

        <div className="flex items-center gap-3 shrink-0">
          {user ? (
            <>
              <Link
                href="/auth"
                title="Go to your account"
                className="hidden md:flex flex-col items-end leading-tight border-2 border-transparent hover:border-black px-2 py-1 -my-1 transition-colors"
              >
                <span className="text-xs font-bold max-w-[180px] truncate">
                  {user.email}
                </span>
                <span className="text-[10px] uppercase tracking-wider text-neutral-600">
                  {user.tier === "PRO"
                    ? "Pro plan"
                    : quota
                    ? `${quota.remaining_seconds ?? 0}s remaining`
                    : "Free plan"}
                </span>
              </Link>
              <Link
                href="/auth"
                className="md:hidden border-2 border-transparent hover:border-black p-1 -my-1 transition-colors"
                aria-label="Go to your account"
              >
                <User className="w-4 h-4" />
              </Link>
              <Badge variant={user.tier === "PRO" ? "inverse" : "outline"}>
                {user.tier}
              </Badge>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  signOut();
                  router.push("/");
                }}
                title="Sign out"
              >
                <LogOut className="w-3.5 h-3.5 mr-1.5" />
                Sign out
              </Button>
            </>
          ) : (
            <Link href="/auth">
              <Button size="sm" variant="default">
                <User className="w-3.5 h-3.5 mr-1.5" />
                Sign in
              </Button>
            </Link>
          )}
        </div>
      </div>
    </header>
  );
}
