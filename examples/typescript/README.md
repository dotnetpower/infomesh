# InfoMesh TypeScript/JavaScript Integration Examples

Example code for integrating InfoMesh's MCP tools from TypeScript and JavaScript clients.

## Files

| File | Description |
|------|-------------|
| `mcp_client.ts` | Full MCP client integration (stdio + HTTP) |
| `http_client.ts` | Direct HTTP Admin API client |
| `package.json` | Dependencies for running examples |

## Setup

```bash
cd examples/typescript
npm install
npx tsx mcp_client.ts
```

## Requirements

- Node.js 18+
- InfoMesh installed (`pip install infomesh` or `uv pip install infomesh`)
- For stdio mode: `infomesh` command available in PATH
- For HTTP mode: `infomesh mcp --http` running on port 8081
