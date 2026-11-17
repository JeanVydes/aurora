import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export interface ExecutionTelemetry {
  vm_id: string;
  isolation_engine: string;
  host_executor: string;
  vcpu_count: number;
  memory_limit_mb: number;
  boot_time_ms: number;
  execution_time_ms: number;
  teardown_time_ms: number;
  total_lifecycle_ms: number;
  state_transitions: string[];
  teardown_status: string;
  boot_timestamp: string;
  teardown_timestamp: string;
}

export interface ExecutionRecord {
  PK: string;
  SK: string;
  execution_id: string;
  account_id: string;
  tier: "FREE" | "PRO";
  code: string;
  runtime: string;
  duration: number;
  timeout_enforced: number;
  memory_limit_mb: number;
  status: "COMPLETED" | "TIMEOUT" | "FAILED" | "MEMORY_LIMIT_EXCEEDED";
  exit_code: number;
  stdout: string;
  stderr: string;
  telemetry: ExecutionTelemetry;
  created_at: string;
  _fuente?: "cache" | "dynamodb";
  account_fuente?: "cache" | "dynamodb";
}

export interface QuotaStatus {
  PK: string;
  SK: string;
  account_id: string;
  period: string;
  tier: "FREE" | "PRO";
  accumulated_seconds: number;
  limit_seconds: number | null;
  remaining_seconds: number | null;
  unlimited: boolean;
  quota_exceeded: boolean;
  execution_count: number;
  _fuente?: "cache" | "dynamodb";
}
