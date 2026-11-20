# WC-W-TECH-003 Wave 3 集成报告

日期：2026-11-20 · 分支：`dev/windows-tech-lab-wave2`（PR #10）
基线：`b689762` → 本轮 `（见 git log）` · 测试 **330 passed / 0 failed**

---

## 第一阶段：PR 收口 ✅

| 检查项 | 结果 |
|---|---|
| PR #8 | open, base=main, head `5a57c6a`, mergeable=True/clean, 3 commits |
| PR #10 | open, base=`dev/windows-tech-lab`, head `b689762`, clean, 19 commits |
| 依赖关系 | `git merge-base --is-ancestor` 确认 **PR #8 完整包含于 PR #10**；顺序安全：#8 先合 → #10 剩纯增量 |
| 与 main / PR #7 | 无文件交集，无冲突面 |
| 独立复跑 | **312 passed**（当时基线），收口时全量 **330 passed** |
| 自动合并 | 未合并，两 PR 均待审 |

## 第二阶段：协议审核 ✅

**T07 校准精度复核**：向量审查发现**真设计缺陷**并修复——3 点仿射是
精确解，第 3 点的偏差会被拟合吸收（残差恒 0），**残差拒绝必须 ≥4 点**
（3 解 + 1 验证点）。向量 `reject_residual` 已改为 4 点用例，`scale_offset`
/`rotation90_scale0.5` M/t/probe 全部 1e-9 钉死。

**T08 内参单位修复（审核核心项）**：移除 `fx<300 → mm` 启发式，改为
**显式 `FocalUnits` 字段**（`pixels` / `mm_35mm_eq` / `unset`）：
- 每单位独立合理区间（pixels 300–30000，mm 10–1200）
- pixels 声明下主点必须在图像内
- unset 兜底宽区间但报错时**要求声明单位**
- **所有向量文件强制携带 units 字段**（`test_t08_vectors_pin_declared_units`
  防回归）；JSON Schema 已导出给 Mac Codable

**T12 状态机审查**：协议模型 6 测试已覆盖乱序/幂等/NACK 不占位/换位
攻击/冲突拒绝；本轮在真实端点落地时补齐**持久化（磁盘即状态）、
认证（生产禁用开关）、限额（块大小/块数/批量数/整包字节）、资源清理
（72h TTL 清扫）**。

**T14 定位声明**：桥接 PoC 是**断言映射的跨系统一致性协议**，不是
标准合规实现——真签名需外部证书账户（红线外），文档与代码 docstring
均已声明。Mac 后续用 c2pa-swift 真实 SDK 时，断言层与本桥接同构。

## 第三阶段：T12 真实后端 ✅（12 端点测试全绿）

端点（`/upload/experimental`，前缀隔离，**不破坏现有 API**——main.py
仅追加 include_router）：

```
POST /batches                          注册（幂等；chunk 计划由服务端从总字节推导）
PUT  /batches/{id}/chunks/{index}      流式接收：tmp→hash→落盘→ack（先持久后确认）
GET  /batches/{id}                     status：缺失索引列表（断点续传依据）
POST /batches/{id}/complete            逐块复验（大小+哈希）→ 重组 → 整包 sha256 独立校验
```

关键行为（**全部有测试**）：
- **重启恢复**：批量状态全在磁盘，新进程（TestClient 重建）继续 status/
  complete（`test_server_restart_recovery`）
- **恶意请求**：traversal id 不碰磁盘、越界 index 400、超长块/短块 400
  且槽位可复用、伪造整包哈希 → 409 且**已传块不销毁**
- **限额**：块 4MiB / 整包 2GiB / 512 块 / 64 活跃批量，全部端到端断言
- **生产禁用**：`WORKCAM_ENV=production` → 503

失败案例记录（真实调试中发现）：
1. body 模型校验失败返回 422 而非 400（同义拒绝，测试已按语义断言）
2. traversal id 由路由层 404，十六进制守卫兜底——两者都"不碰磁盘"

## 第四阶段：跨平台联调工具 ✅

| 交付 | 位置 | Mac 用法 |
|---|---|---|
| T07 校准向量（4 例） | `test-vectors/tech-lab/t07-calibration-vectors.json` | SIMD 解 M/t 与 `expected_M/t` 1e-9 对拍；拒绝例必须 raise |
| T08 投影向量（6 例） | `test-vectors/tech-lab/t08-projection-vectors.json` | `u=fx*xc/zc+cx` 对拍 `expected_uv` 1e-9；**+z 前方约定已声明**（ARKit 原生 -z，需转换） |
| JSON Schema | `test-vectors/tech-lab/record-schemas.json` | 生成 Codable；**units 字段必填** |
| T12 联调客户端 | `tools/tech-lab/validation/t12_upload_client.py` | 纯标准库双平台 CLI：乱序→丢块→续传→重复→complete 哈希核对，逐步日志 |
| T13/T18 独立验证 | `tools/tech-lab/validation/evidence_verifier.py` | 无服务器验签 CLI（双平台命令见 README） |
| 工具文档 | `tools/tech-lab/validation/README.md` | 全部命令 + 约定 + 退出码 |

向量一致性由 `test_cross_vectors.py`（4 测试）在 Windows 侧钉死：
向量文件 ↔ Python 实现逐例互证——**Mac 与此不一致即为 Mac bug**。

## 第五阶段：完整业务链路 ✅（SYNTHETIC，一次跑通）

`test_full_chain.py` 单场景贯通 **8 阶段**（实际运行日志）：

```
capture: photo 4120 B, gps verified gps, grade A
seal: original 3e2fc674f5c35c27 (store + ledger consistent)
watermark: derivative e9395055497d0bd1 (original untouched)
drawing: pin corroborated (gps location 13.5 m from pin (radius 25.0 m))
package: verify all_passed (1 record, 1 derivative)
export: zip 9950 B (rss delta 0.3125 MB)
offline-verify: CLI exit 0, package overall=true
upload: 1 chunks, dropped 0, resumed after restart, server hash == local hash
[CHAIN] PASSED - 8 stages wired together
```

链路覆盖：拍照输入（合成照片+GPS 元数据）→ T05+T06 双层封存（账本+
磁盘一致）→ T10 水印衍生（原图不可触碰）→ T07 图纸关联（GPS 佐证
成立）→ T13 证据包（含衍生图回链）→ T09 流式导出 → T18 离线 CLI
验证 → T12 上传（丢块+续传+**重启后恢复**+哈希终验）。

分层篡改测试（第二用例）钉死各层职责边界：磁盘照片篡改由**证据包**
捕获，账本原件不受影响、并能反向指出磁盘文件与账本不一致。

## 验收对照

| 要求 | 状态 |
|---|---|
| 代码 | T12 端点 `app/api/upload.py` + 向量生成器 + 客户端 CLI + 链路测试 |
| 测试 | **330 passed / 0 failed**（312 → +12 端点 +4 向量 +2 链路 +净增） |
| 运行日志 | 链路 8 阶段逐步日志、端点调试记录（见第三阶段失败案例） |
| 接口文档 | `tools/tech-lab/validation/README.md`（双平台命令、约定、退出码） |
| 失败案例 | 真实调试中捕获的 4 个（向量残差设计、422/400 语义、traversal 404、units KeyError）全部修复并记录 |
| PR / SHA | PR #10 增量提交（见下）；PR #8 未动 |
| 平台声明 | 全部数字为 **Windows 桌面**（Ryzen/Win11/Python 3.12）；零 iPhone 主张 |

## 剩余风险与 Mac 依赖

1. T07/T08 向量待 Mac SIMD 对拍（预期一致，不一致即实现差异）
2. T08 的 +z 前方约定与 ARKit -z 的转换是 Mac 序列化第一件事
3. T12 iPhone 客户端重试节奏（指数退避）未规定——协议只锁语义
4. 全链路为 SYNTHETIC 桌面数据；真机复现按 `test_full_chain.py`
   的 8 阶段脚本执行，不可跳步
