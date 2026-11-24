# WorkCam UI/UX 规范（W014）

版本：w014-rc2 · 2026-11-25 · 依据 `design/prototype/index.html` 与 `design/tokens/design-tokens.json`

## 1. 范围与定位

- W014 产出交互原型、视觉 token、组件规范、用户流程与 SwiftUI 映射，作为 **W017 Swift 参考实现** 的唯一视觉依据。
- 原型（`design/prototype/index.html`）为高保真浏览器原型，仅表达视觉与交互意图；**不代表实现状态**。未实现的功能不得声称已实现（见 AGENTS.md）。
- 所有 UI 文案一律英文；文档用中文记录。

## 2. 视觉方向（三选一，RC2 前定稿）

| 方向 | 名称 | 意图 | 适用 |
|---|---|---|---|
| A | Field Pro | 深蓝权威 + 安全橙行动色，工业现场工具感，户外可读性优先 | 默认推荐 |
| B | Trust Slate | 冷岩蓝 + 青绿点缀，文档化、合规卷宗气质 | 交付/审计场景 |
| C | Signal Clean | iOS 系统色对齐，零学习成本 | 快速上手、系统一致性 |

原型右下角切换器可切换方向与亮/暗主题。**证据状态色（evidence status）与语义规则跨方向共享**，只有品牌层（primary/accent/灰阶）随方向变化。

## 3. Token 体系

- 唯一事实源：`design/tokens/design-tokens.json`（版本 w014-rc2）。
- 结构：`shared`（间距/圆角/阴影/字体/证据状态色/标注色板/严重度映射/chip 规则/恒定表面）+ `directions.{a|b|c}.themes.{light|dark}`（品牌层）。
- 引用字符串（如 `"accent_strong"`、`"evidence_status.dark.verified"`）由消费方按方向+主题解析。
- **证据状态色拆分亮暗双变体**：同一语义在浅底上用深变体、在暗底上用亮变体，保证徽章文字对比度 WCAG AA 达标。
- **CTA 用 `accent_strong` + `on_accent`**，不用装饰用 `accent`（系统绿/亮橙无法承载白字）。
- **落在 primary-subtle/混合底上的文字用 `primary_ink`**（C 方向 = 加深的系统蓝 #0056b3 / 提亮的 #409cff）。
- hover 规则：亮色主题 CTA hover 加深、暗色主题提亮，两种走向都保持标签对比度达标。

## 4. 证据状态色语义（与后端状态机对齐）

依据 `docs/mvp/w006-api-contract.md`（evidence link 状态机）与 `docs/mvp/w008-sync-protocol.md`：

| Token | 语义 | 用户文案（EN） |
|---|---|---|
| `none` | 无证据关联 | `No evidence` |
| `unverified` | 链接已登记，服务端尚未验证（或照片刚捕获） | `Awaiting verification` |
| `signature_valid` | 签名验证通过——**仅证明字节与签名匹配，不证明拍摄场景真实** | `Signature valid` |
| `verified` | 完整验证通过 | `Verified` |
| `failed` | 验证失败 | `Verification failed` |
| `anchored` | 时间锚已锚定 | `Time-anchored` |

呈现规则：
- 状态徽章 = 状态色文字 + `color-mix(in srgb, 状态色 15%, surface)` 底（failed 为 12%），11px semibold，胶囊形。
- **信任语义文案必须如实分级**：`Signature valid ≠ capture scene authentic`、`Device attestation: unknown (App Attest not implemented)` 这类限定语不可删改、不可升级措辞。
- 照片元数据中 `captured_at` / `device_id` 标注为 client-claimed, untrusted 的规则照搬后端 trust_notes。

## 5. 组件规范

| 组件 | 原型类名 | 规格 | 备注 |
|---|---|---|---|
| 卡片 | `.card` | surface 底 + 1px border + `--shadow-card` | border 为装饰性（见 §6 豁免） |
| 项目卡 | `.project-card` | 140px 缩略位 + 名称 + 站点 + 证据徽章汇总 | hover 浮起 |
| 证据徽章 | `.ev-badge` | 11px semibold 胶囊 | 状态色语义见 §4 |
| 严重度条 | `.sev-indicator` | 4px 全高色条，无文字 | low=none / medium=unverified / high=accent_strong / critical=failed |
| 问题状态 chip | `.issue-status-chip` | 11px 胶囊 | open=primary_ink / in_progress=unverified / resolved=verified / closed=text_3 on bg |
| 主按钮 | `.btn-primary` | `accent_strong` 底 + `on_accent` 字 | hover 用 `accent_strong_hover` |
| 次按钮 | `.btn-secondary` | surface 底 + border + text_1 | |
| 相机 HUD | `.camera-hud-item` | `rgba(0,0,0,.6)` 底白字 | 最坏情况（纯白照片底）对比 5.74:1 |
| 照片角标 | `.photo-badge` | 同上 | |
| 快门 | `.shutter-btn` | 72px 白圈 | 按压缩放 0.92 |
| 同步队列项 | `.sync-item` | 操作名 mono 字 + 详情 + 状态徽章 | 操作名与 W008 sync batch op 名一致 |
| 错误横幅 | `.error-banner` | failed 色 10% 底 + 30% 边框 + failed 色字 | |
| 报告纸面 | `.report-preview` | **恒白底黑字**，不随主题反转 | 报告即 PDF 导出的物理介质隐喻；含 "reference document, not a legal certification" 声明 |
| 空状态 | `.empty-state` | 48px 图标 + 标题 + 描述 | |

标注色板：`#FF3B30 #FF9500 #FFCC00 #34C759 #007AFF #AF52DE`（iOS 系统六色，归一化坐标存储，详见 `docs/mvp/w013-annotation-spec.md`）。

字体：系统栈（SF Pro / SF Mono）；**SHA-256 指纹、照片 ID、sync op 详情、设备 token 一律 mono**。

## 6. 无障碍（WCAG 2.1 AA）——审计实证

审计工具：`design/tools/contrast_check.py`（读取 design-tokens.json，可重跑，退出码非 0 即失败）。

**结果（w014-rc2）：硬检查 141/141 通过**；6 项 advisory 如实报告不计数。

覆盖范围：
- 全部 3 方向 × 2 主题的 text_1/2/3 对 bg 与 surface；
- nav 激活/未激活、CTA 标签（含 hover 态）、6 种证据徽章、4 种问题 chip、错误横幅；
- 严重度色条按 UI 组件 3:1；
- 报告纸面（恒亮色变体）；
- 照片浮层按**最坏情况**（底为纯白照片）评估。

豁免（advisory）：卡片 border 对比度 1.22–1.71:1。WCAG 1.4.11 仅要求**可交互组件的语义边界**达到 3:1；原型与 MVP 均无带边框的输入控件，卡片边缘与分隔线属装饰。若后续加入表单输入，其边框必须重新入列硬检查。

### v1 → rc2 修正记录（首轮审计 62 项 FAIL 的系统性修复）

| 问题 | 修正 |
|---|---|
| text_3 辅助灰全亮色主题 <4.5:1（2.3–3.3） | 逐方向加深（A #66707d、B #5c6a7e、C #6d6d72；dark 逐方向提亮） |
| B dark 层级反转（text_3 比背景更暗） | text_2 提亮为 #b6c2d2，text_3 提亮为 #94a3b8 |
| 证据状态色单值不分主题，亮色下 5/6 不达标 | 拆分 light/dark 双变体；badge 底由硬编码 rgba 改为 `color-mix` 跟随变体 |
| CTA 白字对 accent 1.86–3.74:1 | 引入 `accent_strong`/`on_accent`；暗色主题按钮文字改近黑 |
| C 方向 nav 激活/chip 用系统蓝不足 | 引入 `primary_ink`，C light=#0056b3、C dark=#409cff |
| camera HUD 0.5 底最坏情况 3.95:1 | 加深至 0.6（最坏 5.74:1） |
| 徽章底色硬编码旧色值，暗色下与文字变体脱节 | 统一 `color-mix` 引用 token，随主题联动 |

## 7. 原型页面清单与状态诚实性

8 个页面：Projects / Camera / Photo Detail / Issues / Reports / Report Preview / Sync / Settings。
原型允许"模拟数据演示交互"（如 simulateCapture），但：
- 模拟交互必须标注为原型行为，不得在任何文档中被引用为功能已实现；
- App Attest 未实现 → 相关 UI 只能写 `unknown (App Attest not implemented)`；
- 同步操作名只用 W008 batch 已定义的 op 名（`annotation_create`、`issue_transition` 等），不得发明不存在的操作。

## 8. 交付约定

- 任何视觉改动必须同步更新 `design-tokens.json`，并重跑 `contrast_check.py` 保持 141 项硬检查全绿；
- 截图命名 `<page>_dir_<a|b|c>_<light|dark>.png`，存放于 `design/prototype/screenshots/`；
- 本规范与 `design/flows/`、`design/swiftui/` 一起构成 W017 的输入。
