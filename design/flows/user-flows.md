# WorkCam 核心用户流程（W014）

版本：w014-rc1 · 2026-11-25 · 服务 W017 Swift 参考实现

术语与状态机以下列文档为唯一依据：
- `docs/mvp/w006-api-contract.md`（photo 登记 / evidence link / trust_notes）
- `docs/mvp/w008-sync-protocol.md` + `w008-conflict-policy.md`（离线同步 / 冲突）
- `docs/mvp/w012-report-spec.md`（报告快照）
- `docs/mvp/w013-annotation-spec.md`（标注 / 问题状态机）

离线优先总则：**所有写操作先落本地队列，后同步**。UI 永不因离线而禁用本地编辑；同步状态只影响"何时送达"，不影响"能否记录"。

---

## F1 拍摄捕获

```
Projects ──New Project / 打开项目──▶ Camera
Camera：快门 ──▶ 本地写原图（原子写，成功才显示文件名）
            ──▶ 生成 SHA-256 + 证据清单（本地）
            ──▶ 入同步队列（photo_register）
            ──▶ 结果横幅：✓ captured · ◐ Awaiting verification
失败分支：写盘失败 → 不登记、不显示文件名、明确报错（不产生半成品记录）
```

UI 规则：
- 取景 HUD 显示 GPS 精度与设备时间；**均为 client-claimed，不渲染为"可信时间"**（详见 offline-time-trust.md）。
- 拍完即离开或继续拍；结果横幅是唯一"成功"反馈，不得出现"已验证"措辞。

## F2 证据验证状态（只读镜像）

```
unverified ──服务端 verify──▶ signature_valid ──▶ verified
                ├──▶ failed（原因入 failure_reason）
任何状态 ──时间锚──▶ anchored（叠加，不覆盖验证状态）
none：无 evidence link
```

UI 规则：
- 客户端**只能显示**服务端镜像的状态；本地不推定、不改写（同步载荷里的 verification_status 被服务端忽略，UI 不得显示本地猜测值）。
- `signature_valid` 徽章旁必须保留限定文案：`Signature valid ≠ capture scene authentic`。
- App Attest 未实现 → attestation 一律显示 `unknown`，不得渲染成待完成进度。

## F3 标注

```
Photo Detail ──▶ 选择工具（arrow/rectangle/circle/freehand/text/number_marker）
  ──▶ 归一化坐标落库（EXIF 转置后的显示坐标系）
  ──▶ 本地渲染预览 + 入同步队列（annotation_create / annotation_update / annotation_delete）
限制：≤50 条/照片（超限 413 提示）；所有标注响应注记 "human-added annotation content"
冲突：annotation_update 乐观并发 → revision_conflict → 提示 re-fetch 后重试
```

UI 规则：
- 标注色板固定六色（见 ui-spec §5）；编号圆底白字。
- 原图**只读**——标注只出现在衍生图/预览层，UI 不提供任何"修改原图"入口。

## F4 问题（Issue）生命周期

```
New Issue ──▶ open ──▶ in_progress ──▶ resolved ──▶ closed（终态）
                ▲          │            │
                └──────────┴── 重开 ─────┘（resolved→in_progress 清空 resolved_at）
非法跳转 → 409，UI 不渲染不可能的跳转按钮（状态决定可见操作）
```

- severity：low / medium / high / critical（人工记录，UI 注记 "no AI assessment implied"）。
- 照片关联 role：before / progress / after，每角色一张；支持并排对比衍生图。
- 每笔变更显示 history 时间线（status_changed / severity_changed / photo_linked…）。
- 离线跳转/更新 → 队列（issue_create / issue_update / issue_transition / issue_photo_link / issue_photo_unlink）。

## F5 报告

```
Reports ──Generate Report──▶ 选择照片/问题集合 ──▶ 服务端生成快照
  ──▶ Report Preview（恒白纸面）
  ──▶ Download PDF / Export Package（证据包）
```

- **快照不可变**：生成后问题状态变化不改变旧报告字节；UI 明示"Immutable snapshot"。
- 报告含免责声明："reference document, not a legal certification"；HUMAN records 声明照搬 W012/W013。
- 预览纸面不随暗色主题反转（见 ui-spec §5）。

## F6 同步与冲突

```
本地队列（12 pending…）──网络可用──▶ POST /api/v1/sync/batch（幂等 op_id）
逐项回执：queued ─▶ complete / conflict / rejected
```

UI 规则：
- 队列项 = op 名（mono 字，与 W008 batch op 名一致）+ 摘要 + 状态徽章。
- **revision_conflict**：不静默覆盖、不自动重试——横幅提示 `fetch latest data and retry`，用户确认后 re-fetch → 合并 → 新 revision 重试（活锁 3 次上限后人工接管）。
- **幂等回执（duplicate）**：UI 静默（不弹提示、不记为冲突）。
- **idempotency_conflict**（同 key 不同内容）：属于客户端 bug 级异常，显示详情并引导导出诊断。
- 归档项目上的写操作 → `project_archived`：提示"项目已归档"，导航回项目列表；**数据不删**。
- 404 等价原则：跨主体一律显示"不存在"，不显示"属于他人"。

---

## 页面 × 流程覆盖矩阵

| 页面 | 覆盖流程 |
|---|---|
| Projects | F1 入口 / F4 列表入口 |
| Camera | F1 |
| Photo Detail | F2 / F3 |
| Issues | F4 |
| Reports / Report Preview | F5 |
| Sync | F6 |
| Settings | 设备 token（mono）、水印模板、存储占用（F1/F6 的配置面） |
