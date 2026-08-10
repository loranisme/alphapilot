"""Self-contained HTML scorecard: inline-SVG charts, no external resources, no JS.

Renders a benchmark-comparison view (grouped bars, correlation heatmap,
IC-by-horizon lines) as a single deterministic HTML string. Theme-neutral so it
opens identically anywhere. Metrics only — no verdict.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

_PALETTE = ["#2f6f9f", "#c9772e", "#5a9367", "#8a5a9e", "#a03d3d", "#4a4a4a"]

def _esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _diverging(value: float) -> str:
    # +1 -> blue, 0 -> white, -1 -> red
    v = max(-1.0, min(1.0, float(value)))
    if v >= 0:
        r, g, b = 255 - 155*v, 255 - 90*v, 255 - 40*v
    else:
        r, g, b = 255 + 40*v, 255 + 90*v, 255 - 40*v
    clamp = lambda c: max(0, min(255, int(c)))
    return f"rgb({clamp(r)},{clamp(g)},{clamp(b)})"

def _bar_chart(factor_tbl: pd.DataFrame, metrics: list[str], width=680, row_h=26) -> str:
    labels = [str(x) for x in factor_tbl["factor"].tolist()]
    groups = [(m, [float(x) for x in factor_tbl[m].tolist()]) for m in metrics if m in factor_tbl.columns]
    if not groups:
        return ""
    all_vals = [v for _, vals in groups for v in vals if np.isfinite(v)]
    lo, hi = (min(all_vals+[0.0]), max(all_vals+[0.0])) if all_vals else (0.0, 1.0)
    span = (hi - lo) or 1.0
    left, chart_w = 130, width - 170
    zero_x = left + (0 - lo) / span * chart_w
    n = len(groups)
    height = 30 + len(labels) * (row_h * n + 8)
    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">']
    y = 24
    for li, lab in enumerate(labels):
        parts.append(f'<text x="8" y="{y+row_h*n/2}" font-size="12" fill="#333">{_esc(lab)}</text>')
        for gi, (m, vals) in enumerate(groups):
            v = vals[li] if np.isfinite(vals[li]) else 0.0
            x = left + (min(v,0) - lo)/span*chart_w
            w = abs(v)/span*chart_w
            parts.append(f'<rect x="{x:.1f}" y="{y+gi*row_h}" width="{max(1,w):.1f}" height="{row_h-6}" fill="{_PALETTE[gi%len(_PALETTE)]}"/>')
            parts.append(f'<text x="{left+chart_w+6}" y="{y+gi*row_h+row_h-8}" font-size="10" fill="#555">{m}={v:.3f}</text>')
        y += row_h*n + 8
    parts.append(f'<line x1="{zero_x:.1f}" y1="16" x2="{zero_x:.1f}" y2="{height-6}" stroke="#999" stroke-width="0.5"/>')
    parts.append("</svg>")
    return "".join(parts)

def _heatmap(corr: pd.DataFrame, cell=54) -> str:
    names = [str(c) for c in corr.columns]
    n = len(names)
    pad = 90
    size = pad + n*cell
    parts = [f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" role="img">']
    for j, cj in enumerate(names):
        parts.append(f'<text x="{pad+j*cell+cell/2}" y="{pad-6}" font-size="10" fill="#333" text-anchor="middle">{_esc(cj[:8])}</text>')
        parts.append(f'<text x="{pad-6}" y="{pad+j*cell+cell/2}" font-size="10" fill="#333" text-anchor="end">{_esc(cj[:8])}</text>')
    for i in range(n):
        for j in range(n):
            v = float(corr.iloc[i, j]) if np.isfinite(corr.iloc[i, j]) else 0.0
            x, y = pad+j*cell, pad+i*cell
            parts.append(f'<rect x="{x}" y="{y}" width="{cell-1}" height="{cell-1}" fill="{_diverging(v)}" stroke="#fff"/>')
            parts.append(f'<text x="{x+cell/2}" y="{y+cell/2+3}" font-size="10" fill="#222" text-anchor="middle">{v:.2f}</text>')
    parts.append("</svg>")
    return "".join(parts)

def _line_chart(ic_by_h: pd.DataFrame, width=680, height=260) -> str:
    horizons = [float(h) for h in ic_by_h.index]
    if not horizons:
        return ""
    left, right, top, bot = 50, width-120, 20, height-30
    xs = {h: left + (i/(max(1,len(horizons)-1)))*(right-left) for i, h in enumerate(horizons)}
    vals = [float(v) for v in ic_by_h.to_numpy().ravel() if np.isfinite(v)]
    lo, hi = (min(vals+[0.0]), max(vals+[0.0])) if vals else (0.0, 1.0)
    span = (hi-lo) or 1.0
    def py(v): return bot - (v-lo)/span*(bot-top)
    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">']
    parts.append(f'<line x1="{left}" y1="{py(0):.1f}" x2="{right}" y2="{py(0):.1f}" stroke="#999" stroke-width="0.5"/>')
    for h in horizons:
        parts.append(f'<text x="{xs[h]:.1f}" y="{height-12}" font-size="10" fill="#555" text-anchor="middle">h={int(h)}</text>')
    for ci, col in enumerate(ic_by_h.columns):
        color = _PALETTE[ci%len(_PALETTE)]
        pts = " ".join(f"{xs[h]:.1f},{py(float(ic_by_h.loc[h,col])):.1f}" for h in horizons if np.isfinite(ic_by_h.loc[h,col]))
        if pts:
            parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5"/>')
            parts.append(f'<text x="{right+6}" y="{20+ci*16}" font-size="11" fill="{color}">{_esc(str(col)[:10])}</text>')
    parts.append("</svg>")
    return "".join(parts)

def render_scorecard_html(summary: str, factor_tbl: pd.DataFrame, corr: pd.DataFrame,
                          ic_by_horizon: pd.DataFrame, title: str = "因子验证记分卡") -> str:
    css = ("body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#fafafa;color:#222;margin:24px}"
           "h1{font-size:20px}h2{font-size:15px;margin-top:28px;border-bottom:1px solid #ddd;padding-bottom:4px}"
           ".note{color:#666;font-size:13px}.summary{background:#f0f4f8;border-left:3px solid #2f6f9f;padding:10px 14px;font-size:14px}")
    body = [
        f"<style>{css}</style>",
        f"<h1>{_esc(title)}</h1>",
        f'<div class="summary">{_esc(summary)}</div>',
        '<p class="note">纯指标汇总，无判决 (no verdict)。</p>',
        "<h2>各因子指标对比</h2>", _bar_chart(factor_tbl, ["rank_ic","icir_annualized","portfolio_sharpe"]),
        "<h2>相关性热力图</h2>", _heatmap(corr),
        "<h2>IC by horizon</h2>", _line_chart(ic_by_horizon),
    ]
    return "<!doctype html><meta charset='utf-8'>" + "".join(body) + "\n"
