# WorkCam W007 原图存储设计

日期：2026-11-24 · 分支 `dev/windows-mvp-w007`（基于 PR #12 HEAD `0fef063`）

## 设计决策（编码前已按任务书要求报告）

**复用决策**：不重复建大文件上传协议。T12 分块上传（`/upload/experimental`）保留在其实验层；W007 的原图走**整体单请求提交**（工程照片 2-8MB），复用 W003-R3 验证过的原子写入纪律（tmp → hash → rename）与 W002 的 SHA-256 校验语义。

**内容寻址**：sha256 即身份。路径服务端生成 `originals/<sha[:2]>/<sha>.jpg`——客户端任何字节都不进入文件系统路径，路径穿越从构造上不可能（哈希格式校验先于路径构造）。

**不可变性**：对象一经提交永不覆盖。重复上传同内容 = 幂等重放（读回校验后返回已有记录）；磁盘对象损坏时重传被 **409 拒绝**并标记 `corruption_detected`——修复是显式人工决策，不是静默覆盖。

**完整性 ≠ 真实性**（P0 核心区分）：`stored` 只声明"字节与哈希一致"；每个 API 响应都带 claims 块——`file_integrity_verified: true` 与 `photo_authenticity_proven: false` 并列，附 authenticity_note。存储成功**永不**等同可信拍摄。

## 存储结构

```
data/business/
├── workcam.db              # 业务库（W006，迁移至 v3）
└── originals/              # 原图对象库（内容寻址）
    └── <sha[:2]>/<sha256>.jpg
```

- 衍生图/水印图**不在本存储**（W007 只管原图；衍生图走既有 techlab derivative 模型的未来产品化轮次）
- 原始 JPEG 字节**原样保留**：不重新编码、不碰 EXIF

## 数据库迁移 v3（幂等，v1/v2 不动）

```
mvp_originals        存储台账：sha256 主键、byte_size、status
                     （stored | orphan_suspected | corruption_detected）、
                     stored_at、verified_at
mvp_photo_originals  照片→对象显式绑定：photo_id + sha256 双主键、
                     owner_token、bound_at
```

一个对象可被多条业务记录引用（P1 #1）；每条绑定仍归属唯一项目/主体。

## API（/api/v1，全部要求 X-Device-Token，跨主体 404 等价）

| 方法 | 路径 | 说明 |
|---|---|---|
| PUT | `/photos/{id}/original` | 上传+绑定（原子：body 哈希先验 → 存储对象 → 绑定） |
| GET | `/photos/{id}/original` | 授权下载（`X-Original-Sha256` 响应头；无磁盘路径暴露） |
| GET | `/photos/{id}/original/integrity` | 完整性 + 证据链一视图 |

## 一致性与恢复（P0/P4）

- **写入**：唯一 tmp → 流式哈希 → 与声明比对 → 原子 rename；崩溃只留 `.part` 残渣
- **fsck**：全库对象逐一重哈希；报告 `corrupt` / `missing_files` / `orphan_files`（盘有文件无记录）/ `orphan_suspected_db`（记录无照片引用）/ `temp_residue`；`fix_temps=True` **只**清崩溃残渣，**绝不**自动删除任何原图（P4 红线）
- **备份**：DB 用 VACUUM INTO（W006 既有）+ 对象逐个复制后**复验哈希**，产出 manifest
- **恢复**：幂等——已存在且完好的对象跳过不覆盖；损坏的现存对象**拒绝**覆盖（报告人工处理）；重复恢复零损伤

## 关键防护（测试钉死）

| 防护 | 行为 |
|---|---|
| 哈希不一致 | body 哈希 ≠ 照片登记哈希 → 400，**零持久化**（无文件无记录） |
| 重复上传 | 同内容幂等重放；已绑定照片换内容 = 409/400（原图不可变） |
| 路径穿越 | 格式校验先于路径构造；photo 登记 sha256 正则（W006）+ 存储层独立校验 |
| 并发写 | 内容寻址天然幂等；多照片同内容 → 一个对象多绑定 |
| 跨主体 | 绑定/下载/integrity 全部 404 等价（无存在性预言机） |
| DB 回滚 | 绑定失败无幻影行（FK 事务回滚测试） |
