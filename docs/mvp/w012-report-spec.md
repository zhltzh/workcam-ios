# WorkCam W012 工程报告与交付包规范

版本 v1 · 分支 `dev/windows-report-w012` · 全量 **592 passed / 0 failed**
真实样张：`docs/mvp/w012-samples/`（三份 PDF + 一个 ZIP，全部合成输入、真实渲染）

## P1 报告引擎（`app/services/report_pdf.py`，reportlab）

三种英文模板：`site_photo` / `inspection` / `evidence`。

| 能力 | 实现 |
|---|---|
| 纸张 | A4 + US Letter |
| Logo | PNG/JPEG 头部嵌画，装饰性失败不致命 |
| 报告要素 | 工程名/地址、报告编号（WC-YYYYMMDD-XXXX）、生成日期、操作员、公司 |
| 照片要素 | 拍摄时间（标注来源）、GPS 坐标+精度+来源、备注、证据状态 |
| 排版 | 每页 1/2/4 张（四宫格）、自动分页、页码、页眉页脚 |
| 尺寸安全 | 图像按文档实际帧宽**与高**双向约束（竖版照片不再溢出——Letter 样张首跑发现并修复） |
| 证据附录 | evidence 模板默认开启；其他可选。含独立验证说明与逐照片状态表 |
| 诚实边界 | 页脚与附录均声明：**参考文件，非法律认证**；文件完整性≠签名有效≠时间锚定≠现场真实 |

## P2 数据模型与 API（迁移 v7）

`mvp_reports`：report_id PK、project FK、report_type/template_version/page_size/per_page CHECK、photo_ids_json、**snapshot_json**（生成时刻逐照片状态：来源哈希、衍生哈希、时间来源、GPS 来源、证据状态+验证时间）、pdf_sha256、page_count、status。

```
POST /api/v1/projects/{pid}/reports          生成（201；快照 + 版本化）
GET  /api/v1/projects/{pid}/reports          列表
GET  /api/v1/reports/{rid}                    详情（含快照）
GET  /api/v1/reports/{rid}/download          PDF（X-Report-Sha256 头）
POST /api/v1/reports/{rid}/delivery-package  ZIP 交付包
```

**快照不可变**：状态变化后重新生成 = **新行**（旧行零改动，字节可重验）；照片跨项目/重复 ID → 400；不存在照片 → 404，**零残留**（测试钉死）。跨主体一律 404 等价。

**证据状态来源（P0 修复后）**：报告与水印一样，快照的 `evidence_status` 由服务端从 `mvp_evidence_links` 解析——与客户端声明无关。

## P4 交付包

ZIP 结构：`report.pdf` + `originals/` + `derivatives/` + `evidence/` + `manifest.json` + `verification-guide.txt`。三个 include 开关独立控制。**manifest.json 含文件级 SHA-256**（manifest 自身与 guide 除外——自引用哈希不可能，测试注明）。**绝不导出** token、注册码、私钥（泄漏扫描测试逐文件检查）。验签向导写明四层区分与"非法律认证"边界。

## P5 性能实测（DESKTOP，i7 / Python 3.12 / reportlab 5.1）

| 规模 | 耗时 | 产物大小 | 页数 |
|---|---|---|---|
| 10 张 | 0.14s | 56KB | 6 |
| 50 张 | 0.42s | 279KB | 26 |
| 100 张 | 0.73s | 627KB | 51 |
| 500 张报告+交付包 | 2.4s | zip 2.9MB | — |

资源控制：嵌入前降采样（长边 ≤ 1800px，`_EMBED_MAX_EDGE`）——原图**不受影响**（存储原字节逐字节相等，测试钉死），报告内存有界；照片上限 500/请求（schema 422）；ZIP 条目上限 2000。

## P6 测试（29 项）

三模板 / 双纸张 / 横竖混排 / 1·2·4 每页 / 非法 per_page 422 / 长备注特殊字符 / 未验证如实呈现 / 伪造状态服务端覆盖 / **快照跨状态变化不可变**（旧字节重验+新行）/ PDF 魔数+哈希 / ZIP manifest 逐文件验证 / include 开关 / **敏感泄漏扫描** / 原图字节不变 / 跨主体 404 / 跨项目照片 400 / 重复 ID 400 / 失败零残留 / traversal / 批量性能 / **500 张端到端** / 降采样生效且原图完好 / 样张文件。

## P7 Mac 离线参考

`docs/mvp/w012-mac-handoff.md`（Swift PDFKit/CoreGraphics 要点、JSON 快照 Schema、样张、API 速览、真机清单）。
