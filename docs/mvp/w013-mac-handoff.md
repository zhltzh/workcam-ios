# WorkCam W013 Mac 交接文档

基线：`dev/windows-inspection-w013`（基于 PR #18 `1282a66`）。全量 **620 passed / 0 failed**（592 基线 + 28 W013）。全部桌面验证——不宣称 iOS 真机交互完成。

## 0. 获取代码

```bash
git fetch origin dev/windows-inspection-w013
git checkout -b mac-w013-joint dev/windows-inspection-w013
```

## 1. 查看真实样图

`docs/mvp/w013-samples/`（8 张 JPEG）：6 种标注类型各一张 + 全类型组合（含水印）+ 整改前后并排对比。

## 2. API 速览

```bash
TOKEN="<device-token>"
H="X-Device-Token: $TOKEN"
BASE="http://<win-ip>:8000/api/v1"

# 标注（创建/列表/改/软删）
curl -s -X POST "$BASE/photos/<photo_id>/annotations" -H "$H" \
  -H "Content-Type: application/json" \
  -d '{"annotation_type": "arrow", "norm_x1": 0.2, "norm_y1": 0.3,
       "norm_x2": 0.6, "norm_y2": 0.25, "color_hex": "#FF3B30",
       "stroke_width": 6}'
curl -s "$BASE/photos/<photo_id>/annotations" -H "$H"
curl -s -X PATCH "$BASE/annotations/<aid>" -H "$H" \
  -d '{"text_content": "updated", "expected_revision": 1}'
curl -s -X DELETE "$BASE/annotations/<aid>" -H "$H"

# 工程问题（创建/列表/生命周期）
curl -s -X POST "$BASE/projects/<pid>/issues" -H "$H" \
  -d '{"title": "Pier 2 crack", "severity": "high"}'
curl -s "$BASE/projects/<pid>/issues?status=open" -H "$H"
curl -s -X PATCH "$BASE/issues/<iid>" -H "$H" \
  -d '{"status": "in_progress", "expected_revision": 1}'
curl -s "$BASE/issues/<iid>" -H "$H"        # 详情含全部 history

# 整改前后对比
curl -s -X POST "$BASE/issues/<iid>/photos" -H "$H" \
  -d '{"photo_id": "<pid>", "role": "before"}'
curl -s -X POST "$BASE/issues/<iid>/photos" -H "$H" \
  -d '{"photo_id": "<pid2>", "role": "after"}'
curl -s -X POST "$BASE/issues/<iid>/comparison-image" -H "$H" \
  -d '{"before_photo_id": "<pid>", "after_photo_id": "<pid2>"}' \
  -o comparison.jpg
```

## 3. iOS SwiftUI Canvas 实现建议

### 坐标转换

```swift
// normalized (0..1) -> display pixels (EXIF-transposed frame)
let point = CGPoint(x: norm.x * displayWidth,
                    y: norm.y * displayHeight)
```

### 六种类型的 Canvas 绘制

```swift
Canvas { context, size in
    for ann in annotations.sorted(by: { $0.zOrder < $1.zOrder }) {
        let p1 = CGPoint(x: ann.normX1 * size.width,
                          y: ann.normY1 * size.height)
        let p2 = CGPoint(x: (ann.normX2 ?? ann.normX1) * size.width,
                          y: (ann.normY2 ?? ann.normY1) * size.height)
        let color = Color(hex: ann.colorHex)
        switch ann.annotationType {
        case .arrow:
            var path = Path()
            path.move(to: p1); path.addLine(to: p2)
            context.stroke(path, with: .color(color),
                          lineWidth: ann.strokeWidth)
            // arrowhead: rotate a triangle to atan2 direction
        case .rectangle:
            let rect = CGRect(from: p1, to: p2)
            context.stroke(Path(rect), with: .color(color),
                          style: StrokeStyle(lineWidth: ann.strokeWidth))
        case .circle:
            let r = hypot(p2.x - p1.x, p2.y - p1.y)
            context.stroke(Path(ellipseIn: CGRect(...)), ...)
        case .text:
            context.text = .init(ann.textContent)   // wrap at 45% width
        case .numberMarker:
            context.fill(Path(ellipseIn: ...), with: .color(color))
            context.draw(Text("\(ann.markerNumber)").foregroundColor(.white),
                         at: p1)
        }
    }
}
```

### 手势

```swift
// arrow/rectangle drag: capture start/end, store as normalized
DragGesture()
    .onEnded { value in
        newAnnotation.normX1 = value.startLocation.x / displayWidth
        newAnnotation.normY1 = value.startLocation.y / displayHeight
        newAnnotation.normX2 = value.endLocation.x / displayWidth
        newAnnotation.normY2 = value.endLocation.y / displayHeight
    }
```

### EXIF 处理

与 W011 规范一致：`CGImageSourceCopyPropertiesAtIndex` 读 0x0112 → 转置后再绘制。**归一化坐标始终指转置后的显示帧**。

## 4. 离线保存与同步

- 标注/问题**先存本地**（本地 SQLite，与 W008 的离线队列模式一致）
- 恢复网络后逐条 POST；**PATCH 冲突**（revision mismatch）→ re-fetch + 合并 + 新 revision 重试
- 问题状态变更在线历史记录：服务端自动写 `mvp_issue_history`，客户端无需额外处理

## 5. 真机验证清单（待 Mac）

- [ ] 六种标注类型在真机照片上创建/拖动/渲染
- [ ] EXIF orientation 6（竖拍）标注位置正确
- [ ] 整改前后并排对比图在真机照片上生成并下载
- [ ] 断网创建标注/问题 → 恢复网络同步
- [ ] 水印+标注组合在真机上渲染目视核对
- [ ] 大照片（48MP）标注渲染不超时

回填 `docs/tech-lab/experiments/<id>-results.md`——无报告 = DEVICE_PENDING。

## 6. 常见错误

| 症状 | 处理 |
|---|---|
| 413 too_many_annotations | 超 50/照片上限 |
| 409 revision_conflict | re-fetch + 新 expected_revision |
| 409 invalid_status_transition | 状态机见 spec §P3（open 不能直接 resolved） |
| 409 issue_closed | closed 问题不可改 |
| 409 annotation_deleted | 软删除后不可 patch |
| 400 photo_project_mismatch | 对比照片必须在问题所在项目 |

## 7. 安全边界

- 标注是 **human-added content**——每个响应都注记
- 问题等级是**人工记录**——"no AI assessment implied"
- 对比图是**手工关联**——不证明同一位置、不构成验收
- 原图渲染前后逐字节相等（测试钉死）
- 推导式 legacy token 一律 401（P0 修复）
