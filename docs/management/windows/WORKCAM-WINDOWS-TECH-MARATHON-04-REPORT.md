# WORKCAM-WINDOWS-TECH-MARATHON-04 · 研发报告（冲刺 1+2，进行中）

管理基线：TECH-MARATHON-04（120 项工作包）
分支：`dev/windows-tm04`（自 `a8b59ed` 创建；RC3 冻结 `1f645b6` 为已验证祖先，未动）
状态源：`docs/management/windows/TECH-MARATHON-04-STATUS.json`
本两冲刺推送：`690c011..c378aae`（远端已同步；push 经本机代理，直连被重置时用 `git -c http.proxy=...`）

## 里程碑：Windows Swift 从 BLOCKED 到真实全绿

TM-03 的 A05 曾以 4 次安装失败如实记为 BLOCKED（"Swift remains
authored+vector-matched but never compiled here"）。本冲刺发现
**Swift Developer Toolkit 6.4.3 已在机器上安装成功**，随即完成：

1. **首次真实编译**（`a46cf1e`）：`swift build` BUILD COMPLETE
2. **首次真实测试执行**（`0de386b`）：96 项 0 失败
3. **全套最终确认**（`d3a9bff`）：**140 项测试，0 失败，exit 0**

### 首次编译/执行揪出并修复的 15 类真实缺陷

这些代码全部带着 "Mac compiles" 的历史标签，但**从未在任何平台编译过**：

| # | 缺陷 | 位置 |
|---|---|---|
| 1 | 元组数组阻止 Equatable 合成 | Annotations.swift |
| 2 | 4 个参考 UI 文件违反包政策 import SwiftUI | WorkCamModels |
| 3 | 生成器把 Python list repr（单引号）泄进 Swift 源码 | DesignTokens.swift + gen_design_system.py 源头修复 |
| 4 | 引用不存在的 JSONValue 类型（补 import 复用 Evidence 定义） | APIClient.swift |
| 5 | Evidence→Geometry 缺依赖与 import | Package.swift/EvidenceModels.swift |
| 6 | 三个 Record 声明 Identifiable 却无 id | DataModels.swift |
| 7 | `(some Encodable)? = nil` 无 body 调用不可推断 | APIClient.swift |
| 8 | 8-case 公有枚举上 4-case switch 不完备 | CameraIntrinsics.swift（引入私有 Canonical 枚举根治） |
| 9 | **文档化的 rebase 路径写进 let 常量，不可编译** | Outbox.swift |
| 10 | transformed() 元组标签丢失 | Annotations.swift |
| 11 | FixedClock 永不前进 → "重试到期"测试永远无法通过 | Outbox.swift（新增 MutableClock） |
| 12 | 缓存红线测试强解包必然 nil 的 rawValue → 运行即崩 | PhotoCacheRuleTests |
| 13 | 金向量分支形状假设错误（meters/水平垂直/垂线法线/体积占位自比较） | GeometryGoldenVectorTests（修成真验证） |
| 14 | keyset 测试迭代的是 map 后的值（ph-91..100） | PhotoIndexRuleTests |
| 15 | Dictionary<String, String?> 的 nil 值/缺键混淆（get 与 remove 双双中招） | TransactionAndScheduler.swift |

### 平台事实（如实记录）

- Windows 构建必须用 ASCII `--scratch-path`：lld-link 在中文仓库路径上失败（已写进 manifest）
- SwiftPM frontend 冷构建偶发 StackOverflowException：重试即过（本冲刺两次）
- Foundation `replaceItemAt` 在 Windows 是未实现 fatal stub：AtomicFile 改用 remove+move（窗口语义如实注明）
- 测试资源需 `Bundle.module`（`Bundle(for:)` 在 SwiftPM Windows 下找不到资源）

## 120 项状态（冲刺 2 后）

| 状态 | 冲刺前 | 冲刺 1 后 | 冲刺 2 后 |
|---|---|---|---|
| DESKTOP_VERIFIED | 40 | 65 | **70** |
| IN_PROGRESS | 39 | 30 | 30 |
| NOT_STARTED | 38 | 23 | 18 |
| BLOCKED | 2（Swift/Docker） | 1（仅 Docker） | 1（仅 Docker） |
| CROSS_PLATFORM_PENDING | 1 | 1 | 1 |

**B 队列 12/15 收口**（B04/B06/B07/B13/B15 新增 DV；B08/B10 只剩命名续片；B14 继承 TM91/TM35 证据待整合矩阵）。

### 冲刺 2 新增交付

- **B04** 意图审计：rebindStale 给重绑操作**新幂等 id**（旧 id 换内容必撞服务器账本）、依赖边传递重写、SupersessionRecord 只追加审计 — 12/12
- **B06** ImportPlanner：createNew / linkExisting（B05 已映射=幂等）/ needsDecision（同名碰撞永不自动裁决）— 同套件
- **B07** 整改照片角色：CAS 门控 + 命名跨项目/陈旧版本拒绝；after 存在后 before 不可 replace（追加仍可）；撤回=记录在案的 unlink；liveRoles 从只追加日志推导 — 同套件
- **B13** 多客户端并发 harness（真实后端，7 项 ×两遍稳定）：并发同 revision 恰好一个 applied + 输者 typed revision_conflict；幂等重放=duplicate；跨会话重复提交恰好一个效果；**跨 owner 读 404/写 rejected = 隔离即特性**
- **B15** Sync SDK 交付文档：组件地图、10 行接入路径、按层命名拒绝的完整故障矩阵、诚实限制节（`docs/tech-lab/tm04-b15-sync-sdk.md`）

全量矩阵每项引用真实 SHA + 真实测试文件（`TECH-MARATHON-04-STATUS.json`）。

## 本冲刺新增交付（每个都是真实测试过的）

### A 队列（P0 收口）
- **A01** 基线复验：门禁 12/12 @ 1602 passed（与冻结报告完全一致）；`1f645b6` 祖先关系验证
- **A02** `tm04_remaining.py` → `TM04-REMAINING.json`（77 剩余/32 可立即执行/依赖图）
- **A03** b02 系列 13/15 合入核对；w028 商业储备有意隔离；W-X01..X10 无机器可读定义——如实记录而非编造
- **A04** Swift 6.4.3 实装 + 真编译 + 真测试（BLOCKED 解除）
- **A05** WorkCamCore 首次真实编译 + 15 类缺陷修复 + 依赖方向修正
- **A07** `mac-intake-onecommand.sh` 四步一键接入（bootstrap→pytest→swift test→live 合同，每步 PASS/FAIL）

### B 队列（双向同步核心，Mac 最需要）
- **B01** Outbox 状态机真实测试通过（含传输失败退避、崩溃恢复、rebase 重提）
- **B02** ConflictResolution：四个用户决策（keepLocal 盖 base_revision 走 CAS / takeServer 保留本地负载作审计、**永不删除** / manualMerge 必须用户负载 / deferred 永不自动重试）— 7/7
- **B03** FieldDiff 三方合并引擎（identical/independent/conflicting；只有真冲突才到用户）— 8/8
- **B05** UUIDMappingStore 版本化映射（同对重放幂等；异 server_id 命名拒绝；双绑拒绝）
- **B09** EntityLifecycle 终态语义（tombstone 永久、archived 仅显式恢复且拒新照片、closedTerminal 拒绝一切内容变更）
- **B10** CursorStateStore：owner+entity 作用域游标、AtomicFile 原子持久化、崩溃重启精确恢复、防倒退
- **B11** SyncRecovery HTTP 表（401/403/409/429/5xx/4xx 分流 + Retry-After 夹取 [0,300] + dead-letter 永不删）— 9/9
- **B12** 退避+抖动+可测时钟（MutableClock 修复了不可测的"到期"语义）
- Python 对拍 `test_tm04_sync_core_parity.py`（25 passed）钉死跨语言语义

### C 队列（通用核心）
- **C03** Repository 协议 + revision-CHECKED 保存 + 墓碑删除 + ServiceRegistry DI — 6/6
- **C04** TransactionalStore 事务参考语义（staged 可见、回滚零残留、全有或全无）
- **C05** AtomicFile（temp+swap、读回 SHA-256 校验、模式门控残留清理、纯 Foundation SHA-256 对拍 FIPS 向量）
- **C09** SyncScheduler Kahn 拓扑分批（确定性排序、命名环检测）
- **C13** FeatureGate（**离线永不拦快门**——TM67 红线；同步离线诚实说"排队待传"；restricted 与 denied 区分）
- **C14** 可注入缝完整：clock/store/transport/token/UUID 全部可注入
- **C15** 跨平台对拍升级：Swift 侧从"从未执行"变为**真实执行 140 项**

### E 队列（TM-03 遗留两大评审）
- **E07**（原 C06）图像配准选型评审 + 隔离 PoC：OpenCV ECC/SIFT/ORB 一期否决（依赖成本）；采纳 FFT 相位相关 + 重叠窗 NCC 亚像素精化；19 项测试（已知位移 ≤0.5px 含亮度/噪声；无关图/旋转/平坦/小图/彩色全部**命名拒绝**）；配准结果永不是"修复已验证"
- **E08**（原 C07）对比核心：AlignmentSession（自动建议**未接受不生效**；拒绝即不对齐；手动直对齐；纯显示空间）
- **E09**（原 C08）OCR 选型 + 数据合同：Apple Vision 唯一生产路径；闭合分类学；人工确认状态机（审计 who/when，**置信度阈值永远不能推导出 confirmed**）；isSearchable=B08 门；108 个合法合成样本（程序绘制，零抓取内容）

### F/G/H/J 其他
- **F03** 证据目录扫描成本剖析：1k/5k/10k（全量 3.14s → 增量 0.99s@10k，只报告不断言；`docs/perf/tm04-f03-scan-cost.json`）
- **H01** SBOM：CycloneDX 1.5（39 组件=9 直接+30 传递）+ **OSV 真扫**（走代理）：发现 pip 12 个已知 CVE → 升级 25.0.1→26.3 → 重扫 0 漏洞；覆盖率如实报告
- **H07/J01** RC4 线定义（`RC4-CANDIDATE.md`）：实验隔离清单、冻结规则、验收规则

## 全量验证

- **Python**：冲刺 2 专项 1647+ passed（1663 全量 + B13 7 项等新增，零回归；最终数字以最终一轮全量为准）
- **门禁**：12/12 PASSED（冲刺开始在干净树上重跑）
- **Swift**：152 tests / 0 failures（Windows 6.4.3 真实执行，exit 0；含冲刺 2 的 IntentImport 12 项）

## Mac 待办（不变 + 新增）

1. `bash handoff/mac-ready/mac-intake-onecommand.sh` 一条命令回报四行
2. XCODE_PENDING 参考视图（E02-E10）编译——现在 WorkCamCore 在 Mac 是**已验证可编译**的包
3. OCR 真机识别精度按样本集分类报告（en-US 起）；配准 PoC 若要生产化需 phase-2 评审
4. 真机验收清单不变（TM55 十项等）

## 阻塞与限制（如实）

- Docker（I09）：本机无 Docker Desktop，authored-never-built 保持 BLOCKED
- 网络间歇：push 需走 `git -c http.proxy=http://127.0.0.1:10809`；bundle 备份 `dev-windows-tm04-a8b7448.bundle` verify okay
- B13/B14/B15/B06/B07/B04/D 剩余/G 企业储备/I 商业化：下一冲刺按依赖图推进（TM04-REMAINING.json）

## 下一步

第二批：B04/B06/B07/B13/B14/B15（同步 SDK 收口）→ D 队列业务整理 → G 性能分级 soak → H 企业储备 → J08 RC4 冻结。
