#!/usr/bin/env python3
"""WCAG contrast audit for WorkCam W014 design tokens.

Reads design/tokens/design-tokens.json and reports contrast ratios for every
foreground/background pair the prototype actually renders, with the WCAG 2.1
threshold that applies to it.

Hard checks (counted): body text (AA 4.5), labels on CTAs and chips (AA 4.5),
semantic UI color bars (AA 3.0), photo-overlay labels evaluated against the
worst case (a fully white photo).

Advisory checks (reported, not counted): decorative card borders. WCAG 1.4.11
only requires 3:1 for boundaries of interactive components; the prototype has
no bordered inputs, and separators/card edges are decorative.

Run:  python design/tools/contrast_check.py
Exit code 0 = all hard checks pass, 1 = at least one hard failure.
"""

import json
import sys
from pathlib import Path

TOKENS_PATH = Path(__file__).resolve().parents[1] / "tokens" / "design-tokens.json"

AA_NORMAL = 4.5
AA_UI = 3.0


def hex_to_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def srgb_to_linear(channel):
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb):
    r, g, b = (srgb_to_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg_rgb, bg_rgb):
    l1 = relative_luminance(fg_rgb)
    l2 = relative_luminance(bg_rgb)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def blend(fg_rgb, alpha, bg_rgb):
    return tuple(f * alpha + b * (1 - alpha) for f, b in zip(fg_rgb, bg_rgb))


def main():
    tokens = json.loads(TOKENS_PATH.read_text(encoding="utf-8"))
    shared = tokens["shared"]
    ev_light = {k: hex_to_rgb(v) for k, v in shared["evidence_status"]["light"].items()}
    ev_dark = {k: hex_to_rgb(v) for k, v in shared["evidence_status"]["dark"].items()}

    hard, advisory = [], []

    for dir_key, direction in tokens["directions"].items():
        for theme_key, theme in direction["themes"].items():
            label = f"{dir_key.upper()} {theme_key}"
            bg = hex_to_rgb(theme["bg"])
            surface = hex_to_rgb(theme["surface"])
            subtle = hex_to_rgb(theme["primary_subtle"])
            primary_ink = hex_to_rgb(theme["primary_ink"])
            strong = hex_to_rgb(theme["accent_strong"])
            strong_h = hex_to_rgb(theme["accent_strong_hover"])
            on_accent = hex_to_rgb(theme["on_accent"])
            border = hex_to_rgb(theme["border"])
            ev = ev_light if theme_key == "light" else ev_dark

            for text_key in ("text_1", "text_2", "text_3"):
                fg = hex_to_rgb(theme[text_key])
                hard.append((f"{label} · {text_key} on bg", fg, bg, AA_NORMAL))
                hard.append((f"{label} · {text_key} on surface", fg, surface, AA_NORMAL))

            hard.append((f"{label} · nav active primary_ink on primary_subtle", primary_ink, subtle, AA_NORMAL))
            hard.append((f"{label} · nav idle text_2 on surface", hex_to_rgb(theme["text_2"]), surface, AA_NORMAL))

            # CTA labels.
            hard.append((f"{label} · on_accent on accent_strong (btn-primary)", on_accent, strong, AA_NORMAL))
            hard.append((f"{label} · on_accent on accent_strong_hover", on_accent, strong_h, AA_NORMAL))

            # Evidence status badges: 11px bold labels on alpha-blended surface.
            for status, rgb in ev.items():
                alpha = shared["evidence_status"]["badge_alpha_failed" if status == "failed" else "badge_alpha"]
                hard.append((f"{label} · badge {status} on blended surface", rgb, blend(rgb, alpha, surface), AA_NORMAL))

            # Issue status chips.
            hard.append((f"{label} · chip open (primary_ink) on blended surface", primary_ink,
                         blend(primary_ink, 0.15, surface), AA_NORMAL))

            # Error banner: failed color on 10% failed blended over surface.
            hard.append((f"{label} · error-banner text on blended surface", ev["failed"],
                         blend(ev["failed"], 0.1, surface), AA_NORMAL))

            # Severity indicator bars are semantic UI (non-text) — 3:1 vs surface.
            hard.append((f"{label} · sev bar low vs surface", ev["none"], surface, AA_UI))
            hard.append((f"{label} · sev bar medium vs surface", ev["unverified"], surface, AA_UI))
            hard.append((f"{label} · sev bar high vs surface", strong, surface, AA_UI))
            hard.append((f"{label} · sev bar critical vs surface", ev["failed"], surface, AA_UI))

            # Decorative borders — advisory only.
            advisory.append((f"{label} · border vs surface (decorative)", border, surface, AA_UI))

    # Report paper is always light, even when the app is dark.
    paper = hex_to_rgb(shared["fixed_surfaces"]["report_preview_paper"])
    paper_text = hex_to_rgb(shared["fixed_surfaces"]["report_preview_text"])
    hard.append(("paper · report text on paper", paper_text, paper, AA_NORMAL))
    for status, rgb in ev_light.items():
        hard.append((f"paper · badge {status} on white paper", rgb, paper, AA_NORMAL))

    # Photo overlays: white label over rgba(0,0,0,0.6) on a worst-case pure white photo.
    overlay_bg = blend((0, 0, 0), 0.6, (255, 255, 255))
    hard.append(("overlay · white label on photo-badge (worst case)", (255, 255, 255), overlay_bg, AA_NORMAL))
    hard.append(("overlay · white label on camera-hud (worst case)", (255, 255, 255), overlay_bg, AA_NORMAL))

    def report(rows, is_advisory):
        out = []
        for name, fg, bg, threshold in rows:
            ratio = contrast_ratio(fg, bg)
            tag = "ADV" if is_advisory else ("PASS" if ratio >= threshold else "**FAIL**")
            out.append((name, ratio, threshold, tag))
        return out

    rows = report(advisory, True) + report(hard, False)
    rows.sort(key=lambda r: r[3] != "**FAIL**")

    width = max(len(r[0]) for r in rows)
    print(f"| {'Check':<{width}} | Ratio  | Required | Result |")
    print(f"|{'-' * (width + 4)}|--------|----------|--------|")
    for name, ratio, threshold, tag in rows:
        print(f"| {name:<{width}} | {ratio:5.2f}:1 | {threshold:>4.1f}:1   | {tag} |")

    failures = sum(1 for r in rows if r[3] == "**FAIL**")
    hard_total = len(hard)
    print(f"\nHard checks: {hard_total - failures}/{hard_total} passed. "
          f"Advisory (not counted): {len(advisory)} reported above.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
