/**
 * InfoMesh TypeScript Client SDK
 *
 * Feature #12: TypeScript/Node.js client for the InfoMesh MCP API.
 *
 * Usage:
 *   import { InfoMeshClient } from '@infomesh/client';
 *
 *   const client = new InfoMeshClient('http://localhost:8081');
 *   const results = await client.search('python async tutorial');
 */

export interface SearchResult {
  url: string;
  title: string;
  snippet: string;
  score: number;
}

export interface SearchResponse {
  query: string;
  total: number;
  elapsed_ms: number;
  results: SearchResult[];
}

export interface NodeStatus {
  status: string;
  uptime_seconds: number;
  uptime_human: string;
  index: {
    document_count: number;
    db_size_mb: number;
  };
  version: string;
}

export interface CrawlResult {
  url: string;
  success: boolean;
  title?: string;
  text_length?: number;
}

export class InfoMeshClient {
  private baseUrl: string;
  private apiKey?: string;

  constructor(baseUrl: string = 'http://localhost:8080', apiKey?: string) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.apiKey = apiKey;
  }

  private async fetch<T>(path: string, init?: RequestInit): Promise<T> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    if (this.apiKey) {
      headers['x-api-key'] = this.apiKey;
    }

    const response = await globalThis.fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: { ...headers, ...init?.headers },
    });

    if (!response.ok) {
      throw new Error(`InfoMesh API error: ${response.status} ${response.statusText}`);
    }

    return response.json() as Promise<T>;
  }

  /** Search the local index */
  async search(query: string, limit: number = 5): Promise<SearchResponse> {
    const params = new URLSearchParams({ q: query, limit: String(limit) });
    return this.fetch<SearchResponse>(`/search?${params}`);
  }

  /** Get node status */
  async status(): Promise<NodeStatus> {
    return this.fetch<NodeStatus>('/status');
  }

  /** Get node health (detailed) */
  async health(detail: boolean = false): Promise<Record<string, string>> {
    const params = detail ? '?detail=1' : '';
    return this.fetch<Record<string, string>>(`/health${params}`);
  }

  /** Get index statistics */
  async indexStats(): Promise<{ document_count: number; db_size_mb: number }> {
    return this.fetch('/index/stats');
  }

  /** Get credit balance */
  async credits(): Promise<{ balance: number; total_earned: number; total_spent: number }> {
    return this.fetch('/credits/balance');
  }

  /** Get analytics */
  async analytics(): Promise<Record<string, number>> {
    return this.fetch('/analytics');
  }

  /** Get MCP tool usage stats */
  async toolStats(): Promise<Record<string, unknown>> {
    return this.fetch('/analytics/tools');
  }
}

// Default export
export default InfoMeshClient;
