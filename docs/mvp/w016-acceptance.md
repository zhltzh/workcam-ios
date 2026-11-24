# WC-W016 验收报告：场景模拟与压力测试

日期：2026-11-25 · 分支 `dev/windows-stress-w016`（基于 `31917e3`）

DESKTOP_SIMULATION：全部为桌面合成数据、进程内 TestClient 实测。
文中耗时为单轮桌面观测值，不构成性能承诺；所有时间断言仅为挂死守卫。

## 全量回归与多 seed

| PYTHONHASHSEED | 结果 | 耗时 |
|---|---|---|
| 默认 | **645 passed / 0 failed** | 257s |
| 1 | 645 passed / 0 failed | 352s |
| 1337 | 645 passed / 0 failed | 314s |

645 = 635 基线（W013-R1，零回退）+ 10 项 W016 新测试。

实测命令：

```
cd backend
.venv\Scripts\python.exe -m pytest --tb=short -q
$env:PYTHONHASHSEED='1';  .venv\Scripts\python.exe -m pytest -q
$env:PYTHONHASHSEED='1337'; .venv\Scripts\python.exe -m pytest -q
```

## 新增测试（backend/tests/）

| 文件 | 测试 | 验证点 |
|---|---|---|
| test_w016_scenario.py | S1 全天现场作业 | 1 项目 + 20 照片（真实 JPEG 原图字节）+ 20 标注 + 5 问题（2 个 resolved 含 before/after 关联）→ 检查报告（≥5 页）→ 交付包（manifest 逐文件 SHA 验证，originals/ 20 项）→ 全批次重放全 duplicate 且 id_map 稳定 → 新客户端重启一致 → 原图字节不变 |
| | S2 多设备并行 | A/B 两线程各自建项目 + 8 照片 + 上传；项目集不相交、跨主体 404、DB 16 行照片 |
| | S3 并发修订竞争 | 两线程同 expected_revision PATCH 同一 issue：恰好一胜（200）一负（409），revision 终值 = 2，无丢失更新 |
| | S4 混合结果 batch | applied/rejected 共存于一批；台账把拒绝也记录为 rejected，重放确定性返回；坏 op 不污染后续批次 |
| | S5 批量上限边界 | 50 op 整批 applied；51 op → 422 且第 51 个 op 从未落库；整批重放 id_map 逐位一致 |
| test_w016_stress.py | K1 并发上传 | 8 线程 × 3 张真实 JPEG：注册+上传+回读全字节一致；24 张全成（观测 1.1s） |
| | K2 并发 batch | 8 线程各自 10-op batch + 重放：80 op 全 applied、80 照片、id 零串扰、台账零重复（观测 0.8s） |
| | K3 大载荷 | 2820329B（2.7MB）高熵合成原图：上传 0.05s / 回读 0.02s 字节一致，integrity 端点 file_hash_valid=True |
| | K4 批量吞吐 | 10 × 满 50-op batch = 500 op 连续应用，事件流 has_more=True（观测 10.1s ≈ 50 ops/s） |
| | K5 并发报告 | 4 线程对同一项目并发生成报告+交付包：4 份互异真实 PDF，manifest SHA 全验（观测 0.6s） |

单独运行：`.venv\Scripts\python.exe -m pytest tests\test_w016_scenario.py tests\test_w016_stress.py -v`
（10 passed in 21.74s）

## 接口验收要点（协议级，写入断言钉死）

1. **幂等重放**：同 request_hash 重放 → duplicate + 原 receipt；这是 W008 协议核心，S1/S4/S5 三面钉死。
2. **批量原子性边界**：MAX_BATCH_OPS=50 之外整批 422 且零部分应用（S5 用台账行数证明）。
3. **混合结果不中止**：一批内 rejected 不阻断后续 applied（S4）；拒绝记录进台账，重放同型（S4）。
4. **乐观并发无 LWW**：并发竞争下恰好一次应用（S3），输者拿到带 current_revision 的 409。
5. **交付包完整性**：manifest 每文件 SHA-256 与 ZIP 内字节逐一比对（S1/K5）。

## 安全相关验证（本任务覆盖面）

- 跨主体隔离在并发下不破：S2（404 等价）、K2（owner-scoped 台账零串扰）。
- 重放不可篡改他人资源：S1/S4 全量重放仅返回己方 receipt。
- 竞争窗口无双重应用：S3 revision 终值精确为 2。
- 注：身份层 P0 审计（legacy token、迁移 503、并发锁）属 W013-R1 已验收范围，
  本任务不重复展开；P0 结论在其验收文档中仍然有效。

## 数据恢复现状（不属于本任务扩展，如实记录）

基线内已有（随 645 全绿）：`test_backup_restore_roundtrip` /
`test_restore_refuses_nonempty_target`（DB 层，含完整性+FK 审计）、
`test_backup_restore_with_identity`（恢复后全应用重启一致）、
`test_backup_restore_roundtrip_and_repeat_safety`（originals 对象层，重复恢复不损）、
`test_rc3_backup_restore_full_stack`（DB+originals 联合备份恢复）。
**灾备扩展（多代备份、损坏注入、恢复演练自动化）属 WC-W018 范围，未开始。**

## 剩余风险与声明

1. 全部为桌面合成数据与进程内客户端——**iOS 真机结论由 Mac 产出**，本报告不声称任何真机行为。
2. 压测耗时为单进程单轮观测，受桌面负载影响，仅用于回归对比基线，不是 SLA。
3. K4 的 50 ops/s 受每 op 一次 BEGIN IMMEDIATE 落盘纪律（W008 幂等台账）影响，
  属设计取舍（durability > latency）；如需提升属后续优化决策，不在本任务擅改。
4. 未做产品代码改动：本任务仅新增测试与文档，服务实现零变更（零回退的结构性保证）。
