"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/context/auth-context";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Label,
} from "@/components/ui/brutalist";
import { Copy, KeyRound, RefreshCw, Trash2, UserPlus } from "lucide-react";

const TIER_OPTIONS: Array<{ id: "FREE" | "PRO"; title: string; blurb: string }> = [
  {
    id: "FREE",
    title: "Free",
    blurb: "14,400 execution seconds per month",
  },
  {
    id: "PRO",
    title: "Pro",
    blurb: "Unlimited monthly execution seconds",
  },
];

export default function AuthPage() {
  const router = useRouter();
  const { user, signIn, signUp, signOut } = useAuth();

  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [username, setUsername] = useState("");
  const [tier, setTier] = useState<"FREE" | "PRO">("FREE");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [apiKeyMeta, setApiKeyMeta] = useState<{
    has_key: boolean;
    api_key_id: string | null;
    api_key_prefix: string | null;
    created_at: string | null;
    last_used_at: string | null;
  } | null>(null);
  const [freshKey, setFreshKey] = useState<string | null>(null);
  const [keyBusy, setKeyBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const authHeaders = () =>
    user ? { Authorization: `Bearer ${user.token}` } : undefined;

  const loadApiKey = async () => {
    if (!user) return;
    try {
      const resp = await fetch("/api/v1/api-key", { headers: authHeaders() });
      if (resp.ok) setApiKeyMeta(await resp.json());
    } catch {
      // keep current state
    }
  };

  useEffect(() => {
    loadApiKey();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.account_id]);

  const rotateApiKey = async () => {
    if (!user) return;
    setKeyBusy(true);
    try {
      const resp = await fetch("/api/v1/api-key/rotate", {
        method: "POST",
        headers: authHeaders(),
      });
      if (!resp.ok) return;
      const data = await resp.json();
      setFreshKey(data.api_key);
      setCopied(false);
      await loadApiKey();
    } finally {
      setKeyBusy(false);
    }
  };

  const revokeApiKey = async () => {
    if (!user) return;
    setKeyBusy(true);
    try {
      await fetch("/api/v1/api-key", { method: "DELETE", headers: authHeaders() });
      setFreshKey(null);
      await loadApiKey();
    } finally {
      setKeyBusy(false);
    }
  };

  const copyFreshKey = async () => {
    if (!freshKey) return;
    try {
      await navigator.clipboard.writeText(freshKey);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (mode === "signin") {
        const res = await signIn(email, password);
        if (!res.ok) {
          setError(res.error || "Sign in failed");
          return;
        }
        router.push("/dashboard");
      } else {
        const res = await signUp({ email, password, tier, username: username || undefined });
        if (!res.ok) {
          setError(res.error || "Registration failed");
          return;
        }
        router.push("/dashboard");
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (user) {
    return (
      <div className="max-w-2xl mx-auto px-6 py-16 space-y-6">
        <div className="flex items-center justify-between border-b-2 border-black pb-4">
          <div>
            <h1 className="text-2xl font-bold uppercase tracking-widest">Account</h1>
            <p className="text-xs text-neutral-700 mt-1">
              Signed in as <span className="font-bold">{user.email}</span>
            </p>
          </div>
          <Badge variant="inverse">{user.tier} plan</Badge>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Session</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 text-sm">
            <div className="flex justify-between border-b border-neutral-200 pb-2">
              <span className="text-neutral-600">Account ID</span>
              <span className="font-mono">{user.account_id}</span>
            </div>
            <div className="flex justify-between border-b border-neutral-200 pb-2">
              <span className="text-neutral-600">Plan</span>
              <span className="font-bold">{user.tier}</span>
            </div>
            <div className="flex justify-between border-b border-neutral-200 pb-2">
              <span className="text-neutral-600">Token type</span>
              <span className="font-mono">HS256</span>
            </div>

            <div className="border-2 border-black bg-[#F4F4F0] p-3 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="text-xs font-bold uppercase">API Key</div>
                  <div className="text-[11px] text-neutral-600 mt-0.5">
                    For scripts &amp; agents (e.g. X-API-Key header) — no session needed
                  </div>
                </div>
                <Badge variant={apiKeyMeta?.has_key ? "ok" : "outline"}>
                  {apiKeyMeta?.has_key ? "Set" : "Not set"}
                </Badge>
              </div>

              {apiKeyMeta?.has_key && !freshKey && (
                <div className="text-[11px] text-neutral-700 space-y-0.5">
                  <div className="font-mono">{apiKeyMeta.api_key_prefix}…</div>
                  <div>
                    id: <span className="font-mono">{apiKeyMeta.api_key_id}</span>
                    {apiKeyMeta.created_at && (
                      <> · created {new Date(apiKeyMeta.created_at).toLocaleDateString()}</>
                    )}
                    {apiKeyMeta.last_used_at && (
                      <> · last used {new Date(apiKeyMeta.last_used_at).toLocaleDateString()}</>
                    )}
                  </div>
                </div>
              )}

              {freshKey && (
                <div className="border-2 border-black bg-black text-white p-3 space-y-2">
                  <div className="text-[10px] uppercase font-bold text-yellow-300">
                    Copy this now — it will never be shown again
                  </div>
                  <div className="font-mono text-[11px] break-all">{freshKey}</div>
                  <div className="flex items-center gap-2">
                    <Button size="sm" variant="outline" onClick={copyFreshKey}>
                      <Copy className="w-3.5 h-3.5 mr-1.5" />
                      {copied ? "Copied" : "Copy"}
                    </Button>
                    <span className="text-[10px] text-neutral-400">
                      Revoke &amp; rotate any time from this page
                    </span>
                  </div>
                </div>
              )}

              <div className="flex flex-wrap items-center gap-2 pt-1">
                <Button size="sm" onClick={rotateApiKey} disabled={keyBusy}>
                  <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
                  {apiKeyMeta?.has_key ? "Rotate key" : "Generate key"}
                </Button>
                {apiKeyMeta?.has_key && (
                  <Button size="sm" variant="danger" onClick={revokeApiKey} disabled={keyBusy}>
                    <Trash2 className="w-3.5 h-3.5 mr-1.5" />
                    Revoke
                  </Button>
                )}
              </div>

              {freshKey && (
                <pre className="border border-neutral-300 bg-white text-neutral-800 p-2 text-[10px] overflow-x-auto leading-snug">{`curl -X POST \\
  -H "X-API-Key: ${freshKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"code":"print(6*7)","timeout":5}' \\
  https://aurora.jeanvides.com/api/v1/execute`}</pre>
              )}
            </div>

            <div className="flex gap-3 pt-2">
              <Button onClick={() => router.push("/dashboard")}>
                Open console
              </Button>
              <Button variant="outline" onClick={signOut}>
                Sign out
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto px-6 py-12 grid grid-cols-1 lg:grid-cols-12 gap-10">
      <div className="lg:col-span-7">
        <Card>
          <CardHeader className="p-0 bg-white border-b-2 border-black">
            <div className="grid grid-cols-2 w-full">
              <button
                type="button"
                onClick={() => {
                  setMode("signin");
                  setError(null);
                }}
                className={`py-3 text-xs font-bold uppercase tracking-widest border-r-2 border-black ${
                  mode === "signin"
                    ? "bg-black text-white"
                    : "bg-[#F4F4F0] text-black hover:bg-white"
                }`}
              >
                Sign in
              </button>
              <button
                type="button"
                onClick={() => {
                  setMode("signup");
                  setError(null);
                }}
                className={`py-3 text-xs font-bold uppercase tracking-widest ${
                  mode === "signup"
                    ? "bg-black text-white"
                    : "bg-[#F4F4F0] text-black hover:bg-white"
                }`}
              >
                Create account
              </button>
            </div>
          </CardHeader>

          <CardContent className="space-y-5 pt-6">
            {error && (
              <div className="border-2 border-black bg-[#D92626] text-white p-3 text-xs font-bold uppercase">
                {error}
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  required
                />
              </div>

              {mode === "signup" && (
                <div>
                  <Label htmlFor="username">Username (optional)</Label>
                  <Input
                    id="username"
                    type="text"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder="username"
                    autoComplete="username"
                  />
                </div>
              )}

              <div>
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  required
                  autoComplete={mode === "signin" ? "current-password" : "new-password"}
                />
              </div>

              {mode === "signup" && (
                <div>
                  <Label>Plan</Label>
                  <div className="grid grid-cols-2 gap-3">
                    {TIER_OPTIONS.map((option) => (
                      <button
                        key={option.id}
                        type="button"
                        onClick={() => setTier(option.id)}
                        className={`p-3 border-2 border-black text-left ${
                          tier === option.id ? "bg-black text-white" : "bg-white text-black"
                        }`}
                      >
                        <div className="text-xs font-bold uppercase">{option.title}</div>
                        <div className="text-[10px] opacity-80 mt-0.5">{option.blurb}</div>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div className="pt-2">
                <Button type="submit" className="w-full" disabled={submitting}>
                  {mode === "signin" ? (
                    <>
                      <KeyRound className="w-4 h-4 mr-2" />
                      {submitting ? "Signing in…" : "Sign in"}
                    </>
                  ) : (
                    <>
                      <UserPlus className="w-4 h-4 mr-2" />
                      {submitting ? "Creating account…" : "Create account"}
                    </>
                  )}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </div>

      <div className="lg:col-span-5 space-y-6">
        <div className="border-2 border-black bg-white p-6 shadow-brutal">
          <h2 className="text-sm font-bold uppercase tracking-widest border-b-2 border-black pb-2">
            What you get
          </h2>
          <ul className="mt-4 space-y-3 text-sm text-neutral-800">
            {[
              "Ephemeral sandbox execution with strict timeout and memory ceilings",
              "Monthly execution quota metered per second",
              "Full telemetry for every run (stdout, stderr, exit code, duration)",
              "Your executions are private to your account",
            ].map((point) => (
              <li key={point} className="flex items-start gap-2">
                <span className="font-bold">—</span>
                <span>{point}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
