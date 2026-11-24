# WorkCam W012 Mac 交接文档

基线：`dev/windows-report-w012`（基于 PR #17 `e85caa6`）。全量 **592 passed / 0 failed**。全部桌面验证——**不宣称 iOS 真机渲染已通过**。

## 0. 获取代码

```bash
git fetch origin dev/windows-report-w012
git checkout -b mac-w012-joint dev/windows-report-w012
```

依赖变化：`requirements.txt` 新增 **reportlab**（PDF）。

## 1. 查看真实样张

**先看样张**：`docs/mvp/w012-samples/`
- `site_photo_report.pdf`（5 页，四宫格）
- `inspection_report.pdf`（US Letter，每页 1 张）
- `evidence_report.pdf`（含证据附录——六种验证态对比）
- `delivery_package.zip`（manifest + verification-guide + originals）

## 2. API 速览

```bash
# 生成报告（快照绑定生成时刻）
curl -s -X POST http://<win-ip>:8000/api/v1/projects/<pid>/reports \
  -H "X-Device-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"report_type": "evidence", "page_size": "a4", "per_page": 2,
       "photo_ids": ["<photo-uuid-1>", "<photo-uuid-2>"],
       "operator": "J. Wang", "company_name": "CMSS Engineering"}'
# → 201 {report_id, report_number, pdf_sha256, page_count, …}

# 列表 / 详情（含快照）/ 下载
curl -s http://…/api/v1/projects/<pid>/reports -H "X-Device-Token: $TOKEN"
curl -s http://…/api/v1/reports/<rid> -H "X-Device-Token: $TOKEN"
curl -s http://…/api/v1/reports/<rid>/download -H "X-Device-Token: $TOKEN" -o report.pdf

# 交付包（可选包含原件/衍生物/证据）
curl -s -X POST http://…/api/v1/reports/<rid>/delivery-package \
  -H "X-Device-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"include_originals": true, "include_derivatives": true,
       "include_evidence": true}' -o package.zip
```

**安全提示（P0 修复后）**：设备 token 现在是**随机服务端签发秘密**——妥善保管，它出现在任何请求里都不该被记录到客户端日志。

## 3. Swift 离线渲染建议（P7）

- **PDFKit**：用 `UIGraphicsPDFRenderer`（离线、无网络依赖）；`UIGraphicsPDFRendererFormat.documentInfo` 设 author/title
- **布局复刻**：页眉（工程名+报告编号+Logo）、页脚（"reference document, not a legal certification" + 页码）；照片区按每页 1/2/4 宫格，图像双向限缩（宽×高）
- **照片嵌入**：`UIImage` → `draw(in:)`；大图先降采样（长边 ≤ 1800px 对齐服务端）
- **信任标注**：`captured_time_trust == .device` → 追加 `(device time)`；manual GPS → `MANUAL ENTRY`；**证据状态由服务端快照提供**——客户端离线缓存最近的快照 JSON 用于断网渲染
- **快照 Schema**：`GET /reports/{rid}` 的 `snapshot` 字段即渲染输入（photo_id → 状态字典）
- **EXIF**：`CGImageSourceCopyPropertiesAtIndex` 取 orientation，转置后再排版（对齐 W011 规范）

## 4. 真机验证清单（待 Mac）

- [ ] 真机照片走完 登记→上传→生成三模板报告→下载 目视核对
- [ ] 横竖屏混排报告的翻页与照片位置
- [ ] 交付包在 Mac 解压 → manifest 逐文件 SHA 复核
- [ ] iOS 端 UIGraphicsPDFRenderer 渲染与服务端样张目视对比
- [ ] 100 张真机照片报告的耗时与内存

回填 `docs/tech-lab/experiments/<id>-results.md`——**无报告 = DEVICE_PENDING**。

## 5. 安全边界（P0 修复）

- 报告/水印的 evidence_status **一律服务端解析**，客户端声明无效
- 报告是**快照**：后续状态变化不改旧报告；重新生成=新版本
- 交付包不含任何 token/注册码/私钥（泄漏扫描测试钉死）
- manifest.json 无法自含其哈希（设计边界，guide 已注明）
- PDF/ZIP 是参考交付物——**非法律认证**（页脚、附录、guide 三处声明）
