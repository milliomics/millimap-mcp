"""MCP server for MilliMap — exposes the active session snapshot to Claude
and proxies write-path tool calls into the running MilliMap viewer.

- Read path: ``~/.millimap/mcp_session.json`` (written by the desktop app).
- Write path: ``http://127.0.0.1:<port>/tool`` (served by the desktop app;
  port discovered via ``~/.millimap/mcp_control.json``).
"""
from __future__ import annotations

import json
import pathlib
import urllib.error
import urllib.request
from typing import Any

from mcp.server.fastmcp import FastMCP


SNAPSHOT_PATH = pathlib.Path.home() / ".millimap" / "mcp_session.json"
CONTROL_PATH = pathlib.Path.home() / ".millimap" / "mcp_control.json"

mcp = FastMCP("millimap")


def _load_control() -> dict | None:
    try:
        with CONTROL_PATH.open("r") as f:
            return json.load(f)
    except Exception:
        return None


def _post_tool(name: str, args: dict, timeout: float = 600.0) -> dict:
    ctrl = _load_control()
    if not ctrl or not ctrl.get("port"):
        return {
            "ok": False,
            "error": (
                f"MilliMap control endpoint not found at {CONTROL_PATH}. "
                "Make sure MilliMap is running with a dataset loaded."
            ),
        }
    host = ctrl.get("host", "127.0.0.1")
    port = int(ctrl["port"])
    url = f"http://{host}:{port}/tool"
    data = json.dumps({"name": name, "args": args}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        return {"ok": False, "error": f"connection failed: {exc.reason}"}
    except Exception as exc:
        return {"ok": False, "error": f"HTTP call failed: {exc}"}


def _load_snapshot() -> dict[str, Any]:
    if not SNAPSHOT_PATH.exists():
        return {
            "error": "no_snapshot",
            "message": (
                f"No MilliMap snapshot found at {SNAPSHOT_PATH}. "
                "Is MilliMap running with a dataset loaded?"
            ),
        }
    try:
        with SNAPSHOT_PATH.open("r") as f:
            return json.load(f)
    except Exception as exc:
        return {"error": "read_failed", "message": str(exc)}


def _fmt_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str)


@mcp.resource("millimap://session")
def session_resource() -> str:
    """Summary of the active MilliMap dataset and session."""
    snap = _load_snapshot()
    if "error" in snap:
        return _fmt_json(snap)
    return _fmt_json({
        "dataset": snap.get("dataset"),
        "n_cells": snap.get("n_cells"),
        "n_genes": snap.get("n_genes"),
        "assay": snap.get("assay"),
        "obs_columns": snap.get("obs_columns", []),
        "cluster_column": (snap.get("clusters") or {}).get("column"),
        "n_clusters": len((snap.get("clusters") or {}).get("sizes", {})),
        "n_annotations": len(snap.get("annotations", {})),
        "n_rois": len(snap.get("rois", [])),
    })


@mcp.resource("millimap://clusters")
def clusters_resource() -> str:
    """Cluster IDs and their cell counts."""
    return _fmt_json(_load_snapshot().get("clusters", {}))


@mcp.resource("millimap://annotations")
def annotations_resource() -> str:
    """Cluster → cell-type annotations assigned by the scientist in MilliMap."""
    return _fmt_json(_load_snapshot().get("annotations", {}))


@mcp.resource("millimap://markers")
def markers_resource() -> str:
    """Top marker genes per cluster from the most recent rank_genes_groups run."""
    return _fmt_json(_load_snapshot().get("markers", {}))


@mcp.resource("millimap://rois")
def rois_resource() -> str:
    """Regions of interest saved in the current MilliMap session."""
    return _fmt_json(_load_snapshot().get("rois", []))


@mcp.resource("millimap://analysis_cards")
def analysis_cards_resource() -> str:
    """Analysis result cards shown in MilliMap's workspace sidebar.

    Each entry is a summary — title, type, method, timestamp, dataset,
    and dataframe shape if applicable. Use the ``get_analysis_card`` tool
    with the card's ``id`` to read its full payload (including the
    underlying DataFrame).
    """
    return _fmt_json(_load_snapshot().get("analysis_cards", []))


@mcp.tool()
def get_cluster_markers(cluster_id: str, top_n: int = 10) -> str:
    """Return top marker genes for a specific cluster.

    Args:
        cluster_id: Cluster identifier as a string (e.g., "0", "1", "CD8_T").
        top_n: Number of top markers to return (default 10, max 15).
    """
    snap = _load_snapshot()
    if "error" in snap:
        return _fmt_json(snap)
    markers = snap.get("markers", {}).get(str(cluster_id))
    if not markers:
        return _fmt_json({
            "error": "not_found",
            "cluster_id": cluster_id,
            "available_clusters": sorted(snap.get("markers", {}).keys()),
        })
    annotation = snap.get("annotations", {}).get(str(cluster_id))
    return _fmt_json({
        "cluster_id": cluster_id,
        "annotation": annotation,
        "markers": markers[: max(1, min(top_n, 15))],
    })


@mcp.tool()
def genes_for_cell_type(cell_type: str) -> str:
    """Find which clusters are annotated as a given cell type, with their markers.

    Use this when the user asks things like "what genes are for T cells" —
    we find every cluster labelled with that cell type and return their
    marker genes.
    """
    snap = _load_snapshot()
    if "error" in snap:
        return _fmt_json(snap)
    needle = cell_type.strip().lower()
    hits: list[dict] = []
    for cid, label in snap.get("annotations", {}).items():
        if needle and needle in str(label).lower():
            hits.append({
                "cluster_id": cid,
                "annotation": label,
                "markers": snap.get("markers", {}).get(cid, [])[:10],
            })
    if not hits:
        return _fmt_json({
            "error": "not_found",
            "query": cell_type,
            "available_annotations": sorted(set(snap.get("annotations", {}).values())),
        })
    return _fmt_json({"query": cell_type, "matches": hits})


@mcp.tool()
def search_genes(query: str, limit: int = 20) -> str:
    """Case-insensitive search across marker genes. Returns matching genes and
    which cluster they mark.
    """
    snap = _load_snapshot()
    if "error" in snap:
        return _fmt_json(snap)
    needle = query.strip().lower()
    hits: list[dict] = []
    for cid, entries in snap.get("markers", {}).items():
        for entry in entries:
            gene = str(entry.get("gene", ""))
            if needle and needle in gene.lower():
                hits.append({
                    "gene": gene,
                    "cluster_id": cid,
                    "annotation": snap.get("annotations", {}).get(cid),
                    "score": entry.get("score"),
                    "log2fc": entry.get("log2fc"),
                })
                if len(hits) >= max(1, min(limit, 100)):
                    break
        if len(hits) >= max(1, min(limit, 100)):
            break
    return _fmt_json({"query": query, "hits": hits})


@mcp.tool()
def list_rois() -> str:
    """List all ROIs saved in the current MilliMap session."""
    return _fmt_json(_load_snapshot().get("rois", []))


@mcp.tool()
def list_analysis_cards() -> str:
    """List the analysis result cards visible in MilliMap's workspace sidebar.

    Same data as the ``millimap://analysis_cards`` resource — returns an
    array of summaries. Use ``get_analysis_card`` to load the full payload
    for a specific card.
    """
    return _fmt_json(_load_snapshot().get("analysis_cards", []))


@mcp.tool()
def get_analysis_card(card_id: str, max_rows: int = 50) -> str:
    """Fetch the full payload of one analysis result card, including its
    underlying DataFrame (up to ``max_rows`` rows, default 50).

    Use this to inspect the actual numbers behind a card — e.g. the
    differential expression table, spatial autocorrelation p-values,
    neighborhood enrichment z-scores — so you can reason over the result.

    Args:
        card_id: The ``id`` field from ``list_analysis_cards`` (a hex token).
        max_rows: Max rows of the DataFrame to include (1–500, default 50).
    """
    return _fmt_json(_post_tool("get_card_detail", {
        "card_id": card_id,
        "max_rows": max_rows,
    }))


# ── Write-path tools: drive MilliMap's analysis & UI ───────────────────────


@mcp.tool()
def run_clustering(resolution: float = 0.5, n_neighbors: int = 15) -> str:
    """Run MilliMap's clustering pipeline on the active dataset.

    Runs PCA → neighbors (n_neighbors) → Leiden (resolution) → UMAP using
    Scanpy and updates the 3D view in MilliMap with the new cluster labels.

    Args:
        resolution: Leiden resolution (higher = more clusters). Default 0.5.
        n_neighbors: k for the neighbors graph. Default 15.
    """
    return _fmt_json(_post_tool("run_clustering", {
        "resolution": resolution,
        "n_neighbors": n_neighbors,
    }))


@mcp.tool()
def find_markers(groupby: str = "clusters", method: str = "wilcoxon") -> str:
    """Run rank_genes_groups in MilliMap to find marker genes per cluster.

    After this completes, the MilliMap snapshot refreshes with the top
    markers per cluster — subsequent calls to get_cluster_markers or
    genes_for_cell_type will see them.

    Args:
        groupby: obs column to group by. Default 'clusters'.
        method: 'wilcoxon' (default), 't-test', or 'logreg'.
    """
    return _fmt_json(_post_tool("find_markers", {
        "groupby": groupby, "method": method,
    }))


@mcp.tool()
def annotate_cluster(cluster_id: str, label: str) -> str:
    """Set a cell-type annotation on a cluster in the running MilliMap session.

    The label appears in MilliMap's annotation panel and is written back to
    the session snapshot — use this when you've figured out what a cluster is.

    Args:
        cluster_id: Cluster identifier as shown in MilliMap (e.g. "Cluster 3", "1").
        label: Cell-type name (e.g. "CD8+ T cell", "fibroblast", "doublet").
    """
    return _fmt_json(_post_tool("annotate_cluster", {
        "cluster_id": cluster_id, "label": label,
    }))


@mcp.tool()
def score_gene_signature(genes: list[str], score_name: str = "mcp_score") -> str:
    """Score a gene signature across all cells and add it as an obs column.

    Use this to apply a published signature (e.g. exhausted T cell markers,
    EMT genes) to the dataset. The score becomes a colorable field in MilliMap.

    Args:
        genes: List of gene symbols to score together.
        score_name: Name for the new obs column (default 'mcp_score').
    """
    return _fmt_json(_post_tool("score_gene_signature", {
        "genes": genes, "score_name": score_name,
    }))


@mcp.tool()
def apply_qc_filter(
    min_genes: int = 200,
    max_genes: int = 6000,
    min_counts: int = 500,
    max_mito_pct: float = 20.0,
) -> str:
    """Apply QC filters to the active dataset in MilliMap.

    Replaces the active adata with the filtered subset and re-renders.
    The original can be restored via the in-app QC controls.
    """
    return _fmt_json(_post_tool("apply_qc_filter", {
        "min_genes": min_genes, "max_genes": max_genes,
        "min_counts": min_counts, "max_mito_pct": max_mito_pct,
    }))


@mcp.tool()
def run_millimap_tool(tool_name: str, tool_args_json: str = "{}") -> str:
    """Escape hatch — run any of MilliMap's 30+ analysis tools by name.

    Use when a workflow needs a tool not individually exposed above.

    Examples of tool_name:
        run_deg_clusters, run_deg_roi, run_go_enrichment,
        find_spatially_variable_genes, run_neighborhood_enrichment,
        run_co_occurrence, run_centrality_scores, run_interaction_matrix,
        run_ripley, run_ligrec, run_pca, run_louvain, run_diffmap,
        run_draw_graph, run_paga, run_dpt, run_embedding_density,
        run_doublet_detection, normalize_data, find_highly_variable_genes,
        score_cell_cycle, create_dotplot, create_heatmap,
        create_stacked_violin, annotate_clusters.

    Args:
        tool_name: Exact tool name from the list above.
        tool_args_json: JSON string of arguments, e.g. '{"group_a": "1", "group_b": "2"}'.
    """
    try:
        inner = json.loads(tool_args_json) if tool_args_json else {}
    except Exception as exc:
        return _fmt_json({"ok": False, "error": f"bad tool_args_json: {exc}"})
    return _fmt_json(_post_tool("run_tool", {
        "tool_name": tool_name, "tool_args": inner,
    }))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
