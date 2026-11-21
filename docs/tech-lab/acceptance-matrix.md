# 验收矩阵 (Acceptance Matrix)

每项技术对应的交付物与**可执行证据**。更新状态时必须附测试命令与结果。

全部命令在仓库根执行（依赖 `backend/.venv`）：

```powershell
Push-Location backend; .venv\Scripts\python.exe -m pytest tests -q; Pop-Location
# Wave 2 基线（本分支）: 139（main 既有）+ 123（techlab）= 262 passed
# PR #7 合并后再加 18（W005 回归）
```

图例：✅ 已交付并测试；◐ 部分交付（真机在 Mac）；— 未开始；⏳ 后续 Wave。

| # | 状态 | 研究+资料 | PoC/不可行证明 | 自动化测试 | 性能/精度指标 | 真机验收 |
|---|---|---|---|---|---|---|
| T01 | POC_READY（协议层） | ✅ ADR-0001 | ✅ key_lifecycle | ✅ 4 测试 | 协议行为级 | ⏳ t01 脚本待 Mac |
| T02 | POC_READY | ✅ risk T02 | ✅ location_meta | ✅ 8 | 质量分级阈值表 | ⏳ Core Location |
| T03 | POC_READY | ✅ W003 边界 | ✅ time_integrity | ✅ 9 | 8 类异常检出表 | —（纯算法） |
| T04 | POC_READY（工具链） | ✅ 实验方案修订 | ✅ 清单生成器+统计+验收 CLI | ✅ 21（W-02 13 + W-03 8） | 合成集结构断言（**零真机数据**） | ⏳ 132 条唯一 ID 待 Mac 实测 |
| T05 | DESKTOP_VERIFIED | ✅ ADR-0002 | ✅ derivative_model（W-10 强化） | ✅ 13 | 伪造/篡改/断链 100% 拒绝 | ⏳ iOS 渲染 |
| T06 | DESKTOP_VERIFIED | ✅ t06-fault-matrix.md | ✅ storage_integrity | ✅ 10 | F1-F8 全过；恢复零删除 | ⏳ iOS 断电对照 |
| T09 | DESKTOP_VERIFIED | ✅ docstring 边界声明 | ✅ export_engine（含 DOCX/XLSX） | ✅ 9 | 100/500/1000 张：0.04/0.21/0.42s，峰值内存 ≤2.31MB | ⏳ iOS 编码 |
| T10 | DESKTOP_VERIFIED | ✅ 诚实性规则 | ✅ watermark_engine | ✅ 14 | 防伪语义测试全过 | ⏳ CoreGraphics |
| T11 | DESKTOP_VERIFIED | ✅ 桌面/iPhone 分离声明 | ✅ project_db | ✅ 11 | 10 万条基准（DESKTOP） | ⏳ iPhone 重测 |
| T13 | DESKTOP_VERIFIED | ✅ W-08 设计 | ✅ evidence_package | ✅ 14 | 四类篡改+衍生图+历史密钥全检出 | —（完备） |
| T18 | DESKTOP_VERIFIED（Win）/ macOS 待冒烟 | ✅ CLI README | ✅ evidence_verifier + anchor_verifier | ✅ 10（子进程端到端） | 字节级锚点对拍真实服务 | ⏳ macOS 冒烟 |
| T07 | POC_READY | ✅ wave3-design | ✅ drawing_pins | ✅ 10 | ≥4 点校准/退化拒绝/佐证三要素 | PDF 取点 UI、向量对拍 |
| T08 | POC_READY | ✅ 坐标系合同+闭式 | ✅ ar_persistence | ✅ 13 | 投影 oracle 0.5px；ARKit 转换 1e-12 回放 | ARKit 实测 |
| T12 | DESKTOP_VERIFIED | ✅ 协议模型+端点 | ✅ upload 端点+客户端 CLI | ✅ 12 端点+链路 | 丢 8/补 8/字节级一致 | iPhone 退避节奏 |
| T14 | RESEARCHED | ✅ t14-c2pa-diff 十项差异表 | ✅ 桥接映射+哈希不变量 | ✅ 4 | — | SDK 集成+证书（产品决策） |
| T15 | POC_READY（骨架） | ✅ wave4-report | ✅ defect_detection | ✅ 6 | 合成：recall 0.76，污渍零误报 | Core ML+真实数据 |
| T16 | POC_READY（协议） | ✅ wave4-report | ✅ voice_chain | ✅ 7 | 防编造强制引用全过 | on-device 识别实测 |
| T17 | POC_READY（骨架） | ✅ wave4-report | ✅ change_detection | ✅ 6 | 曝光+30 零误报；平移精确找回 | vImage+真实照片对 |

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
