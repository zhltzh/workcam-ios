# WorkCam Design Tokens

## Direction A: "Field Pro" — Engineering-first, high-visibility

Corporate blue base with safety-orange accents. Optimized for outdoor
readability. Inspired by construction site signage and professional
engineering tools.

### Colors — Light Mode

| Token | Value | Usage |
|---|---|---|
| `--color-primary` | `#1a355e` | Navigation, primary buttons |
| `--color-primary-hover` | `#244a7c` | Hover states |
| `--color-primary-subtle` | `#e8eef7` | Selected item backgrounds |
| `--color-accent` | `#e8590c` | CTAs, active states |
| `--color-accent-hover` | `#f06c1a` | CTA hover |
| `--color-success` | `#2f9e44` | Verified states |
| `--color-warning` | `#e8a80c` | In-progress states |
| `--color-danger` | `#c92a2a` | Critical, destructive actions |
| `--color-danger-subtle` | `#fff0f0` | Critical issue backgrounds |
| `--color-bg` | `#f8f9fa` | Page background |
| `--color-surface` | `#ffffff` | Card surfaces |
| `--color-text-primary` | `#1b1e23` | Primary text |
| `--color-text-secondary` | `#5a6472` | Secondary text |
| `--color-text-tertiary` | `#9aa3af` | Placeholder text |
| `--color-border` | `#dee2e6` | Dividers, input borders |
| `--color-focus-ring` | `#2e6da4` | Focus outlines |

### Colors — Dark Mode

| Token | Value | Usage |
|---|---|---|
| `--color-primary` | `#5c9d ff` | Navigation, primary buttons |
| `--color-primary-hover` | `#7ab5ff` | Hover states |
| `--color-primary-subtle` | `#1a2a3a` | Selected item backgrounds |
| `--color-accent` | `#ff8c42` | CTAs, active states |
| `--color-bg` | `#0e1113` | Page background |
| `--color-surface` | `#1a1e23` | Card surfaces |
| `--color-text-primary` | `#e9ecef` | Primary text |
| `--color-text-secondary` | `#909aa3` | Secondary text |
| `--color-border` | `#2a3038` | Dividers, input borders |

### Typography

| Token | Value | Usage |
|---|---|---|
| `--font-family` | `-apple-system, BlinkMacSystemFont, "SF Pro Text", Roboto, sans-serif` | Body |
| `--font-family-mono` | `"SF Mono", "Fira Code", monospace` | SHA-256, IDs |
| `--text-xs` | `11px / 16px` | Captions, badges |
| `--text-sm` | `13px / 18px` | Secondary info |
| `--text-base` | `15px / 22px` | Body text |
| `--text-lg` | `17px / 24px` | Section headers |
| `--text-xl` | `20px / 28px` | Page titles |
| `--text-2xl` | `28px / 36px` | Hero text |

### Spacing & Radius

| Token | Value |
|---|---|
| `--space-xs` | `4px` |
| `--space-sm` | `8px` |
| `--space-md` | `16px` |
| `--space-lg` | `24px` |
| `--space-xl` | `32px` |
| `--radius-sm` | `6px` |
| `--radius-md` | `10px` |
| `--radius-lg` | `16px` |
| `--shadow-card` | `0 1px 3px rgba(0,0,0,0.08)` |
| `--shadow-pop` | `0 4px 16px rgba(0,0,0,0.12)` |

### Evidence Status Colors (all directions)

| Status | Color | Badge |
|---|---|---|
| `no_evidence` | `#9aa3af` (gray) | ○ No Evidence |
| `unverified` | `#e8a80c` (amber) | ◐ Unverified |
| `signature_valid` | `#5c7cfa` (blue) | ◐ Signature Valid |
| `cryptographically_valid` | `#2f9e44` (green) | ● Verified |
| `verification_failed` | `#c92a2a` (red) | ✕ Failed |
| `server_anchored` | `#7048e8` (purple) | ⬢ Anchored |

---

## Direction B: "Trust Slate" — Evidence-first, forensic

Muted dark-gray base with a single teal accent. Optimized for
evidence review and report reading. Inspired by legal/forensic
software and medical imaging viewers.

### Colors — Light Mode

| Token | Value |
|---|---|
| `--color-primary` | `#0c4a6e` |
| `--color-accent` | `#0d9488` |
| `--color-bg` | `#f1f5f9` |
| `--color-surface` | `#ffffff` |
| `--color-border` | `#cbd5e1` |
| `--color-text-primary` | `#0f172a` |
| `--color-text-secondary` | `#475569` |

### Colors — Dark Mode

| Token | Value |
|---|---|
| `--color-primary` | `#38bdf8` |
| `--color-accent` | `#2dd4bf` |
| `--color-bg` | `#020617` |
| `--color-surface` | `#0f172a` |
| `--color-border` | `#1e293b` |
| `--color-text-primary` | `#f1f5f9` |
| `--color-text-secondary` | `#94a3b8` |

---

## Direction C: "Signal Clean" — Minimal, iOS-native

Pure white/near-black base, SF symbols, no custom brand accent —
relies entirely on Apple system colors and iOS-native feel. Inspired
by Apple's own productivity apps (Notes, Files, Freeform).

### Colors — Light Mode

| Token | Value |
|---|---|
| `--color-primary` | `#007AFF` (Apple system blue) |
| `--color-accent` | `#34C759` (Apple system green) |
| `--color-bg` | `#F2F2F7` (Apple system gray 6) |
| `--color-surface` | `#FFFFFF` |
| `--color-border` | `#C6C6C8` |
| `--color-text-primary` | `#000000` |
| `--color-text-secondary` | `#3C3C4399` |

### Colors — Dark Mode

| Token | Value |
|---|---|
| `--color-primary` | `#0A84FF` |
| `--color-accent` | `#30D158` |
| `--color-bg` | `#000000` |
| `--color-surface` | `#1C1C1E` |
| `--color-border` | `#38383A` |
| `--color-text-primary` | `#FFFFFF` |
| `--color-text-secondary` | `#EBEBF599` |

---

## Shared Component Tokens

| Token | Value | Note |
|---|---|---|
| `--evidence-badge-radius` | `12px` | Pill-shaped status badges |
| `--evidence-badge-font` | `--text-xs, 600 weight` | Small, legible |
| `--photo-thumb-radius` | `8px` | Grid thumbnails |
| `--photo-grid-gap` | `2px` | Camera-roll tight grid |
| `--issue-card-radius` | `--radius-md` | Issue cards |
| `--annotation-stroke-default` | `4px` | Default stroke width |
| `--annotation-colors` | `#FF3B30, #FF9500, #FFCC00, #34C759, #007AFF, #AF52DE` | 6-color picker |
| `--map-pin-primary` | `--color-accent` | GPS marker color |

## iOS API Mapping

| Design Token | SwiftUI | UIKit |
|---|---|---|
| `--color-primary` | `Color("Primary")` or `.blue` | `UIColor.systemBlue` |
| `--color-bg` | `Color(.systemGroupedBackground)` | `.systemGroupedBackground` |
| `--color-surface` | `Color(.secondarySystemGroupedBackground)` | `.secondarySystemGroupedBackground` |
| `--font-family` | `.system` (default) | `UIFont.systemFont` |
| `--radius-md` | `cornerRadius(10)` | `layer.cornerRadius = 10` |
| `--shadow-card` | `.shadow(radius: 2, y: 1)` | `layer.shadowPath` |

> SwiftUI component reference code is in `design/swiftui/`.
