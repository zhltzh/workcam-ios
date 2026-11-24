# WorkCam W011 水印规范（Watermark Spec）

版本 v1 · 分支 `dev/windows-watermark-w011` · 全量 **556 passed / 0 failed**
10 张真实渲染样图：`docs/mvp/w011-samples/`（全部 SYNTHETIC 合成输入）

## P0 水印数据模型

`WatermarkFields`（`app/services/watermark_render.py`）— 12 个产品字段，**每字段独立 `show_*` 开关**：

| 字段 | 开关 | 信任语义 |
|---|---|---|
| Project Name | `show_project_name` | — |
| Site Address | `show_site_address` | — |
| Captured Time | `show_captured_time` + `captured_time_trust` | `device`→标注 `[device time, unverified]`；`server_anchored`→标注 `[server-anchored]` |
| Server-Anchored Time | `show_server_anchored_time` | 仅服务器锚定时间可带信任标签 |
| GPS Coordinates | `show_gps` + `gps_source` | `gps`→正常显示；`manual`→**必须**显示 `MANUAL ENTRY`；`none`→不渲染 |
| GPS Accuracy | `show_gps_accuracy` | 与 gps 同源 |
| Photo ID | `show_photo_id` | — |
| Operator | `show_operator` | — |
| Company Name | `show_company_name` | — |
| Company Logo | `show_company_logo` | PNG 字节，内容哈希参与幂等键 |
| Notes | `show_notes` | 自动换行 |
| Evidence Status | `show_evidence_status` | **只能**取六态（unverified/signature_valid/cryptographically_valid/verification_failed/server_anchored/no_evidence）——标签直译真实状态 |

### 硬规则（Pydantic 强制 + 测试钉死）

- **UNVERIFIED 永不渲染成 "Verified"**：`evidence_status` 是受控枚举——客户端**无法**提交自由文本伪造
- 设备时间**必须**带 `[device time, unverified]` 标注（渲染器格式化，非客户端可选）
- Manual GPS **必须**显示 `MANUAL ENTRY`
- `show_gps=True` 但 `gps_source=none` 或坐标缺失 → 模型级 422 拒绝
- 全部字段关闭 → 渲染级 `ValueError: nothing to render`

## P1 五种英文模板

| 模板 | 定位 | 字号系数（短边比） | 面板不透明度 | Logo | 用途 |
|---|---|---|---|---|---|
| `minimal` | bottom_right | 0.018 | 0.45 | ✗ | 极简（Photo+Time+Evidence） |
| `construction` | bottom_left | 0.022 | 0.60 | ✗ | 施工现场（Project/Site/Time/GPS/ID/Operator） |
| `inspection` | bottom_right | 0.020 | 0.55 | ✗ | 检查记录（含 Notes 换行） |
| `corporate` | bottom_left | 0.024 | 0.65 | ✓ | 企业品牌（Logo 块） |
| `evidence` | bottom_right | 0.022 | 0.70 | ✓ | 可信证据（全字段 + 信任标注） |

**渲染规则**（引擎 `render_watermark()`）：
- 横竖屏：字号按 `min(width, height)` 缩放——竖屏不会溢出
- 分辨率自适应：panel 宽 ≤ 图宽 62%、高 ≤ 模板比例上限（0.18–0.40）
- 位置：四角任选（模板默认如上表）
- 自动换行：按测量宽度的贪心换行（长 Notes 实测 8+ 行）
- **深浅背景自适应**：渲染前采样目标区域平均亮度 → 暗底配浅面板白字、亮底配深面板黑字
- EXIF Orientation：**渲染前按 0x0112 转置**（1/3/6/8 全测）——水印落在视觉正立图上
- 超限防护：80MP 渲染预算（`MAX_RENDER_PIXELS`）在像素解码前拒绝

## P2 原图保护（W007 不可变原则）

- 渲染**只读**原图字节；衍生物写独立内容寻址库 `<db>/derivatives/<sha[:2]>/<sha>.jpg`
- 衍生图独立 SHA-256；记录 `original_sha256`（绑定）+ `derivative_sha256` + `template_name` + `template_version` + `render_params_hash`
- 同一原图可生成**多版本**（不同模板/字段组合 → 不同 render_params_hash）
- **每个 API 响应都声明**：`"derivative is a DERIVATIVE image, not the trusted original evidence"`
- 幂等：`(photo_id, template, render_params_hash, original_sha256)` UNIQUE——同请求重放返回同一 derivative_id

## P3 衍生图 API

```
POST /api/v1/photos/{photo_id}/derivatives/watermark   生成（201；幂等重放标识）
GET  /api/v1/photos/{photo_id}/derivatives             列表（owner 隔离）
GET  /api/v1/derivatives/{derivative_id}/download     授权下载
```

- 依赖顺序强制：原图未上传 → 404 `original_not_uploaded`（先元数据后字节的 W007 规则）
- 跨主体 404 等价（无存在性预言机）
- 下载响应头：`X-Derivative-Sha256` / `X-Original-Sha256` / `X-Derivative-Note`（声明非证据）
- 迁移 v6：`mvp_derivatives` 表（UNIQUE 四元组 + photo FK + owner 索引）

## P4 iOS 离线渲染规范

### Watermark JSON Schema

`WatermarkFields` 的 Pydantic schema（服务端 `/openapi.json` 内嵌）即跨平台契约。iOS 用 `Codable` 镜像以下结构（字段名 snake_case 对齐）：

```json
{
  "project_name": "string?", "show_project_name": false,
  "site_address": "string?", "show_site_address": false,
  "captured_time": "string?", "captured_time_trust": "device|server_anchored",
  "show_captured_time": false,
  "server_anchored_time": "string?", "show_server_anchored_time": false,
  "gps_latitude": 0.0, "gps_longitude": 0.0,
  "gps_source": "gps|manual|none", "show_gps": false,
  "gps_accuracy_m": 0.0, "show_gps_accuracy": false,
  "photo_id": "string?", "show_photo_id": false,
  "operator": "string?", "show_operator": false,
  "company_name": "string?", "show_company_name": false,
  "company_logo": "bytes(base64 in transit)?", "show_company_logo": false,
  "notes": "string?", "show_notes": false,
  "evidence_status": "unverified|signature_valid|cryptographically_valid|verification_failed|server_anchored|no_evidence",
  "show_evidence_status": false
}
```

### 坐标与尺寸计算规则（iOS 必须逐条实现）

1. `base = min(imageWidth, imageHeight)`（EXIF 转置后的像素尺寸）
2. `fontSize = max(10, Int(0.018…0.024 × base))`（模板系数）
3. `margin = max(8, Int(0.015 × base))`
4. `maxTextWidth = Int(0.62 × imageWidth)`（Logo 存在时减去 Logo 宽）
5. 文本换行：贪心按词（测量宽 ≤ maxTextWidth）
6. panel 放置按模板 position（四角）+ margin
7. panel 颜色：**先采样**目标区域平均亮度（`> 0.5 → 亮底 → 深色面板(20,20,20)+白字`；反之浅面板+黑字）
8. EXIF 0x0112 转置**先于**一切几何计算

### EXIF Orientation

支持 1/3/6/8（常见 iPhone 拍摄方位）。转置映射：`6→rotate90CW`、`8→rotate270`、`3→rotate180`、5/7→翻转+转置（罕见，兼容处理）。

### JPEG / HEIC 输入兼容

- 服务端参考实现：JPEG 进（Pillow）/ JPEG 出（quality 90）
- **iOS 端**：`ImageIO` + `CoreGraphics` 原生支持 JPEG 与 HEIC——建议衍生图统一输出 **JPEG**（兼容性优先）；HEIC 输入解码后按同规则渲染
- 衍生图**不复制** EXIF（衍生物是新工件；原图保留自己的一切元数据）

### 跨平台测试向量

`test-vectors/tech-lab/w011-geometry-vectors.json`（10 例 = 5 模板 × 横/竖屏）：
每例含输入尺寸、original/derivative SHA-256 与**完整几何期望**（panel 框/颜色/字号/行数/亮度/luminance）。iOS 端几何必须一致到 1px（像素级一致不要求——字体不同）。

### 离线要求

**规范不依赖网络**：几何计算、字段格式化、信任标注、模板定义全部为纯本地函数——iPhone 无网络时必须能完成全部渲染（服务端引擎即规范参考实现）。

## 样图

`docs/mvp/w011-samples/`（10 张真实渲染 JPEG，输入为合成测试图）：

```
minimal_landscape.jpg / minimal_portrait.jpg
construction_landscape.jpg / construction_portrait.jpg
inspection_landscape.jpg / inspection_portrait.jpg
corporate_landscape.jpg / corporate_portrait.jpg      ← 含 Logo 块
evidence_landscape.jpg / evidence_portrait.jpg        ← 全字段 + 信任标注
```

**合成输入声明**：所有样图的照片底图为程序合成（渐变+色块），非真实工程照片；渲染输出为真实 JPEG 像素。
