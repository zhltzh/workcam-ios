# T06 故障注入矩阵 (W-04)

PhotoStore（`backend/techlab/storage_integrity.py`）的故障注入与恢复行为总表。
每行都有自动化测试钉死（`tests/test_techlab/test_storage_integrity.py`）。

## 故障矩阵

| # | 故障 | 注入方式 | 系统行为 | 恢复结果 | 可恢复? | 测试 |
|---|---|---|---|---|---|---|
| F1 | 写入中断（临时文件已写、改名前断电） | `crash_before_rename` | 临时文件被清理，无提交 | 干净状态，无需恢复 | ✅ 自愈 | `test_crash_before_rename_leaves_nothing` |
| F2 | 写入中断（照片已落盘、清单未更新） | `crash_after_rename` | 照片成为孤儿（未提交） | fsck 识别孤儿 → 移入 quarantine/（移动，不删除） | ✅ 显式隔离 | `test_crash_after_rename_creates_orphan_then_fsck_quarantines` |
| F3 | 磁盘空间不足 | `write_bytes_budget` | 立即抛 `DiskFullError`，无部分写入 | 干净状态；调用方收到明确错误 | ✅ 报错后干净 | `test_disk_full_fails_loudly_no_partial_state` |
| F4 | 提交后字节损坏（bit-rot/篡改） | 直接改写已提交照片 | fsck 哈希复验失败 | 损坏文件移入 quarantine/，报告 `hash_mismatches` | ✅ 显式隔离（原字节已不可信，不修复） | `test_hash_mismatch_detected_and_quarantined` |
| F5 | 原图被手动删除 | unlink | fsck 报告 `missing_files` | 不伪造、不猜测；条目保留等人工/备份恢复 | ⚠️ 报告+人工 | `test_missing_file_reported_not_invented` |
| F6 | manifest.json 损坏 | 写入垃圾 JSON | fsck 报告 manifest 不可读 | **孤儿检测跳过**：无法区分孤儿与已提交证据，全部原地保留 | ⚠️ 报告+人工 | `test_corrupted_manifest_reported_no_data_loss` |
| F7 | 重复写入同一照片（同字节） | 重复 `save_photo` | 幂等：photo_id = 内容哈希 → 同文件覆盖同内容，manifest 条目同值 | 无孤儿、无重复条目 | ✅ 自愈 | `test_duplicate_write_same_bytes_is_idempotent` |
| F8 | 重复写入不同照片 | 正常流程 | 不同 photo_id，新条目 | 正常 | ✅ 正常 | `test_manifest_stays_json_loadable_after_every_save`（10 张并存） |

## 设计原则（与任务书对齐）

1. **恢复永不删除数据**：F2/F4 用 quarantine（移动），F5/F6 原地保留 + 报告。任何路径都不存在"静默丢失"。
2. **必须明确报错的故障**：F3（磁盘满）抛异常；F6（manifest 损坏）进入 LIMITED 恢复模式（跳过孤儿判定），绝不假装完整恢复。
3. **可恢复故障清单**：F1/F2/F3/F4/F7/F8。
4. **必须人工介入的故障清单**：F5（需要备份或重新拍摄）、F6（需要清单备份或人工鉴定）。

## 与 PR #8（Wave 1）的差异

Wave 1 的 T06 只有内存级原子写；W-04 之后故障注入覆盖真实文件系统的
写入中断、磁盘满、bit-rot、删除、清单损坏与重复写入，全部由测试执行
真实目录与真实断电点模拟，非 mock。

## iPhone 侧映射（待 Mac 验证）

- iOS 已有 `.pending-` 暂存目录 + 原子 rename（M003 CaptureStore），
  对应 F1/F2 的等价机制；真机断电/强杀实验属于 DEVICE_PENDING，
  需要 Mac 按本矩阵逐项复测并在 `experiments/` 回填结果。
