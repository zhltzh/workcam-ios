# WorkCam W011 Mac 交接文档

基线：`dev/windows-watermark-w011`（基于 RC1 `91f2819`）。全量 **556 passed / 0 failed**（513 RC1 基线 + 43 W011）。全部桌面验证——**不宣称 iOS 真机渲染已通过**。

## 0. 获取代码

```bash
git fetch origin dev/windows-watermark-w011
git checkout -b mac-w011-joint dev/windows-watermark-w011
git log --oneline -1    # 确认 W011 HEAD
```

## 1. Windows 受控内网启动（不变）

```powershell
$env:WORKCAM_ENROLLMENT_CODE = "<注册码>"
$env:WORKCAM_UPLOAD_AUTH = "1"
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 启动自动迁移 v6（衍生图表）
```

依赖变化：`requirements.txt` 新增 **Pillow**（`pip install -r requirements.txt` 自动装）。

## 2. 查看真实渲染样图

**先看样图再写代码**：`docs/mvp/w011-samples/`（10 张 JPEG）——5 模板 × 横/竖屏，Corporate/Evidence 含 Logo 块，深浅底自适应在 construction（暗底）与 corporate（亮底）对比可见。

## 3. API 快览（详见 w011-watermark-spec.md）

```bash
# 生成水印衍生图（原图必须先上传——W007 规则）
curl -s -X POST http://<win-ip>:8000/api/v1/photos/<photo_id>/derivatives/watermark \
  -H "X-Device-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"template": "evidence",
       "fields": {"project_name": "Bridge N-12", "show_project_name": true,
                   "photo_id": "IMG_001", "show_photo_id": true,
                   "captured_time": "2026-11-24 10:00",
                   "captured_time_trust": "device",
                   "show_captured_time": true,
                   "gps_latitude": 32.06, "gps_longitude": 118.79,
                   "gps_source": "gps", "show_gps": true,
                   "gps_accuracy_m": 4.0, "show_gps_accuracy": true,
                   "evidence_status": "unverified",
                   "show_evidence_status": true}}'
# → 201 {derivative_id, original_sha256, derivative_sha256, geometry, …}
# 重发同一请求 → 201 + idempotent_replay: true（幂等）

# 列表 / 下载
curl -s http://…/api/v1/photos/<photo_id>/derivatives -H "X-Device-Token: $TOKEN"
curl -s http://…/api/v1/derivatives/<id>/download -H "X-Device-Token: $TOKEN" -o wm.jpg
```

## 4. iOS 离线渲染实现要点（Swift）

按 `docs/mvp/w011-watermark-spec.md` §P4 逐条实现，重点：

1. **JSON Schema**：`WatermarkFields` Codable 镜像（snake_case）
2. **几何**：`base = min(w, h)` → 字号 = 系数 × base → 换行/panel 按规范公式
3. **EXIF**：`CGImageSourceCopyPropertiesAtIndex` 读 orientation → `CGImageOrientationTransform` 或 CGContext 变换
4. **信任标注**：`captured_time_trust == .device` → 文本后必须接 `[device time, unverified]`；manual GPS → `MANUAL ENTRY`——**这些标注不可选**
5. **evidence_status**：受控枚举直译——`UNVERIFIED` 永远渲染为 `UNVERIFIED`
6. **输出**：建议 JPEG（quality ~0.9）；HEIC 输入解码后同样渲染
7. **对拍**：`test-vectors/tech-lab/w011-geometry-vectors.json`（10 例几何期望——iOS panel 框/颜色/字号需 1px 一致；像素不要求相同）

## 5. 真机验证清单（待 Mac）

- [ ] 真机照片（HEIC + JPEG）经 API 生成 5 模板衍生图，下载后目视核对
- [ ] 真机 EXIF orientation 6（竖拍）渲染位置正确（转置后落底）
- [ ] iOS 本地渲染与几何向量对拍（10 例全过）
- [ ] 断网状态下本地渲染 5 模板全部可用（规范无网络依赖）
- [ ] 大照片（48MP ProRAW 导出 JPEG）渲染不超时
- [ ] 深底/浅底真机照片的背景自适应正确

回填模板：`docs/tech-lab/experiments/<id>-results.md`——**无报告 = DEVICE_PENDING**。

## 6. 常见错误

| 症状 | 处理 |
|---|---|
| 404 `original_not_uploaded` | 先 `PUT /photos/{id}/original` 传原图字节 |
| 400 `unknown_template` | 模板名必须是五个之一（spec §P1） |
| 400 `invalid_logo` | logo base64 非法或 `show_company_logo` 未开 |
| 422 模型校验失败 | `show_gps=true` 需坐标+来源；时间需 trust 枚举 |
| 渲染报 `too large` | 超 80MP 渲染预算（防炸弹） |
| `nothing to render` | 至少开一个字段 |

## 7. 安全与诚实边界

- 衍生图**不是**证据——每个 API 响应都带该声明
- 原图字节永不被渲染修改（测试钉死 `original == snapshot`）
- 证据状态只由服务端 W006 verify 端点写入；水印只**镜像**
- `idempotent_replay` 表示同请求返回同衍生物——不是重复计费/重复存储
