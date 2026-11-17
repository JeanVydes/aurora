"use client";

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { ExecutionRecord, QuotaStatus } from "@/lib/utils";

export interface AuthUser {
  account_id: string;
  email: string;
  username?: string;
  tier: "FREE" | "PRO";
  token: string;
  _fuente?: "cache" | "dynamodb";
}

export interface DispatchResult {
  ok: boolean;
  status: number;
  execution?: ExecutionRecord;
  error?: string;
  message?: string;
}

interface AuthContextValue {
  user: AuthUser | null;
  quota: QuotaStatus | null;
  executions: ExecutionRecord[];
  selectedExecution: ExecutionRecord | null;
  setSelectedExecution: (execution: ExecutionRecord | null) => void;
  healthStatus: {
    status: string;
    control_plane: string;
    runtime: string;
    table_name: string;
    redis_host: string;
    timestamp: string;
  } | null;
  isLoading: boolean;
  isRefreshing: boolean;
  lastRefreshedAt: string | null;
  signIn: (email: string, password: string) => Promise<{ ok: boolean; error?: string }>;
  signUp: (params: {
    email: string;
    password: string;
    tier: "FREE" | "PRO";
    username?: string;
  }) => Promise<{ ok: boolean; error?: string }>;
  signOut: () => void;
  refreshAll: () => Promise<void>;
  refreshJwtToken: () => Promise<void>;
  dispatchCodeExecution: (params: {
    code: string;
    timeout: number;
    memory_limit_mb: number;
  }) => Promise<DispatchResult>;
  fetchExecutionById: (executionId: string) => Promise<ExecutionRecord | null>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

const STORAGE_KEY = "aurora_auth_v1";
const HEALTH_POLL_MS = 15000;

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [quota, setQuota] = useState<QuotaStatus | null>(null);
  const [executions, setExecutions] = useState<ExecutionRecord[]>([]);
  const [selectedExecution, setSelectedExecution] = useState<ExecutionRecord | null>(null);
  const [healthStatus, setHealthStatus] = useState<AuthContextValue["healthStatus"]>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [lastRefreshedAt, setLastRefreshedAt] = useState<string | null>(null);

  const userRef = useRef<AuthUser | null>(null);
  userRef.current = user;

  const buildHeaders = useCallback((token?: string) => {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const activeToken = token || userRef.current?.token;
    if (activeToken) {
      headers["Authorization"] = `Bearer ${activeToken}`;
    }
    return headers;
  }, []);

  const persistUser = useCallback((nextUser: AuthUser | null) => {
    setUser(nextUser);
    userRef.current = nextUser;
    if (typeof window === "undefined") return;
    if (nextUser) {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(nextUser));
    } else {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  }, []);

  const refreshAll = useCallback(async (overrideToken?: string) => {
    setIsRefreshing(true);
    try {
      const healthResp = await fetch("/api/v1/health", {
        headers: buildHeaders(overrideToken),
        cache: "no-store",
      });
      if (healthResp.ok) {
        setHealthStatus(await healthResp.json());
      }

      const activeToken = overrideToken || userRef.current?.token;
      if (!activeToken) {
        setLastRefreshedAt(new Date().toISOString());
        return;
      }

      const [quotaResp, execsResp] = await Promise.all([
        fetch("/api/v1/quota", {
          headers: buildHeaders(activeToken),
          cache: "no-store",
        }),
        fetch("/api/v1/executions", {
          headers: buildHeaders(activeToken),
          cache: "no-store",
        }),
      ]);

      if (quotaResp.ok) {
        const qData = await quotaResp.json();
        setQuota(qData);
        if (userRef.current && qData.tier && qData.tier !== userRef.current.tier) {
          persistUser({ ...userRef.current, tier: qData.tier });
        }
      }

      if (execsResp.ok) {
        const eData = await execsResp.json();
        if (Array.isArray(eData.executions)) {
          setExecutions(eData.executions);
          setSelectedExecution((prev) => prev || eData.executions[0] || null);
        }
      }

      setLastRefreshedAt(new Date().toISOString());
    } catch {
      // Keep current state on transient network errors
    } finally {
      setIsRefreshing(false);
    }
  }, [buildHeaders, persistUser]);

  const refreshJwtToken = useCallback(async () => {
    if (!userRef.current?.token) return;
    setIsRefreshing(true);
    try {
      const resp = await fetch("/api/v1/auth/refresh", {
        method: "POST",
        headers: buildHeaders(userRef.current.token),
      });
      if (resp.ok) {
        const data = await resp.json();
        const updatedUser: AuthUser = {
          account_id: data.account_id,
          email: data.email,
          username: userRef.current.username,
          tier: data.tier,
          token: data.token,
          _fuente: data._fuente,
        };
        persistUser(updatedUser);
        await refreshAll(data.token);
      }
    } finally {
      setIsRefreshing(false);
    }
  }, [buildHeaders, persistUser, refreshAll]);

  const signIn = useCallback(
    async (email: string, password: string) => {
      const resp = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: buildHeaders(),
        body: JSON.stringify({ email, password }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        return { ok: false, error: data.error || "Invalid credentials" };
      }
      const loggedUser: AuthUser = {
        account_id: data.account_id,
        email: data.email,
        username: data.username || email.split("@")[0],
        tier: data.tier,
        token: data.token,
        _fuente: data._fuente,
      };
      persistUser(loggedUser);
      await refreshAll(data.token);
      return { ok: true };
    },
    [buildHeaders, persistUser, refreshAll]
  );

  const signUp = useCallback(
    async (params: {
      email: string;
      password: string;
      tier: "FREE" | "PRO";
      username?: string;
    }) => {
      const resp = await fetch("/api/v1/auth/register", {
        method: "POST",
        headers: buildHeaders(),
        body: JSON.stringify(params),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        return { ok: false, error: data.error || "Registration failed" };
      }
      const newUser: AuthUser = {
        account_id: data.account_id,
        email: data.email,
        username: data.username || params.email.split("@")[0],
        tier: data.tier,
        token: data.token,
        _fuente: data._fuente,
      };
      persistUser(newUser);
      await refreshAll(data.token);
      return { ok: true };
    },
    [buildHeaders, persistUser, refreshAll]
  );

  const signOut = useCallback(() => {
    persistUser(null);
    setQuota(null);
    setExecutions([]);
    setSelectedExecution(null);
  }, [persistUser]);

  const dispatchCodeExecution = useCallback(
    async (params: {
      code: string;
      timeout: number;
      memory_limit_mb: number;
    }): Promise<DispatchResult> => {
      if (!userRef.current?.token) {
        return { ok: false, status: 401, error: "Not authenticated" };
      }
      const resp = await fetch("/api/v1/execute", {
        method: "POST",
        headers: buildHeaders(userRef.current.token),
        body: JSON.stringify({
          code: params.code,
          runtime: "python3.11",
          timeout: params.timeout,
          memory_limit_mb: params.memory_limit_mb,
        }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        if (data.quota) setQuota(data.quota);
        return {
          ok: false,
          status: resp.status,
          error: data.error || "Execution failed",
          message: data.message,
        };
      }

      if (data.quota) {
        setQuota(data.quota);
      }
      setSelectedExecution(data);
      try {
        const listResp = await fetch("/api/v1/executions", {
          headers: buildHeaders(userRef.current.token),
          cache: "no-store",
        });
        if (listResp.ok) {
          const listData = await listResp.json();
          if (Array.isArray(listData.executions)) {
            setExecutions(listData.executions);
          }
        }
      } catch {
        // keep current list
      }

      return {
        ok: true,
        status: resp.status,
        execution: data,
      };
    },
    [buildHeaders]
  );

  const fetchExecutionById = useCallback(
    async (executionId: string): Promise<ExecutionRecord | null> => {
      const resp = await fetch(`/api/v1/executions/${executionId}`, {
        headers: buildHeaders(),
        cache: "no-store",
      });
      if (!resp.ok) return null;
      const data = await resp.json();
      setSelectedExecution(data);
      setExecutions((prev) =>
        prev.map((item) =>
          item.execution_id === executionId ? { ...item, _fuente: data._fuente } : item
        )
      );
      return data;
    },
    [buildHeaders]
  );

  // Restore persisted session on initial mount
  useEffect(() => {
    const init = async () => {
      if (typeof window !== "undefined") {
        const saved = window.localStorage.getItem(STORAGE_KEY);
        if (saved) {
          try {
            const parsed = JSON.parse(saved) as AuthUser;
            setUser(parsed);
            userRef.current = parsed;
            await refreshAll(parsed.token);
            setIsLoading(false);
            return;
          } catch {
            window.localStorage.removeItem(STORAGE_KEY);
          }
        }
      }
      await refreshAll();
      setIsLoading(false);
    };
    init();
  }, [refreshAll]);

  // Poll health/quota/executions
  useEffect(() => {
    const id = setInterval(() => {
      refreshAll();
    }, HEALTH_POLL_MS);
    return () => clearInterval(id);
  }, [refreshAll]);

  return (
    <AuthContext.Provider
      value={{
        user,
        quota,
        executions,
        selectedExecution,
        setSelectedExecution,
        healthStatus,
        isLoading,
        isRefreshing,
        lastRefreshedAt,
        signIn,
        signUp,
        signOut,
        refreshAll: () => refreshAll(),
        refreshJwtToken,
        dispatchCodeExecution,
        fetchExecutionById,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
