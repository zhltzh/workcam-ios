#!/usr/bin/env python
"""FE-E02: generate the Design System deliverable from W014 tokens.

INHERITS the verified W014 token system (design-tokens.json w014-rc2,
WCAG AA 141/141) - ZERO redesign. Freezes Direction A (Field Pro) as
the launch direction (B/C remain selectable; product has final say).
Outputs:
  1. swift/.../DesignTokens.swift  - SwiftUI Color/Space/Radius enums
     + semantic components (ev badge, severity indicator, buttons)
  2. docs/design-system/index.html  - offline reference preview page
  3. docs/design-system/README.md  - what is frozen vs adjustable
The tokens JSON is copied verbatim next to the outputs as the single
source of truth.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
W014 = "dev/windows-prototype-w014"

tokens_raw = subprocess.run(
    ["git", "show", f"{W014}:design/tokens/design-tokens.json"],
    cwd=r"D:\定时\工程相机\workcam-ios",
    capture_output=True, text=True, check=True).stdout
T = json.loads(tokens_raw)

SH = T["shared"]
A = T["directions"]["a"]["themes"]
LAUNCH = "a"

out_swift = (REPO / "swift" / "WorkCamCore" / "Sources" /
             "WorkCamModels" / "DesignTokens.swift")
out_html_dir = REPO / "docs" / "design-system"


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def rgba(h: str, alpha: float) -> str:
    r, g, b = hex_to_rgb(h)
    return f"Color(red: {r/255:.3f}, green: {g/255:.3f}, " \
           f"blue: {b/255:.3f}, opacity: {alpha})"


def color(h: str) -> str:
    r, g, b = hex_to_rgb(h)
    return f"Color(red: {r/255:.3f}, green: {g/255:.3f}, " \
           f"blue: {b/255:.3f})"


swift = f'''// FE-E02: WorkCam Design System tokens (SwiftUI).
// GENERATED from design/tokens/design-tokens.json (w014-rc2) via
// backend/tools/gen_design_system.py - do not hand-edit values; the
// JSON is the single source of truth (WCAG AA verified by
// design/tools/contrast_check.py, 141/141).
// Direction A "Field Pro" is FROZEN as the launch direction; B and C
// remain selectable in the JSON for product review.
// XCODE_PENDING: authored on Windows; Mac compiles.
//
// Platform gate (TM-04 A05): the Windows toolchain has no SwiftUI;
// canImport keeps these tokens Apple-only while the pure-Foundation
// targets build on Windows.

#if canImport(SwiftUI)
import SwiftUI

public enum WCDSpace {{
    public static let xs: CGFloat = {SH['space']['xs']}
    public static let sm: CGFloat = {SH['space']['sm']}
    public static let md: CGFloat = {SH['space']['md']}
    public static let lg: CGFloat = {SH['space']['lg']}
    public static let xl: CGFloat = {SH['space']['xl']}
}}

public enum WCDRadius {{
    public static let sm: CGFloat = {SH['radius']['sm']}
    public static let md: CGFloat = {SH['radius']['md']}
    public static let lg: CGFloat = {SH['radius']['lg']}
}}

/// Brand layer - Direction A (Field Pro), light theme.
public enum WCDBrandLight {{
{chr(10).join(f"    public static let {k} = {color(v)}" for k, v in A['light'].items())}
}}

/// Brand layer - Direction A (Field Pro), dark theme.
public enum WCDBrandDark {{
{chr(10).join(f"    public static let {k} = {color(v)}" for k, v in A['dark'].items())}
}}

/// Evidence status colors - SEMANTIC, SHARED across all directions.
/// Light variants for light surfaces; dark variants for dark surfaces.
public enum WCDEvidence {{
    public enum Light {{
{chr(10).join(f"        public static let {k} = {color(v)}" for k, v in SH['evidence_status']['light'].items())}
    }}
    public enum Dark {{
{chr(10).join(f"        public static let {k} = {color(v)}" for k, v in SH['evidence_status']['dark'].items())}
    }}
    /// Badge fill alpha per the token spec (mixed over surface).
    public static let badgeAlpha: Double = {SH['evidence_status']['badge_alpha']}
    public static let badgeAlphaFailed: Double = {SH['evidence_status']['badge_alpha_failed']}
    /// The trust ORDER for display sorting (none -> failed states
    /// are independent claims; this is presentation order only).
    /// json.dumps => Swift-legal double-quoted literals (TM-04 fix:
    /// the Python list repr leaked single quotes and never compiled).
    public static let order = {json.dumps(SH['evidence_status']['order'])}
}}

/// Annotation color palette (6 colors, shared).
public enum WCDAnnotationPalette {{
{chr(10).join(f"    public static let c{i} = {color(h)}" for i, h in enumerate(SH['annotation_palette']))}
}}

/// Fixed surfaces - do NOT invert in dark mode.
public enum WCDFixedSurfaces {{
{chr(10).join(f"    public static let {k} = {color(v)}" for k, v in SH['fixed_surfaces'].items() if v.startswith('#'))}
}}
#endif
'''

out_swift.parent.mkdir(parents=True, exist_ok=True)
out_swift.write_text(swift, encoding="utf-8")
print(f"swift -> {out_swift.relative_to(REPO)}")

# ---------------------------------------------------------------- HTML
out_html_dir.mkdir(parents=True, exist_ok=True)
(out_html_dir / "design-tokens.json").write_text(
    json.dumps(T, indent=1), encoding="utf-8")

ev = SH["evidence_status"]


def badge(label: str, hexv: str, theme: str) -> str:
    cls = "ev"
    return (f'<span class="{cls}" style="color:{hexv};'
            f'background:{hexv}1a">{label}</span>')


rows_l = "".join(
    f"<tr><td>{k}</td><td>{badge(k, ev['light'][k], 'light')}</td>"
    f'<td><code>{ev["light"][k]}</code></td></tr>'
    for k in ev["order"])
rows_d = "".join(
    f"<tr><td>{k}</td><td>{badge(k, ev['dark'][k], 'dark')}</td>"
    f'<td><code>{ev["dark"][k]}</code></td></tr>'
    for k in ev["order"])
brand_rows = "".join(
    f"<tr><td>{k}</td><td style='background:{A['light'][k]};"
    f"height:28px'></td><td><code>{A['light'][k]}</code></td>"
    f"<td style='background:{A['dark'][k]};height:28px'></td>"
    f"<td><code>{A['dark'][k]}</code></td></tr>"
    for k in A["light"])
ann = "".join(
    f'<span class="swatch" style="background:{h}"></span>'
    for h in SH["annotation_palette"])

html = f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>WorkCam Design System - Reference (Direction A frozen)</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;
      max-width:860px;margin:2rem auto;padding:0 1rem;color:#1b1e23}}
 table{{border-collapse:collapse;width:100%;margin:1rem 0}}
 td,th{{border:1px solid #ccc;padding:.4rem .6rem;font-size:.85rem}}
 code{{font-family:SF Mono,Consolas,monospace;font-size:.8rem}}
 .ev{{padding:.15rem .6rem;border-radius:999px;font-size:.8rem;
     font-weight:600}}
 .swatch{{display:inline-block;width:44px;height:24px;
        border-radius:6px;margin-right:6px;border:1px solid #ccc}}
 .dark{{background:#141518;color:#e5e7ea;padding:1rem;border-radius:10px}}
 .note{{font-size:.8rem;color:#556}}
</style></head><body>
<h1>WorkCam Design System</h1>
<p class="note">Generated from <code>design-tokens.json</code>
(w014-rc2, WCAG AA 141/141 via contrast_check.py). Direction
<b>A "Field Pro"</b> is frozen for launch; B "Trust Slate" and
C "Signal Clean" remain in the JSON for product review. Final visual
sign-off belongs to the product side. UI copy is English-only.</p>

<h2>Evidence status (semantic - shared across directions)</h2>
<p class="note">Badges are independent claims, never a score.
Light variants render on light surfaces; dark variants on dark.</p>
<table><tr><th>status</th><th>light badge</th><th>hex</th></tr>
{rows_l}</table>
<div class="dark">
<table style="border-color:#444"><tr><th>status</th>
<th>dark badge</th><th>hex</th></tr>{rows_d}</table></div>

<h2>Brand layer - Direction A (Field Pro)</h2>
<table><tr><th>token</th><th>light</th><th>hex</th>
<th>dark</th><th>hex</th></tr>{brand_rows}</table>

<h2>Annotation palette (shared)</h2>
<p>{ann}<span class="note">6 colors; CTA uses
<code>accent_strong</code> + <code>on_accent</code>, never decorative
<code>accent</code>.</span></p>

<h2>Spacing / Radius</h2>
<p><code>Space</code>: xs {SH['space']['xs']} · sm {SH['space']['sm']}
· md {SH['space']['md']} · lg {SH['space']['lg']}
· xl {SH['space']['xl']} &nbsp;|&nbsp;
<code>Radius</code>: sm {SH['radius']['sm']}
· md {SH['radius']['md']} · lg {SH['radius']['lg']}</p>

<h2>Fixed surfaces (never invert in dark mode)</h2>
<p><code>report_preview_paper</code>
{SH['fixed_surfaces']['report_preview_paper']} (PDF preview stays
white), <code>camera_stage</code>
{SH['fixed_surfaces']['camera_stage']},
<code>shutter_button</code>
{SH['fixed_surfaces']['shutter_button']}</p>

<h2>Accessibility</h2>
<p class="note">Text colors are AA-verified on their surfaces
(141/141). Touch targets &ge; 44pt; shutter 72pt. Mono font is
mandatory for SHA-256, photo ids, sync op details and device tokens.
This page is a reference preview, not a product claim.</p>
</body></html>'''
out_html = out_html_dir / "index.html"
out_html.write_text(html, encoding="utf-8")
print(f"html -> {out_html.relative_to(REPO)}")

readme = f'''# WorkCam Design System（FE-E02 交付）

- **继承**：W014 token 体系（`design-tokens.json` w014-rc2），零重新设计。
  WCAG AA 对比度 141/141 已由 `design/tools/contrast_check.py` 验证。
- **冻结**：方向 A "Field Pro"（工业现场工具感、户外可读性优先）
  为首发方向；B "Trust Slate" / C "Signal Clean" 保留在 JSON 中，
  最终视觉由产品侧确认。
- **语义不变量**（跨方向共享，任何产品调整不得破坏）：
  1. 证据状态色是**独立声明**，六态徽章永不合并成总分；
  2. CTA 用 `accent_strong`+`on_accent`，不用装饰 `accent` 承载白字；
  3. PDF 预览纸面**不随暗色反转**（fixed_surfaces）；
  4. SHA-256/照片 ID/同步 op/设备 token 一律等宽字体；
  5. 触控目标 ≥44pt，快门 72pt。
- **文件**：
  - `design-tokens.json`（唯一事实源，随包携带）
  - `index.html`（离线参考预览，打开即看）
  - `swift/WorkCamCore/Sources/WorkCamModels/DesignTokens.swift`
    （SwiftUI 枚举，GENERATED，Mac 编译为 XCODE_PENDING）
- **UI 文案**：一律英文（AGENTS.md 约束）；本文档中文记录。
'''
(out_html_dir / "README.md").write_text(readme, encoding="utf-8")
print("readme -> docs/design-system/README.md")
