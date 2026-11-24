# 原型 → SwiftUI 映射指南（W014 → W017）

版本：w014-rc1 · 2026-11-25 · 输入：`design/prototype/index.html` + `design/tokens/design-tokens.json`

约束（继承 AGENTS.md）：原生 Swift/SwiftUI、系统框架优先、零第三方依赖、不加 entitlements；未实现的功能标注 Not Implemented。

## 1. Token → SwiftUI

`design-tokens.json` 的双变体结构直接映射 **Asset Catalog 双外观颜色**：

| JSON | Asset Catalog |
|---|---|
| `directions.X.themes.light.<token>` | Color Set 的 *Any Appearance* |
| `directions.X.themes.dark.<token>` | Color Set 的 *Dark Appearance* |

- 颜色资产名与 JSON 键一致（`primary` / `primaryInk` / `accentStrong` / `onAccent` / `bg` / `surface` / `border` / `text1` / `text2` / `text3` / `evNone` / `evUnverified` / `evSigValid` / `evVerified` / `evFailed` / `evAnchored`）。
- 三方向并存时按"已定稿方向"取一套资产；**证据状态色资产在任何方向下共享同一语义**。
- 间距/圆角：`Space.xs…xl` 与 `Radius.sm/md/lg` 两个 `CGFloat` 枚举，值直接取自 `shared`。
- 字体：`Font.system(.body…)` + `Font.system(.footnote, design: .monospace)`；mono 固定用于 SHA-256 / photo id / sync op / device token。
- 徽章混合底：`Color.evUnverified.opacity(0.15)` 叠加在 `surface` 之上近似 `color-mix`；准确实现用 `UIColor` blend 扩展（15%/12% failed），对比度以 `contrast_check.py` 为准绳重跑不得回退。

## 2. 组件映射表

| 原型类 | SwiftUI | 说明 |
|---|---|---|
| `.app-shell` 侧栏 | iPhone：`TabView`；iPad：`NavigationSplitView` | 原型侧栏是桌面隐喻，iPhone 以 Tab 承载 |
| `.card` | `GroupBox` / 自定义 `ViewModifier`（surface 底、`.clipShape(RoundedRectangle(cornerRadius: 10))`、shadow-card） | |
| `.project-card` | `NavigationLink` 包裹的卡片 `VStack` | hover 浮起仅桌面，iOS 忽略 |
| `.ev-badge` | `Label` + `.textCase`，`Capsule` 填充混合底，11pt semibold | 语义文案见 ui-spec §4，不可改写 |
| `.sev-indicator` | `Rectangle().frame(width: 4)` 或 `borderColor` 指示 | 无文字，纯 UI 组件（3:1 已验证） |
| `.issue-status-chip` | 同 ev-badge 的胶囊封装，色由 status 映射 | |
| `.btn-primary` | `.borderedProminent` + tint `accentStrong`，前景 `onAccent` | hover 语义 → iOS 无 hover，仅保留按压反馈 |
| `.btn-secondary` | `.bordered` | |
| `.shutter-btn` | `Button` + `Circle()` 白描边（72pt），`.scaleEffect(pressed ? 0.92 : 1)` | |
| `.camera-hud-item` | 取景层 `overlay(alignment:)`，`rgba(0,0,0,.6)` 胶囊白字 | HUD 文案为 client-claimed |
| `.photo-badge` | 同上 | |
| `.sync-item` | `List` 行：op 名 mono + 摘要 + 状态徽章 | |
| `.error-banner` | 顶部 `banner` ViewModifier（failed 10% 底/30% 边） | |
| `.report-preview` | `PDFKit` `PDFView`（服务端 PDF 原样渲染）或 `ScrollView` 纸面排版 | **不随暗色反转** |
| `.empty-state` | `ContentUnavailableView`（iOS 17+） | |
| 内联 SVG 图标 | SF Symbols：folder / camera / exclamationmark.triangle / doc / arrow.clockwise / gearshape | 原型图标为 Feather 线条风，SF Symbols 用 Regular weight 对应 |

## 3. 导航映射

- 8 页 → `TabView`：Projects / Camera（中央大按钮）/ Issues / Reports；Sync、Settings 收进工具栏或列表入口（对应原型 Evidence 分组）。
- Photo Detail / Report Preview 为 push 目标（`NavigationStack`）。
- 遵循 flows `user-flows.md`：**不可达操作不渲染**（closed 问题无操作按钮、归档项目无写入口）。

## 4. 主题与方向

- 深色：跟随系统（`@Environment(\.colorScheme)`），不提供应用内切换器——原型切换器是评审工具，**不进产品**。
- 三方向仅一套进 RC2；评审记录（screenshots/）保留三方向证据。

## 5. 诚实性检查表（W017 验收对照）

- [ ] `Signature valid ≠ capture scene authentic` 文案逐字保留
- [ ] attestation 未实现 → UI 显示 `unknown (App Attest not implemented)`
- [ ] `captured_at` / `device_id` 显示为 client-claimed, untrusted
- [ ] 未实现的页面显示 Not Implemented，不放假数据
- [ ] 同步 op 名与 W008 冻结名单一致
- [ ] 报告页保留 "reference document, not a legal certification"
- [ ] 视觉改动后重跑 `design/tools/contrast_check.py` 保持 141/141
