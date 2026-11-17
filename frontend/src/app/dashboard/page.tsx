"use client";

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";
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
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Cpu,
  RefreshCw,
  Terminal,
  XCircle,
} from "lucide-react";
import { ExecutionRecord } from "@/lib/utils";

const CODE_PRESETS: Array<{
  id: string;
  label: string;
  code: string;
  timeout: number;
  memoryMb: number;
}> = [
  {
    id: "hello",
    label: "Hello",
    code: `print("Hello from Aurora")`,
    timeout: 5,
    memoryMb: 128,
  },
  {
    id: "bench",
    label: "Compute bench",
    code: `import hashlib\n\ndigest = hashlib.sha256(b"aurora").hexdigest()\ntotal = sum(i * i for i in range(100_000))\nprint("sha256:", digest)\nprint("sum:", total)`,
    timeout: 5,
    memoryMb: 128,
  },
  {
    id: "timeout",
    label: "Timeout test",
    code: `import time\ntime.sleep(30)\nprint("unreachable")`,
    timeout: 2,
    memoryMb: 128,
  },
  {
    id: "memory",
    label: "Memory test",
    code: `data = bytearray()\nfor _ in range(40):\n    data += b"x" * 1024 * 1024\nprint("allocated", len(data), "bytes")`,
    timeout: 5,
    memoryMb: 16,
  },
];

function statusVariant(status: ExecutionRecord["status"]): "ok" | "alert" | "inverse" {
  if (status === "COMPLETED") return "ok";
  if (status === "FAILED" || status === "MEMORY_LIMIT_EXCEEDED") return "alert";
  return "inverse";
}

export default function DashboardPage() {
  const {
    user,
    quota,
    executions,
    selectedExecution,
    isLoading,
    isRefreshing,
    lastRefreshedAt,
    refreshAll,
    dispatchCodeExecution,
    fetchExecutionById,
  } = useAuth();

  const [code, setCode] = useState(CODE_PRESETS[0].code);
  const [timeoutSec, setTimeoutSec] = useState(5);
  const [memoryMb, setMemoryMb] = useState(128);
  const [isDispatching, setIsDispatching] = useState(false);
  const [dispatchError, setDispatchError] = useState<string | null>(null);

  const codeLineCount = useMemo(() => code.split("\n").length, [code]);

  const handleSelectPreset = (preset: (typeof CODE_PRESETS)[number]) => {
    setCode(preset.code);
    setTimeoutSec(preset.timeout);
    setMemoryMb(preset.memoryMb);
  };

  const handleDispatch = async () => {
    setDispatchError(null);
    setIsDispatching(true);
    try {
      const result = await dispatchCodeExecution({
        code,
        timeout: timeoutSec,
        memory_limit_mb: memoryMb,
      });
      if (!result.ok) {
        setDispatchError(result.error || "Execution failed");
      }
    } finally {
      setIsDispatching(false);
    }
  };

  if (isLoading) {
    return (
      <div className="max-w-7xl mx-auto px-6 py-16">
        <p className="text-sm font-mono uppercase tracking-widest text-neutral-600">
          Loading…
        </p>
      </div>
    );
  }

  if (!user) {
    return (
      <div className="max-w-2xl mx-auto px-6 py-16 text-center space-y-6">
        <div className="inline-flex w-12 h-12 border-2 border-black bg-black text-white items-center justify-center">
          <Terminal className="w-5 h-5" />
        </div>
        <h1 className="text-2xl font-bold uppercase tracking-widest">Sign in to run code</h1>
        <p className="text-sm text-neutral-700">
          The console dispatches isolated executions against your account quota.
          Create a free account to try it.
        </p>
        <Link href="/auth">
          <Button size="lg">Sign in / Create account</Button>
        </Link>
      </div>
    );
  }

  const quotaUsedPct = quota
    ? quota.unlimited
      ? 0
      : Math.min(100, (quota.accumulated_seconds / (quota.limit_seconds ?? 1)) * 100)
    : 0;

  return (
    <div className="max-w-7xl mx-auto px-6 py-8 space-y-8">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4 border-b-2 border-black pb-4">
        <div>
          <div className="text-[11px] font-bold uppercase tracking-widest text-neutral-600">
            Console
          </div>
          <h1 className="text-2xl font-bold uppercase tracking-widest mt-1">
            Ephemeral Python sandbox
          </h1>
        </div>
        <div className="flex items-center gap-3 text-[11px] font-mono uppercase">
          <span className="text-neutral-600">
            {lastRefreshedAt
              ? `Updated ${new Date(lastRefreshedAt).toLocaleTimeString()}`
              : "—"}
          </span>
          <button
            onClick={() => refreshAll()}
            className="border-2 border-black p-1.5 hover:bg-black hover:text-white transition-colors"
            title="Refresh"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
        {/* Editor */}
        <Card className="xl:col-span-7">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Terminal className="w-4 h-4" />
              Code
            </CardTitle>
            <div className="flex flex-wrap gap-1.5">
              {CODE_PRESETS.map((preset) => (
                <button
                  key={preset.id}
                  type="button"
                  onClick={() => handleSelectPreset(preset)}
                  className="border border-black bg-white px-2 py-0.5 text-[10px] font-bold uppercase hover:bg-black hover:text-white"
                >
                  {preset.label}
                </button>
              ))}
            </div>
          </CardHeader>
          <CardContent className="p-0">
            <div className="flex border-b-2 border-black bg-[#111111] text-white font-mono text-xs">
              <div
                aria-hidden="true"
                className="select-none border-r-2 border-black bg-black text-neutral-500 px-3 py-4 text-right leading-6"
              >
                {Array.from({ length: codeLineCount }).map((_, i) => (
                  <div key={i}>{String(i + 1).padStart(2, "0")}</div>
                ))}
              </div>
              <textarea
                aria-label="Python code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                rows={14}
                spellCheck={false}
                className="w-full bg-[#111111] text-white p-4 font-mono text-xs leading-6 focus:outline-none resize-y"
              />
            </div>

            <div className="p-4 bg-[#F4F4F0] flex flex-wrap items-end gap-4">
              <div>
                <label htmlFor="timeout" className="block text-[10px] font-bold uppercase text-neutral-600 mb-1">
                  Timeout (s)
                </label>
                <input
                  id="timeout"
                  type="number"
                  min={0.5}
                  max={120}
                  step={0.5}
                  value={timeoutSec}
                  onChange={(e) => setTimeoutSec(Number(e.target.value))}
                  className="w-24 border-2 border-black bg-white px-2 py-1.5 text-sm font-mono focus:outline-none"
                />
              </div>
              <div>
                <label htmlFor="memory" className="block text-[10px] font-bold uppercase text-neutral-600 mb-1">
                  Memory limit
                </label>
                <select
                  id="memory"
                  value={memoryMb}
                  onChange={(e) => setMemoryMb(Number(e.target.value))}
                  className="border-2 border-black bg-white px-2 py-1.5 text-sm font-mono focus:outline-none"
                >
                  {[16, 64, 128, 256, 512].map((mb) => (
                    <option key={mb} value={mb}>{mb} MB</option>
                  ))}
                </select>
              </div>
              <Button
                size="lg"
                onClick={handleDispatch}
                disabled={isDispatching}
                className="ml-auto"
              >
                {isDispatching ? "Running…" : "Run"}
              </Button>
            </div>
            {dispatchError && (
              <div className="px-4 pb-4 bg-[#F4F4F0]">
                <div className="border-2 border-black bg-[#D92626] text-white text-xs font-bold uppercase p-2 flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                  {dispatchError}
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Selected execution */}
        <Card className="xl:col-span-5">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Cpu className="w-4 h-4" />
              Output
            </CardTitle>
            {selectedExecution && (
              <Badge variant={statusVariant(selectedExecution.status)}>
                {selectedExecution.status}
              </Badge>
            )}
          </CardHeader>
          <CardContent className="space-y-4">
            {selectedExecution ? (
              <>
                <pre className="border-2 border-black bg-black text-white text-xs p-3 min-h-[120px] max-h-56 overflow-auto whitespace-pre-wrap">
                  {selectedExecution.stdout || ""}
                  {selectedExecution.stderr ? (
                    <span className="text-red-400">\n{selectedExecution.stderr}</span>
                  ) : null}
                  {!selectedExecution.stdout && !selectedExecution.stderr && "—"}
                </pre>

                <div className="grid grid-cols-2 gap-3 text-[11px]">
                  {[
                    ["Execution", selectedExecution.execution_id],
                    ["Duration", `${Number(selectedExecution.duration).toFixed(3)}s`],
                    ["Exit code", String(selectedExecution.exit_code)],
                    ["Timeout enforced", `${selectedExecution.timeout_enforced}s`],
                    ["Memory limit", `${selectedExecution.memory_limit_mb} MB`],
                    ["VM ID", selectedExecution.telemetry?.vm_id || "—"],
                    ["Boot", `${selectedExecution.telemetry?.boot_time_ms ?? "—"} ms`],
                    ["Teardown", `${selectedExecution.telemetry?.teardown_time_ms ?? "—"} ms`],
                  ].map(([label, value]) => (
                    <div key={label as string} className="border-2 border-black bg-[#F4F4F0] p-2">
                      <div className="text-[9px] uppercase text-neutral-600">{label}</div>
                      <div className="font-mono font-bold truncate">{value}</div>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <p className="text-sm text-neutral-600">
                Run code to see output and telemetry here.
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Usage */}
      <section className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        <Card className="lg:col-span-4">
          <CardHeader>
            <CardTitle>Usage — {quota?.period || "current month"}</CardTitle>
            <Badge variant="outline">{quota?.tier || "FREE"}</Badge>
          </CardHeader>
          <CardContent className="space-y-4">
            {quota?.unlimited ? (
              <div>
                <div className="text-3xl font-bold">∞</div>
                <div className="text-[11px] uppercase text-neutral-600 mt-1">
                  Unlimited execution seconds on Pro
                </div>
                <div className="text-xs text-neutral-600 mt-2">
                  {quota.accumulated_seconds.toFixed(2)}s consumed this period ·{" "}
                  {quota.execution_count} executions
                </div>
              </div>
            ) : (
              <>
                <div>
                  <div className="flex items-baseline justify-between">
                    <span className="text-3xl font-bold">
                      {quota ? quota.remaining_seconds?.toFixed(2) : "—"}
                    </span>
                    <span className="text-[11px] uppercase text-neutral-600">
                      seconds remaining
                    </span>
                  </div>
                  <div className="mt-2 h-3 border-2 border-black bg-white">
                    <div
                      className="h-full bg-black"
                      style={{ width: `${100 - quotaUsedPct}%` }}
                    />
                  </div>
                  <div className="text-[11px] text-neutral-600 mt-2">
                    {quota?.accumulated_seconds.toFixed(2) ?? 0}s of{" "}
                    {quota?.limit_seconds ?? 14400}s used · {quota?.execution_count ?? 0}{" "}
                    executions
                  </div>
                </div>
              </>
            )}
          </CardContent>
        </Card>

        {/* Recent executions */}
        <Card className="lg:col-span-8">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4" />
              Recent runs
            </CardTitle>
            {executions.length > 0 && (
              <button
                onClick={() => fetchExecutionById(executions[0].execution_id)}
                className="text-[10px] uppercase font-bold underline underline-offset-2"
              >
                Refresh first
              </button>
            )}
          </CardHeader>
          <CardContent className="p-0">
            {executions.length === 0 ? (
              <p className="text-sm text-neutral-600 p-4">No executions yet.</p>
            ) : (
              <ul className="divide-y-2 divide-black">
                {executions.slice(0, 10).map((ex) => {
                  const isSelected = selectedExecution?.execution_id === ex.execution_id;
                  return (
                    <li key={ex.execution_id}>
                      <button
                        type="button"
                        onClick={() => fetchExecutionById(ex.execution_id)}
                        className={`w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-[#F4F4F0] ${
                          isSelected ? "bg-[#E5E5E0]" : ""
                        }`}
                      >
                        {ex.status === "COMPLETED" ? (
                          <CheckCircle2 className="w-4 h-4 shrink-0" />
                        ) : (
                          <XCircle className="w-4 h-4 shrink-0" />
                        )}
                        <span className="font-mono text-xs flex-1 truncate">
                          {ex.execution_id}
                        </span>
                        <span className="text-[10px] uppercase hidden sm:inline text-neutral-600">
                          {Number(ex.duration).toFixed(3)}s
                        </span>
                        <Badge variant={statusVariant(ex.status)}>{ex.status}</Badge>
                        <ChevronRight className="w-4 h-4 shrink-0" />
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
