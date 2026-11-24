# WC-W013-R1 验收报告

日期：2026-11-24 · 分支 `dev/windows-inspection-w013` · HEAD（见 commit）

## 全量测试

```
635 passed / 0 failed = 620 基线（零回退）+ 15 R1 专项
```

**多 PYTHONHASHSEED 交叉验证**：seed=0/1/42/1337/99999/7777 全部 620+ 全绿（W013 基线时）+ R1 后 635 全绿——**flake 已彻底消除，不依赖任何特定 seed**。

## P0 身份安全最终补充审计

### 修复的真缺陷

| 缺陷 | 修复 |
|---|---|
| **惰性迁移失败被吞掉**——`except` 后标记 done，服务以不完整状态继续 | **503 拒绝服务** + `_migrations_done` 保持 False（DB 恢复后下次请求重试）；中间件返回 JSONResponse 503 而非抛异常（避免 Starlette 兼容问题） |
| **并发首次请求无锁竞争** | `threading.Lock` + double-check 保证迁移仅执行一次；`test_concurrent_lazy_migration_no_race` 8 线程并发验证 |

### 验证清单（全部通过）

| # | 要求 | 测试 | 结果 |
|---|---|---|---|
| 1 | 旧公钥推导 token 全部失效 | W013 `test_legacy_derived_token_rejected` + W012 `test_captured_manifest_cannot_yield_credential` | ✅ 401 |
| 2 | 所有入口统一身份认证 | `upload._auth_device` / `challenge` / `verify` / `/api/v1` 全走 `device_identity.resolve_device` | ✅ |
| 3 | 重注册需密钥持有证明 + 受控授权 | enrollment code + P-256 公钥验证 + 幂等（同公钥→同 token） | ✅ |
| 4 | 凭据轮换不改归属/幂等 | `test_reenrollment_keeps_ownership_and_idempotency` | ✅ |
| 5 | 吊销设备不能复活 | `test_revoked_legacy_device_cannot_resurrect` | ✅ 401 |
| 6 | DB 不存明文认证秘密 | token 为 sha256 hash（不可逆存储的不是秘密本体，仅指纹）；审计只存 16 位指纹 | ✅ |
| 7 | 日志/证据包不泄密 | `test_audit_log_never_stores_full_tokens` + `test_delivery_package_sensitive_leak_scan` | ✅ |
| 8 | 并发惰性迁移无竞争 | `test_concurrent_lazy_migration_no_race`（8 线程） | ✅ |
| 9 | 迁移失败明确拒绝 | `test_migration_failure_returns_503` | ✅ 503 |
| 10 | 多 seed / 执行顺序 / 并发启动 | seed=0/1/42/1337/99999/7777 + 线程并发 | ✅ |

## P1 Inspection Report 扩展（补齐完成）

- `ReportCreateRequest` 新增 `issue_ids` 参数
- `_snapshot_issues()` 在报告生成时绑定问题状态快照（title/severity/status/description/location/history/photos-with-SHA）
- PDF 引擎新增 `_issue_section()`：问题标题+等级+状态+描述+关联照片+历史时间线，含 "HUMAN records; no AI assessment implied" + "manual associations" 声明
- **快照不可变**：问题状态变化后旧报告字节不变（`test_report_with_issues_snapshot_immutable` 钉死）；新报告反映新状态
- snapshot_json 升级为 `{"photos": [...], "issues": [...]}`（交付包读兼容旧格式）
- **2 份真实 PDF 样张**：`inspection_report_with_issues.pdf`（4 页）+ `evidence_report_full.pdf`（5 页，含证据附录+问题）

## P2 离线同步扩展（补齐完成）

W008 sync batch 新增 **8 个操作类型**：

| op | 说明 | 幂等 | 测试 |
|---|---|---|---|
| `annotation_create` | 创建标注（归一化坐标） | operation_id 台账 | ✅ |
| `annotation_update` | 更新（乐观 revision） | ✅ + revision_conflict | ✅ |
| `annotation_delete` | 软删除 | ✅ | ✅ |
| `issue_create` | 创建问题 | ✅ | ✅ |
| `issue_update` | 更新（severity 等，记 history） | ✅ | ✅ |
| `issue_transition` | 状态机跳转（非法跳转 409） | ✅ + 幂等同状态 | ✅ |
| `issue_photo_link` | 关联照片（role，记 history） | ✅ | ✅ |
| `issue_photo_unlink` | 取消关联（记 history） | ✅ | ✅ |

**id_map 扩展**：`server_annotation_id` / `server_issue_id` 加入映射（同 W008 photo/project 语义）。**绝不 LWW**——所有更新操作带 `expected_revision`。跨主体一律 404 等价。全部操作写入 `mvp_issue_history`。

## P3 完整工程检查场景模拟

`test_r1_full_inspection_scenario`（15 项测试中最大的一条）实测通过：

```
[同步] project_create → photo_register → annotation_create → issue_create
[上传] 原图字节 PUT
[关联] issue_photo_link (before)
[状态] issue_transition: open → in_progress → resolved
[报告] inspection_report（含问题快照）
[交付] delivery_package（manifest SHA 逐文件验证）
[重放] 全部 duplicate（安全）
[验证] 原图 byte-identical；重启一致
```

## 真实样张

| 文件 | 内容 |
|---|---|
| `inspection_report_with_issues.pdf` | 4 页：封面 + 2 张照片 + 问题清单（2 个 issue 含完整历史+对比照片） |
| `evidence_report_full.pdf` | 5 页：封面 + 照片 + 问题清单 + 证据附录 |

## 剩余风险

1. iOS 真机交互未验证（全部桌面验证——Canvas 绘制/手势/EXIF/断网同步待 Mac）
2. 标注/问题实体尚未扩展 `mvp_sync_events` 拉取事件流（sync batch 写/推没问题；拉取侧 entity_type CHECK 约束未扩展——列为下一轮）
3. 全部报告/照片/问题均为合成桌面数据——真机结论由 Mac 产出
