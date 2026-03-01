/**
 * InfoMesh HTTP Admin API Client — TypeScript example.
 *
 * Demonstrates how to interact with InfoMesh's local admin API
 * for health checks, index stats, credit info, and analytics.
 *
 * Usage:
 *   npx tsx http_client.ts
 *
 * Prerequisites:
 *   - InfoMesh node running: `infomesh start`
 *   - Admin API on default port 8080
 */

const BASE_URL = "http://127.0.0.1:8080";

// Optional: set INFOMESH_API_KEY env var for auth
const API_KEY = process.env.INFOMESH_API_KEY;

interface HealthResponse {
  status: string;
}

interface ReadinessResponse {
  status: string;
  db: string;
}

interface IndexStats {
  document_count: number;
  db_size_mb: number;
}

interface CreditBalance {
  balance: number;
  total_earned: number;
  total_spent: number;
}

interface AnalyticsData {
  total_searches: number;
  total_crawls: number;
  total_fetches: number;
  avg_latency_ms: number;
  uptime_seconds: number;
}

async function request<T>(path: string): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (API_KEY) {
    headers["x-api-key"] = API_KEY;
  }

  const resp = await fetch(`${BASE_URL}${path}`, { headers });
  if (!resp.ok) {
    throw new Error(
      `HTTP ${resp.status}: ${await resp.text()}`
    );
  }
  return resp.json() as Promise<T>;
}

async function main(): Promise<void> {
  try {
    // Health check
    const health = await request<HealthResponse>("/health");
    console.log("Health:", health);

    // Readiness probe
    const ready =
      await request<ReadinessResponse>("/readiness");
    console.log("Readiness:", ready);

    // Index statistics
    const index =
      await request<IndexStats>("/index/stats");
    console.log("Index:", index);

    // Credit balance
    const credits =
      await request<CreditBalance>("/credits/balance");
    console.log("Credits:", credits);

    // Analytics
    const analytics =
      await request<AnalyticsData>("/analytics");
    console.log("Analytics:", analytics);

    // Network peers
    const peers = await request<Record<string, unknown>>(
      "/network/peers"
    );
    console.log("Peers:", peers);

    // Full node status
    const status = await request<Record<string, unknown>>(
      "/status"
    );
    console.log("Status:", status);
  } catch (err) {
    console.error("Error:", err);
    console.error(
      "Is the InfoMesh node running? Start with: infomesh start"
    );
  }
}

main();
