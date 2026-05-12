# millimap-mcp

MCP (Model Context Protocol) server that lets **Claude Desktop** read the active [MilliMap](https://github.com/milliomics/MilliMap) session — its dataset, clusters, cell-type annotations, marker genes, regions of interest — and drive analyses inside the viewer from chat.

## How it works

```
┌──────────────────┐       writes        ┌──────────────────────────┐
│  MilliMap        │  ──────────────▶    │  ~/.millimap/            │
│  (desktop app)   │     every 3s        │  mcp_session.json        │
└──────────────────┘                     └──────────────────────────┘
                                                    │ reads
                                                    ▼
                                         ┌──────────────────────────┐
┌──────────────────┐     stdio MCP       │  millimap-mcp            │
│  Claude Desktop  │  ◀───────────────▶  │  (this package)          │
└──────────────────┘                     └──────────────────────────┘
```

MilliMap writes a snapshot of its current session to `~/.millimap/mcp_session.json` every few seconds. This MCP server is launched by Claude Desktop as a stdio subprocess; it reads the snapshot and exposes it to Claude as MCP resources and tools.

## Prerequisites

- **MilliMap** installed and runnable — see [milliomics/MilliMap](https://github.com/milliomics/MilliMap).
- **Claude Desktop** — [download from anthropic.com](https://claude.ai/download).
- **Python 3.10+** with `pip`.

## Install — into your existing MilliMap environment

**Important:** install `millimap-mcp` into the **same Python environment** you use to run MilliMap. That way the resulting `millimap-mcp` script lands in that env's `bin/` (or `Scripts\` on Windows), and Claude Desktop finds it automatically when you activate the env.

### If you set up MilliMap with conda (the recommended path)

```bash
# 1. Activate the MilliMap env FIRST — this is the step people miss.
conda activate millimap

# 2. Clone this repo somewhere convenient.
git clone https://github.com/milliomics/millimap-mcp.git
cd millimap-mcp

# 3. Install into the activated env.
pip install -e .
```

### If you set up MilliMap with pip (no conda)

Use the same Python you use to launch MilliMap. The safest invocation is to spell out the interpreter explicitly:

```bash
git clone https://github.com/milliomics/millimap-mcp.git
cd millimap-mcp

# Replace this with the python you use for MilliMap:
/path/to/your/python -m pip install -e .

# e.g.:
# /usr/local/bin/python3.11 -m pip install -e .
# or:
# /Users/you/venvs/millimap/bin/python -m pip install -e .
```

### Verify the install

After install, the `millimap-mcp` script should be on your `PATH` (when the env is activated):

```bash
which millimap-mcp           # macOS / Linux
where millimap-mcp           # Windows
```

You should see a path under your MilliMap env, e.g.:
- conda:  `/Users/<you>/anaconda3/envs/millimap/bin/millimap-mcp`
- venv:   `/Users/<you>/venvs/millimap/bin/millimap-mcp`
- Windows conda: `C:\Users\<you>\anaconda3\envs\millimap\Scripts\millimap-mcp.exe`

Keep that path handy — you'll paste it into Claude Desktop's config in the next step (or just use the bare name `millimap-mcp` if Claude Desktop sees the same `PATH` you do).

## Wire it up to Claude Desktop

Open Claude Desktop's config file:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

Create the file if it doesn't exist. Add (merge into any existing `mcpServers` object):

```json
{
  "mcpServers": {
    "millimap": {
      "command": "millimap-mcp"
    }
  }
}
```

### Use an absolute path if `millimap-mcp` isn't on Claude Desktop's PATH

Claude Desktop launches subprocesses with its own `PATH`, which may not include your conda env's `bin/`. If you get *"command not found"* / *"spawn ENOENT"* after restarting Claude, swap the bare command for the absolute path you got from `which millimap-mcp` above:

```json
{
  "mcpServers": {
    "millimap": {
      "command": "/Users/<you>/anaconda3/envs/millimap/bin/millimap-mcp"
    }
  }
}
```

On Windows, double-escape backslashes (JSON requires it):

```json
{
  "mcpServers": {
    "millimap": {
      "command": "C:\\Users\\<you>\\anaconda3\\envs\\millimap\\Scripts\\millimap-mcp.exe"
    }
  }
}
```

Then **fully quit + relaunch Claude Desktop** (the window-close X is not enough — quit from the menu bar / system tray).

In Claude Desktop's tools menu (plug icon in the message composer) you should now see `millimap`. Toggle it on.

## What Claude can see

**Resources** (read-only context):
- `millimap://session` — dataset name, cell/gene counts, assay, cluster column
- `millimap://clusters` — cluster IDs and sizes
- `millimap://annotations` — cluster → cell-type labels set by the scientist
- `millimap://markers` — top marker genes per cluster
- `millimap://rois` — regions of interest saved in the session
- `millimap://analysis_cards` — workspace result cards (summaries only — use `get_analysis_card` for the full payload)

**Read tools** (read the snapshot, no side effects):
- `get_cluster_markers(cluster_id, top_n)` — markers for one cluster
- `genes_for_cell_type(cell_type)` — *"what genes mark T cells?"*
- `search_genes(query, limit)` — find which cluster a gene marks
- `list_rois()` — enumerate saved ROIs
- `list_analysis_cards()` — workspace result cards (summaries)
- `get_analysis_card(card_id, max_rows)` — full payload of one card including its DataFrame

**Write tools** (drive MilliMap's analysis library + UI):
- `run_clustering(resolution, n_neighbors)` — re-cluster the dataset
- `find_markers(groupby, method)` — run `rank_genes_groups`
- `annotate_cluster(cluster_id, label)` — assign a cell-type label
- `score_gene_signature(genes, score_name)` — score a gene set across cells
- `apply_qc_filter(min_genes, max_genes, min_counts, max_mito_pct)` — QC filter
- `run_millimap_tool(tool_name, tool_args_json)` — escape hatch for any of MilliMap's 30+ analysis tools (DE, GO enrichment, neighborhood enrichment, co-occurrence, Ripley, doublet detection, dotplot/heatmap/violin, etc.)

Write tools require MilliMap to be running with a dataset loaded. They POST to a local HTTP endpoint (127.0.0.1, ephemeral port) served by the viewer; the port is discovered via `~/.millimap/mcp_control.json`.
