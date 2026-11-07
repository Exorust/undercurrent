# undercurrent MCP

A free, read-only MCP server for the undercurrent map of 459,249 arXiv CS papers. It exposes snapshot topics and bridge papers, with live arXiv abstracts and searches. Rate limits may apply.

Endpoint: https://charoori.com/undercurrent/api/mcp/

Use the trailing slash. charoori.com answers the no-slash form with a 308 redirect, and some MCP clients do not follow redirects on POST. Both forms work for clients that do follow redirects.

Uses the official MCP SDK with stateless Streamable HTTP. No account or API key is required. Each POST gets a fresh server and transport; snapshot JSON stays cached for ten minutes per warm instance. Set `UC_DATA_BASE` to override the snapshot source, which defaults to `https://undercurrent-five-kappa.vercel.app/data`.

Limits, per warm instance: 60 MCP requests per 10 minutes per IP (over the limit returns HTTP 429 with `Retry-After`). arXiv responses are cached for one hour (up to 500 entries), and outbound arXiv requests are spaced at least 3 seconds apart (`UC_ARXIV_SPACING_MS` overrides); a call that would wait more than 20 seconds fails with "arXiv is busy, try again shortly".

## Connect

Claude Code:

```sh
claude mcp add --transport http undercurrent https://charoori.com/undercurrent/api/mcp/
```

Codex, in `~/.codex/config.toml`:

```toml
[mcp_servers.undercurrent]
url = "https://charoori.com/undercurrent/api/mcp/"
```

Cursor, in `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "undercurrent": {
      "url": "https://charoori.com/undercurrent/api/mcp/"
    }
  }
}
```

## Tools

| Tool | What it returns |
| --- | --- |
| `list_topics` | Topics ranked by corrected growth or window paper count |
| `get_topic` | Monthly history, growth, top-cited and newest papers, terms and neighbours |
| `arriving` | Arriving sub-topics, their parent topics and window/corpus shares |
| `links` | Topic pairs with bridge counts, strength, growth and status |
| `compare` | Both topics, cosine distance, pair statistics, available bridge papers and evidence IDs |
| `get_papers` | Live titles, authors, abstracts and publication dates for up to 30 arXiv IDs |
| `novelty_check` | arXiv relevance results for the agent to assess. Tries an AND of up to 4 key phrases, then the 2 longest, then an OR of all; returns `query_used` and `relaxed` |

Levels are 0 for sub-topics, 1 for topics and 2 for areas. Relevant tools accept `window`: `3m`, `6m` or `12m`, defaulting to `6m`. Windows cover complete months; topic growth adjusts for CS growth. Top-cited and newest paper lists cover the whole snapshot. Cross-level comparisons return distance and topic evidence, but have no pair-specific bridge file.

The `ideas_between` prompt guides facet recombination, candidate selection, novelty searches and one revision pass. It requires evidence IDs returned by tools. The server makes no model calls, and search results cannot establish novelty on their own.

## Local verification and export

From the repository root:

```sh
node mcp/test.mjs
.venv/bin/python -m radar.export --selftest
```

The Node test injects a fake fetch backed by `site/data/` and a canned Atom feed. It requires no network or listening port.

The exporter includes `api/mcp.js`, `package.json` and `llms.txt` in its temporary output before swapping it into place. Vercel installs the declared dependencies; the export does not copy `node_modules`. This directory supplies the function code; publishing the export is a separate deployment step.
