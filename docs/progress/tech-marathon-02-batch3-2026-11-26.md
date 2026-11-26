# TECH-MARATHON-02 · 第三批进度（2026-11-26 深夜）

分支 `dev/windows-tm-geometry` · HEAD `b9093c1`（已推远端）
全量测试：**1311 passed** · **质量门禁：PASSED**（9 gates 全绿）

## 本批交付（第二队列收口）

| 任务 | Commit | 内容 | 状态 |
|---|---|---|---|
| TM68 冲突详情引擎 | `b8a120e` | TM34 全部冲突类型 → UI 可用详情（双方版本、服务器当前版本提取、允许动作清单、**绝不自动选边**；非冲突码返回 nil 不误报）；规则测试 | CROSS_PLATFORM_PENDING |
| TM71/TM73 数据层 | `b8a120e` | 统一本地模型（**事实字段而非存储状态**——管道态由 TM70 推导）、LocalDataSource/Repository 协议、OfflineFirst 门面（写先本地后 outbox；**原图字节永不走 outbox**）、内存 double、**SQLite 参考 Schema v1 文档**（WAL、部分摘要索引、拍摄暂存表、与服务端同纪律的迁移策略） | CROSS_PLATFORM_PENDING |
| TM72 照片索引与缓存 | `b8a120e` | 字节预算 LRU（**类型系统结构性只许衍生图**——无原图 API，测试钉死）、keyset 分页无漂移（与服务端稳定序一致）、重复照片=候选永不删 | CROSS_PLATFORM_PENDING |
| TM89 真实端到端 | `e921ff0` | 零 mock 全链：注册→项目→照片(元数据+字节)→**真签名 manifest verify（cryptographically_valid）**→标注+问题生命周期→台账幂等重放（duplicate 回执）→报告 PDF+交付 ZIP→**W018 CLI 独立验证 PASS**→TM06 十维桥接（诚实状态）；敏感材料核查（库中只有公钥） | **DESKTOP_VERIFIED** |
| TM94 统一质量门禁 | `b9093c1` | 一命令 9 门禁：全量测试/schema/OpenAPI live==repo(88 ops)/迁移(fresh v1..v14+毒级回滚)/依赖锁/向量/文档链接/敏感扫描/**生产-实验隔离**（test-keys 白名单带理由 README；字符串字面量不误报；真实密钥体=失败；新 techlab 导入=失败）；**GATE: PASSED** | DESKTOP_VERIFIED |

## 门禁过程发现并处置的问题

1. OpenAPI 门禁的 repo 副本路径差一层（worktree 根 vs backend）→ 修正。
2. 文档链接门禁抓到 3 个历史陈旧引用 → 允许清单 + 注明移位原因（其余链接继续强制）。
3. 敏感扫描首版误报字符串字面量 PEM 头 → 改为要求真实密钥体（base64 行）。
4. **test-keys/ 处置决定**：这些是 W003 时代发布的 RFC 式可复现密钥对
   （离线验证向量需要私钥半来签名公开向量；真机密钥在 Secure Enclave
   永不离开）。加 README 说明 + 门禁白名单**仅此目录**。

## 网络事件

推送经历代理进程死亡（10808/10809 均断）→ **直连可用**，全部提交经直连
推送成功，0 积压。

## 下一批

- TM91 弱网测试工具 / TM93 升级兼容测试（Python 侧）
- TM95 跨平台环境（macOS bootstrap.sh 语法可验证版）
- TM74-81 工程业务（模板/清单/命名/资产/测量记录/报告组装/交付包）
- TM82-87 UI 参考（SwiftUI XCODE_PENDING）
