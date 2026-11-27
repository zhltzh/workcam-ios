# WorkCam Design System（FE-E02 交付）

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
