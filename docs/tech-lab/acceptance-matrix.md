# 验收矩阵 (Acceptance Matrix)

每项技术对应的交付物与**可执行证据**。更新状态时必须附测试命令与结果。

图例：✅ 本轮已交付并测试；◐ 部分交付（真机在 Mac）；— 未开始；⏳ Wave 2+。

## 测试基线说明（R1-A5，防误读）

本分支（PR #8）全量测试 = **main @ 17d9699 的 139 项既有测试 + techlab 新增**。
W005 的 +18 项测试位于 `dev/windows-w005`（PR #7，待审核合并），**不在本分支基线内**。
因此本分支约 170 项与 PR #7 分支的 157 项并存，差异全部来源于此，**不是回归**。
PR #7 合并后两线自然归一。

| # | 状态 | 研究+资料 | PoC/不可行证明 | 自动化测试 | 已验证能力 | 未验证能力（待 Mac/后续） |
|---|---|---|---|---|---|---|
| T01 | POC_READY（协议层） | ✅ ADR-0001 + W005-2 对照 | ✅ 密钥生命周期协议模型 | ✅ `test_key_lifecycle.py` | **协议行为**：公钥存档可复验旧证据（G1）、同 device 新密钥显式冲突（G2）、key_id 轮换后旧回执可验（G3）、验证不依赖私钥（G4） | SE 硬件生成/签名、Keychain 持久化、卸载真实清钥 —— 全部待真机（脚本 `experiments/t01-se-acceptance.md`）。**软件密钥测试不得报告为真机通过** |
| T02 | POC_READY | ✅ risk-register T02 | ✅ `techlab/location_meta.py` | ✅ `test_location_meta.py` | 来源/质量分级语义；manual 永不显示 verified；离线 network 封顶 C 级；批量评估 | Core Location 真实离线定位分布与精度实测 |
| T03 | POC_READY | ✅ W003 边界 + 异常分类 | ✅ `techlab/time_integrity.py` | ✅ `test_time_integrity.py` | 6 类时钟异常检出（回拨/跳变/时区/乱序/单调钟/锚点冲突）；诚实性分级上限 | —（纯算法，无真机项；锚点行为已在 W003 真实测试覆盖） |
| T04 | RESEARCHED | ✅ ARKit 能力研究 | ◐ 实验方案 + CSV 分析工具（带自测） | ◐ 分析工具自测（合成数据） | 分析链路：误差/重复性/失败率统计可用 | **全部实测数据**待真机（96 次测试矩阵）；当前零真机数据，按规则停在 DEVICE_PENDING |
| T05 | POC_READY | ✅ ADR-0002 | ✅ `techlab/derivative_model.py`（W-10 加固） | ✅ `test_derivative_model.py` | 原图 write-once；伪造 OriginalPhoto 对象被拒（身份校验）；真实文件字节复验与磁盘篡改检测；衍生图回链 + 防冒充原件 | iOS 端衍生图渲染与 sidecar 落地（Wave 2 前置） |
| T06 | LIMITED | ✅ 现有原子写盘点 | — | ⏳ Wave 2 | 后端锚点存储原子写+回滚已验证（W003-R3） | 系统化故障注入（Wave 2） |
| T07–T12, T14 | NOT_STARTED | — | — | — | — | — |
| T13 | RESEARCHED | ✅ 绑定方式盘点 | ⏳ Wave 2 证据包格式 | — | 签名绑定哈希、重放检测（W002/W005 已有） | 独立证据包文件格式（Wave 2） |
| T15–T17 | NOT_STARTED | — | — | — | — | — |
| T18 | LIMITED | ✅ 现有工具差距分析 | ⏳ Wave 2 离线 CLI | — | 联网联调工具 13/13（W005） | 离线独立验签 CLI（Wave 2） |

## 本轮新增测试的证据索引

全部命令在仓库根执行（依赖 `backend/.venv`）：

```powershell
Push-Location backend; .venv\Scripts\python.exe -m pytest tests\test_techlab -v; Pop-Location
# 预期：test_techlab 全部通过（数量见当轮 Wave 报告）

Push-Location backend; .venv\Scripts\python.exe -m pytest tests -q; Pop-Location
# 预期：139（main 既有基线）+ techlab 新增全部通过，无回归
# （数字以本轮实际输出为准；与 PR #7 分支 157 的差异见上方基线说明）
```

## 真机验收模板（iPhone 专属，随实验方案交付）

每个真机实验必须回填 `experiments/<id>-results.md`：

```markdown
## T0X 真机结果
- 设备型号 / iOS 版本 / 日期：
- 执行步骤与实际输出：（原始数据文件路径 datasets/）
- 成功/失败/失败率：
- 与 Windows 侧预期的一致性：
- 结论：状态从 DEVICE_PENDING 改为 DEVICE_VERIFIED / LIMITED / REJECTED
```

没有此报告，状态永远停留在 DEVICE_PENDING。
