"use client";

import React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/context/auth-context";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/brutalist";
import {
  Layers,
  ArrowRight,
  Check,
  Terminal,
} from "lucide-react";

const TIERS = [
  {
    name: "Free",
    blurb: "For exploration and personal projects",
    price: "$0",
    cadence: "free during public beta",
    quota: "14,400 execution seconds / month",
    cta: "Start free",
    accent: false,
    features: [
      "4 hours of compute per month",
      "Python 3.11 sandbox, 128 MB default",
      "Strict quota enforcement (HTTP 429)",
      "Full execution telemetry",
    ],
  },
  {
    name: "Pro",
    blurb: "For teams shipping on Aurora",
    price: "$0",
    cadence: "free during public beta",
    quota: "Unlimited execution seconds",
    cta: "Create a Pro workspace",
    accent: true,
    features: [
      "Unlimited monthly compute quota",
      "Same isolation guarantees, no ceilings",
      "Instant plan switch, no checkout",
      "Full execution telemetry",
    ],
  },
];

export default function LandingPage() {
  const router = useRouter();
  const { user } = useAuth();

  return (
    <div className="max-w-7xl mx-auto px-6 py-12 space-y-14">
      {/* HERO */}
      <section className="grid grid-cols-1 lg:grid-cols-12 gap-10 items-center">
        <div className="lg:col-span-7 space-y-6">
          <Badge variant="inverse">Public beta</Badge>
          <h1 className="text-4xl sm:text-6xl font-bold uppercase tracking-tight leading-none">
            Run code you don't trust. Instantly.
          </h1>
          <p className="text-base sm:text-lg leading-relaxed text-neutral-800 max-w-2xl">
            Aurora executes untrusted code on your behalf inside ephemeral,
            memory- and time-capped sandboxes — and meters every second of it.
            Built for safe multi-tenant code execution, today, in public beta.
          </p>
          <div className="flex flex-wrap items-center gap-4 pt-2">
            <Button size="lg" variant="default" onClick={() => router.push(user ? "/dashboard" : "/auth")}>
              <Terminal className="w-4 h-4 mr-2" />
              {user ? "Open the console" : "Get started"}
              <ArrowRight className="w-4 h-4 ml-2" />
            </Button>
            <Link href={user ? "/dashboard" : "/auth"}>
              <Button size="lg" variant="outline">
                {user ? "Console" : "Sign in"}
              </Button>
            </Link>
          </div>
        </div>

        <div className="lg:col-span-5">
          <div className="border-2 border-black bg-black text-white p-5 font-mono text-[12px] leading-relaxed shadow-brutal">
            <div className="text-neutral-400 mb-3"># dispatch a sandbox</div>
            <div className="text-green-400">POST /execute</div>
            <div className="text-neutral-400">authorization: Bearer &lt;JWT&gt;</div>
            <div className="mt-3 text-neutral-400">{"{"}</div>
            <div className="pl-4">"code": <span className="text-yellow-300">"print(6 * 7)"</span>,</div>
            <div className="pl-4">"runtime": <span className="text-yellow-300">"python3.11"</span>,</div>
            <div className="pl-4">"timeout": <span className="text-yellow-300">5.0</span></div>
            <div className="text-neutral-400">{"}"}</div>
            <div className="mt-3 text-neutral-400"># →</div>
            <div className="text-cyan-300">duration: 0.021s</div>
            <div className="text-cyan-300">status: COMPLETED</div>
            <div className="text-cyan-300">vm_id: fc-vm-…</div>
          </div>
        </div>
      </section>

      {/* PRICING */}
      <section className="space-y-5" id="pricing">
        <div className="flex items-center justify-between border-b-2 border-black pb-2">
          <h2 className="text-lg font-bold uppercase tracking-widest">
            Pricing
          </h2>
          <Badge variant="inverse">Free during beta</Badge>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {TIERS.map((tier) => (
            <Card
              key={tier.name}
              className={`flex flex-col ${tier.accent ? "shadow-brutal" : ""}`}
            >
              <CardHeader>
                <CardTitle className="flex items-center justify-between">
                  {tier.name}
                  <Badge variant={tier.accent ? "inverse" : "outline"}>
                    {tier.quota}
                  </Badge>
                </CardTitle>
                <p className="text-xs text-neutral-600 mt-1">{tier.blurb}</p>
              </CardHeader>
              <CardContent className="flex flex-col gap-4 text-sm flex-1">
                <div>
                  <span className="text-4xl font-bold">{tier.price}</span>
                  <span className="ml-2 text-xs uppercase text-neutral-600">
                    {tier.cadence}
                  </span>
                </div>
                <ul className="space-y-2 text-xs text-neutral-800">
                  {tier.features.map((feature) => (
                    <li key={feature} className="flex items-start gap-2">
                      <Check className="w-4 h-4 shrink-0 mt-0.5" />
                      <span>{feature}</span>
                    </li>
                  ))}
                </ul>
                <div className="mt-auto">
                  <Button
                    size="md"
                    variant={tier.accent ? "default" : "outline"}
                    className="w-full"
                    onClick={() => router.push("/auth")}
                  >
                    {tier.cta}
                    <ArrowRight className="w-4 h-4 ml-2" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
        <p className="text-[11px] text-neutral-600">
          Production pricing will be announced with general availability. Every
          account, regardless of plan, gets full isolated execution and complete
          telemetry.
        </p>
      </section>

      {/* TECH TRUST STRIP */}
      <section className="border-2 border-black bg-white p-5 flex flex-col md:flex-row items-center justify-between gap-4 text-xs font-mono uppercase tracking-wider text-neutral-700">
        <div className="flex items-center gap-2">
          <Layers className="w-4 h-4" />
          <span>Built on DynamoDB Single-Table, Redis 7, AWS CDK, LocalStack</span>
        </div>
        <div className="flex items-center gap-4">
          <span>PBKDF2-HMAC-SHA256</span>
          <span>·</span>
          <span>JWT HS256</span>
        </div>
      </section>
    </div>
  );
}
