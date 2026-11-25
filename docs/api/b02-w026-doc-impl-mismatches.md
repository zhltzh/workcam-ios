# B02-W026 文档-实现不一致清单（基线 31917e3）

版本 v1 · 分支 `dev/windows-b02-w026-api-contract` · 2026-11-25

**核对方法**：以代码与真实响应为唯一事实来源；每条差异均由真实 HTTP 调用或代码逐行阅读证实（多数有 `docs/api/b02-w026-mock-data.json` 的真实响应或 `backend/tests/test_b02_w026_contract.py` 的断言佐证）。**未修改任何生产代码**；对文档本体只做本任务交付内的增补（docs/api/ 新文档），不回改历史文档。

| # | 类型 | 位置 | 差异描述 | 证据 | 处置 |
|---|---|---|---|---|---|
| 1 | 文档缺口 | `docs/mvp/w008-sync-protocol.md`（事件流） | 文档写 `entity_type` 过滤词表 project/photo/evidence_link/original，未说明**基线实际只有 project/photo 两类事件被产生**（`_emit_event` 仅被 `_exec_project_create/_exec_project_update/_exec_photo_register` 调用）；evidence_link/original 在过滤词表里但永远拉空 | `grep _emit_event`：4 处，全在 sync.py；契约测试 `test_event_stream_emits_only_project_and_photo` | 已在 api-reference §4.1 写明基线事实；不动原文档 |
| 2 | 跨分支契约差异 | `origin/dev/windows-b02-w014-offline-sync` 的 `b02-w014-offline-sync-spec.md` §4 | 该文档示例 payload **不含 `operation_id`**；基线 executor（`ProjectCreateOp(**payload)` 等）把 `operation_id` 设为必填——按 W014 示例向基线服务器发请求得到 **500** | 契约测试 `test_sync_payload_must_carry_operation_id`（500 钉死）；mock F6 全部 payload 均带 | 在 api-reference §4.1 与 Mac 指南 §7 显著标注；W014 合入时须复核其分支行为再定最终契约 |
| 3 | 实现缺口（typed error 违例） | `app/api/sync.py` executors | executor 内的 pydantic 校验异常（`ProjectCreateOp(**payload)` 等）未捕获 → **HTTP 500**，而 sync 协议承诺"确定性失败是 typed 回执"。非 422/4xx/typed error | 同上测试；真实调用复现（本任务首次跑通 F6 时发现） | 不改生产代码（纪律）；列入剩余问题，建议 B02 后续任务在 executor 外包一层 `except ValidationError -> rejected invalid_payload` |
| 4 | 文档缺口 | `docs/mvp/workcam-mac-integration-rc1.md` §联调、`w006-api-contract.md` | `POST /register-key` 的三个参数是 **QUERY 参数**（FastAPI 对无注解 str/int 的默认），不是 JSON body——两文档均未提及，Mac 端按 REST 直觉发 body 会得到 422 或静默不注册 | mock F3 `register-key-test`（URL-encoded query 调用成功）；openapi.json 导出如实反映为 query parameters | api-reference §3.1 与 Mac 指南 §4 标注 ⚠️ |
| 5 | 文档缺口 | time-anchor 面（`docs/mvp/w006-api-contract.md` F3 节引用锚点回执时） | **anchor 响应不是合法 receipt**：`TimeAnchorReceipt` 必填 `device_reported_start_time/end_time/stored_at`（unsigned 字段，由客户端补回），直接把 `/time-anchor/anchor` 响应提交 `/time-anchor/verify` 得 **422**。任何既有文档没有写明这一组装步骤 | mock ERRORS `anchor-response-is-not-a-receipt`（真实 422）+ F3 `anchor-verify`（组装后 200 valid） | Mac 指南 §4 与 Swift 模型 `TimeAnchorReceipt` 注释写明 |
| 6 | 文档含糊 | `app/api/time_anchor.py` `/anchor` docstring | docstring 说 "Validation error (e.g., invalid characters)"；实际守卫（`_validate_field`）**只拒控制字符（<0x20, 0x7F）与 `=`**（防字段注入）。空格、标点完全合法——"batch with spaces!" 实测 200 | 实测：`batch with spaces!` → 200；`bad=batch-id` → 400（mock ERRORS `time-anchor-invalid-batch-id`） | api-reference §3.2 写明真实守卫；docstring 原样保留（不改生产代码） |
| 7 | 代码卫生（遗留死代码） | `app/api/upload_auth.py` | `issue_challenge` / `verify_identity`（内存版挑战-响应）已被 `device_identity` 的 SQLite 版替代（`upload.py` 路由直接 import 后者），但 `upload.py` 顶部仍 import 这两个函数而**从不调用**——误导读者以为内存版在用 | `grep issue_challenge\|verify_identity app/api/upload.py`：仅 import 行（50/52），实际使用在行 253-254（device_identity 版） | 不改生产代码；记录待清理。另注：upload 面限流用的是 `upload_auth._check_rate`（**内存桶**，重启清零），业务面是 `device_identity.check_rate`（**SQLite 持久**）——两套并存是现状 |
| 8 | 文档信息缺口 | `docs/mvp/w006-api-contract.md` §2 幂等 | 幂等重放的"相同内容"判定字段是 `original_sha256 + original_filename + byte_size`，**不含** `mime_type/captured_at/device_id`（后三者不参与冲突判定，改了也算重放）——文档只写"相同内容重放" | `mvp.py::register_photo` 三字段比对（行 274-276） | api-reference §1.5 写明参与判定的字段清单 |
| 9 | 文档过时 | `docs/mvp/workcam-mac-integration-rc1.md` | 基线标注 `26e71fc`（W009 时代），接口面不含 W011（衍生图）/W012（报告）/W013（标注问题）——继续按它联调会漏掉 20 个端点 | 文档第 17 行自述基线；openapi.json 当前 47 paths | 新指南 `b02-w026-mac-integration-guide.md` 显式取代之（原文档保留作历史） |
| 10 | 文档信息缺口 | `docs/mvp/w012-report-spec.md` §P2 | 报告 detail 的 `snapshot` 键是 **photo UUID**（动态键，按 photo_ids 请求顺序插入），值是逐照片快照 entry——spec 只写"详情（含快照）"未写键形态 | `reports.py::report_detail`；契约测试对 report-detail 的 snapshot 特判 dynamic keys | api-reference §4.5 与 Swift 模型注释写明 |
| 11 | 文档缺口 | `docs/mvp/w006-api-contract.md` §错误结构 | 业务 typed envelope 与 **pydantic 422**（detail 为数组的另一种形态）并存；既有文档只描述了前者 | FastAPI 默认行为；契约测试 `test_offset_pagination_and_clamp` 的 422 分支 | api-reference §1.2 与 Swift 模型"接入注意 #4"写明两类错误体 |

## 核对范围声明（诚实边界）

- **已逐行核对**：`app/api/` 全部 12 个路由模块、`app/main.py`、`app/db.py`（ApiError/序列化）、`app/models/`（capture/time_anchor）、`app/services/device_identity.py`、`security_config.py`、`original_store.py`/`watermark_render.py` 的参数面。
- **逐项核对 API 断言**：`docs/mvp/w006-api-contract.md`、`w008-sync-protocol.md`、`w012-report-spec.md`（P2 节）、`w013-annotation-spec.md`、`w013-r1-acceptance.md`、`workcam-mac-integration-rc1.md`；以及未合入分支 `b02-w014-offline-sync-spec.md`、`b02-w017-photo-batch-api.md`、`w015-report-enhance-spec.md`（与基线的关系已写进 api-reference 附录 A，差异本身不算基线不一致）。
- **未逐字核对**：`w006-data-model.md`、`w007-original-storage.md`、`w008-conflict-policy.md`、`w009-deployment.md`、`w009-security.md`、`w011-watermark-spec.md`、`w012-security-supplement.md`、`w013-mac-handoff.md` 的全文（非 API 契约直接标的部分）。如需全量校准，列为后续任务。

## 与 openapi.json 相关的说明

- `docs/api/openapi.json` 的 paths/methods/全部 schema 均为运行中应用的**机器真实导出**；人工只加了 `x-b02-w026-enrichment` 节点列明的标注字段（description/summary/security/个别 example）。
- openapi 里 `/verify`（response_model）等带 schema 的端点组件如实导出；大多数 `/api/v1` 端点返回运行时 dict（无 response_model），其 openapi schema 为空对象——**这正是需要 mock 数据 + 契约测试补足 schema 事实的原因**，Mac 端建模以 mock 响应字段为准。
