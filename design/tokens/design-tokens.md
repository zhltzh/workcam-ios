# WorkCam Design Tokens (Human-Readable Reference)

> **机器事实源：`design/tokens/design-tokens.json`（w014-rc2）。**
> 本文档是其人类可读摘要，供评审与手写代码参考；两者不一致时以 JSON 为准。
> 所有色值均通过 `design/tools/contrast_check.py` 的 WCAG 2.1 AA 审计（硬检查 141/141）。

## Token 架构

- `shared`：跨方向共享（间距/圆角/阴影/字体/**证据状态色（亮暗双变体）**/标注色板/严重度映射/问题 chip/hover 规则/恒定表面）。
- `directions.{a|b|c}.themes.{light|dark}`：品牌层（primary 系/accent 系/背景/文字/边框）。
- 证据状态色与品牌色正交：任何方向下语义一致，只有亮暗变体之分。

## 方向

| Key | 名称 | 意图 |
|---|---|---|
| `a` | Field Pro | 深蓝权威 + 安全橙，工业现场工具感，户外可读优先 |
| `b` | Trust Slate | 冷岩蓝 + 青绿，文档化、合规卷宗气质 |
| `c` | Signal Clean | iOS 系统色对齐，零学习成本（注：系统绿不能承载白字，CTA 用加深绿） |

## Colors — Direction A "Field Pro"

| Token | Light | Dark |
|---|---|---|
| `primary` | `#1a355e` | `#5c9dff` |
| `primary_hover` | `#244a7c` | `#7ab5ff` |
| `primary_subtle` | `#e8eef7` | `#1a2a3a` |
| `primary_ink`（subtle 底上的文字） | `#1a355e` | `#5c9dff` |
| `accent`（装饰） | `#e8590c` | `#ff8c42` |
| `accent_strong`（CTA 底） | `#c2410c` | `#ff8c42` |
| `accent_strong_hover` | `#ad3b09` | `#ffa05c` |
| `on_accent`（CTA 标签） | `#ffffff` | `#0e1113` |
| `bg` / `surface` / `border` | `#f8f9fa` / `#ffffff` / `#dee2e6` | `#0e1113` / `#1a1e23` / `#2a3038` |
| `text_1` / `text_2` / `text_3` | `#1b1e23` / `#5a6472` / `#66707d` | `#e9ecef` / `#909aa3` / `#8b95a1` |

## Colors — Direction B "Trust Slate"

| Token | Light | Dark |
|---|---|---|
| `primary` / `primary_hover` / `primary_subtle` | `#0c4a6e` / `#155e8a` / `#e0f2fe` | `#38bdf8` / `#7dd3fc` / `#0c2638` |
| `primary_ink` | `#0c4a6e` | `#38bdf8` |
| `accent` | `#0d9488` | `#2dd4bf` |
| `accent_strong` / `accent_strong_hover` | `#0f766e` / `#0c635c` | `#2dd4bf` / `#55e0cc` |
| `on_accent` | `#ffffff` | `#02120f` |
| `bg` / `surface` / `border` | `#f1f5f9` / `#ffffff` / `#cbd5e1` | `#020617` / `#0f172a` / `#1e293b` |
| `text_1` / `text_2` / `text_3` | `#0f172a` / `#475569` / `#5c6a7e` | `#f1f5f9` / `#b6c2d2` / `#94a3b8` |

## Colors — Direction C "Signal Clean"

| Token | Light | Dark |
|---|---|---|
| `primary` / `primary_hover` / `primary_subtle` | `#007AFF` / `#0056b3` / `#e3f0ff` | `#0A84FF` / `#409cff` / `#0a1a2a` |
| `primary_ink` | `#0056b3` | `#409cff` |
| `accent` | `#34C759` | `#30D158` |
| `accent_strong` / `accent_strong_hover` | `#1e7a34` / `#186a2e` | `#30D158` / `#45e06d` |
| `on_accent` | `#ffffff` | `#00140a` |
| `bg` / `surface` / `border` | `#F2F2F7` / `#FFFFFF` / `#C6C6C8` | `#000000` / `#1C1C1E` / `#38383A` |
| `text_1` / `text_2` / `text_3` | `#000000` / `#3c3c43` / `#6d6d72` | `#FFFFFF` / `#ebebf5` / `#8e8e93` |

## 证据状态色（shared，亮暗双变体）

语义与文案见 `design/specs/ui-spec.md` §4；徽章底 = 状态色与 surface 的 `color-mix`（15%；failed 12%）。

| Status | Light | Dark | Badge 文案（EN） |
|---|---|---|---|
| `none` | `#5b6470` | `#a3abb5` | No evidence |
| `unverified` | `#8a5f00` | `#ffc94d` | Awaiting verification |
| `signature_valid` | `#3b5bdb` | `#7a95ff` | Signature valid |
| `verified` | `#1e6f38` | `#51cf66` | Verified |
| `failed` | `#c92a2a` | `#ff6b6b` | Verification failed |
| `anchored` | `#5f3dc4` | `#a58af9` | Time-anchored |

严重度映射：low→`none`，medium→`unverified`，high→`accent_strong`，critical→`failed`。
问题 chip：open→`primary_ink`，in_progress→`unverified`，resolved→`verified`，closed→`text_3` on `bg`。

## Typography

| Token | Value | Usage |
|---|---|---|
| `--font-family` | `-apple-system, BlinkMacSystemFont, "SF Pro Text", Segoe UI, Roboto, sans-serif` | Body |
| `--font-family-mono` | `"SF Mono", "Fira Code", Consolas, monospace` | SHA-256、photo id、sync op、device token |
| `--text-xs` | `11px / 16px` | 徽章、caption |
| `--text-sm` | `13px / 18px` | 次要信息 |
| `--text-base` | `15px / 22px` | 正文 |
| `--text-lg` | `17px / 24px` | 分节标题 |
| `--text-xl` | `20px / 28px` | 页面标题 |

## Spacing · Radius · Shadow

| Token | Value |
|---|---|
| `--space-xs/sm/md/lg/xl` | `4 / 8 / 16 / 24 / 32px` |
| `--radius-sm/md/lg` | `6 / 10 / 16px` |
| `--shadow-card` | `0 1px 3px rgba(0,0,0,0.08)` |
| `--shadow-pop` | `0 4px 16px rgba(0,0,0,0.12)` |

## 共享组件 tokens

| Token | Value | 说明 |
|---|---|---|
| evidence badge | `--text-xs, 600`，胶囊 | 11px semibold |
| photo grid gap | `8px`（原型 `--space-sm`） | 网格缩略 |
| annotation 默认描边 | `4px`（规格允许 1..40） | 见 w013-annotation-spec |
| `annotation_palette` | `#FF3B30 #FF9500 #FFCC00 #34C759 #007AFF #AF52DE` | 六色取色器 |
| 恒定表面 | 报告纸面 `#ffffff`/`#1b1e23`；相机 HUD 与照片角标 `rgba(0,0,0,.6)`；快门 `#ffffff` | 报告纸面不随暗色主题反转 |

## iOS API 映射（摘要）

完整版见 `design/swiftui/swiftui-mapping.md`。

| Design Token | SwiftUI |
|---|---|
| 主题双变体 | Asset Catalog Color Set 的 Any / Dark Appearance |
| `bg` / `surface` | `Color(.systemGroupedBackground)` / `Color(.secondarySystemGroupedBackground)` |
| `radius-md` | `.clipShape(RoundedRectangle(cornerRadius: 10))` |
| `--font-family-mono` | `.system(.footnote, design: .monospace)` |
