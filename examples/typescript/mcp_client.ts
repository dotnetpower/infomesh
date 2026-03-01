/**
 * InfoMesh MCP Client — TypeScript integration example.
 *
 * Demonstrates how to connect to InfoMesh's MCP server and
 * call search, crawl, suggest, batch_search, and analytics tools.
 *
 * Usage:
 *   npx tsx mcp_client.ts
 *
 * Prerequisites:
 *   - `infomesh` CLI installed and in PATH
 *   - OR: `infomesh mcp --http` running on port 8081
 */

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

async function main(): Promise<void> {
  // --- 1. Connect via stdio transport ---
  const transport = new StdioClientTransport({
    command: "infomesh",
    args: ["mcp"],
  });

  const client = new Client(
    { name: "infomesh-ts-example", version: "1.0.0" },
    { capabilities: {} }
  );

  await client.connect(transport);
  console.log("Connected to InfoMesh MCP server");

  // --- 2. List available tools ---
  const tools = await client.listTools();
  console.log(
    "Available tools:",
    tools.tools.map((t) => t.name)
  );

  // --- 3. Basic search (text format) ---
  const textResult = await client.callTool({
    name: "search",
    arguments: { query: "python web framework", limit: 5 },
  });
  console.log("\n=== Search (text) ===");
  console.log(
    (textResult.content as Array<{ text: string }>)[0]?.text
  );

  // --- 4. Search with JSON output + filters ---
  const jsonResult = await client.callTool({
    name: "search",
    arguments: {
      query: "machine learning tutorial",
      limit: 3,
      format: "json",
      language: "en",
      snippet_length: 300,
    },
  });
  console.log("\n=== Search (JSON) ===");
  const parsed = JSON.parse(
    (jsonResult.content as Array<{ text: string }>)[0]?.text ||
      "{}"
  );
  console.log("Total results:", parsed.total);
  console.log("Quota:", parsed.quota);
  for (const r of parsed.results || []) {
    console.log(`  - ${r.title} (${r.url})`);
  }

  // --- 5. Search with domain filtering ---
  const filtered = await client.callTool({
    name: "search_local",
    arguments: {
      query: "API documentation",
      include_domains: ["docs.python.org"],
      format: "json",
    },
  });
  console.log("\n=== Filtered search ===");
  console.log(
    (filtered.content as Array<{ text: string }>)[0]?.text
  );

  // --- 6. Batch search ---
  const batch = await client.callTool({
    name: "batch_search",
    arguments: {
      queries: ["python asyncio", "rust ownership", "go goroutines"],
      limit: 3,
      format: "json",
    },
  });
  console.log("\n=== Batch search ===");
  const batchData = JSON.parse(
    (batch.content as Array<{ text: string }>)[0]?.text || "{}"
  );
  for (const br of batchData.batch_results || []) {
    console.log(`  Query: ${br.query}, Results: ${br.total}`);
  }

  // --- 7. Search suggestions ---
  const suggestions = await client.callTool({
    name: "suggest",
    arguments: { prefix: "pyth", limit: 5 },
  });
  console.log("\n=== Suggestions ===");
  console.log(
    (suggestions.content as Array<{ text: string }>)[0]?.text
  );

  // --- 8. Fetch a page ---
  const page = await client.callTool({
    name: "fetch_page",
    arguments: {
      url: "https://docs.python.org/3/",
      format: "json",
    },
  });
  console.log("\n=== Fetch page ===");
  const pageData = JSON.parse(
    (page.content as Array<{ text: string }>)[0]?.text || "{}"
  );
  console.log(`Title: ${pageData.title}`);
  console.log(`Text length: ${pageData.text?.length || 0}`);

  // --- 9. Crawl a URL ---
  const crawl = await client.callTool({
    name: "crawl_url",
    arguments: {
      url: "https://example.com",
      depth: 1,
    },
  });
  console.log("\n=== Crawl ===");
  console.log(
    (crawl.content as Array<{ text: string }>)[0]?.text
  );

  // --- 10. Network stats ---
  const stats = await client.callTool({
    name: "network_stats",
    arguments: { format: "json" },
  });
  console.log("\n=== Network stats ===");
  console.log(
    (stats.content as Array<{ text: string }>)[0]?.text
  );

  // --- 11. Analytics ---
  const analytics = await client.callTool({
    name: "analytics",
    arguments: { format: "json" },
  });
  console.log("\n=== Analytics ===");
  console.log(
    (analytics.content as Array<{ text: string }>)[0]?.text
  );

  // --- 12. Session-based search ---
  const session1 = await client.callTool({
    name: "search",
    arguments: {
      query: "FastAPI tutorial",
      session_id: "my-session",
      format: "json",
    },
  });
  console.log("\n=== Session search 1 ===");
  console.log(
    (session1.content as Array<{ text: string }>)[0]?.text
  );

  // Follow-up in same session
  const session2 = await client.callTool({
    name: "search",
    arguments: {
      query: "FastAPI middleware",
      session_id: "my-session",
      format: "json",
    },
  });
  console.log("\n=== Session search 2 ===");
  console.log(
    (session2.content as Array<{ text: string }>)[0]?.text
  );

  await client.close();
  console.log("\nDone.");
}

main().catch(console.error);
