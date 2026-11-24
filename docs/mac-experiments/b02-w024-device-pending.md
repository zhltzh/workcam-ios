# B02-W024 Mac 端真机实验清单（全部状态：DEVICE_PENDING）

> Windows 侧只交付模型/协议/测试工具；以下实验必须由 Mac + 真实 iPhone
> 执行。**当前状态一律为 DEVICE_PENDING，尚无任何真机数据**；下表含每项
> 实验的步骤与记录格式要求，实验完成后由 Mac 侧按相同格式回填。

## 通用要求

- 每项实验记录：设备型号 / iOS 版本 / 实验开始-结束时间 / 步骤日志 /
  原始读数（导出 JSON 或截图）/ 结论（验证 / 推翻 / 不确定 假设 A1-A3）。
- 所有读数导出为 JSON，字段命名对齐 `timegeo_trust.py`：
  `monotonic_ms`、`boot_id`、`device_wall_time`、`utc_offset`、
  `horizontal_accuracy`、`fix_timestamp`、`is_simulated`。
- 实验期间关闭 Wi-Fi 与蜂窝（离线模拟），完成后恢复网络再记录同步行为。

---

## E1. 单调钟抗系统时间修改（验证假设 A1）

**状态：DEVICE_PENDING**

1. App 启动即记录 `monotonic_ms`（`CFAbsoluteTime`/`DispatchTime` 换算 ms）
   与墙钟，每 60s 采样一次，共 5 个样本。
2. 手动把系统时间**回拨 1 小时**，继续采样 5 个样本。
3. 手动把系统时间**前拨 3 天**，继续采样 5 个样本。
4. **记录格式**：`[{t_wall, monotonic_ms}, ...]` 三段各 5 条。
   **判定**：回拨/前拨后 `monotonic_ms` 是否继续按真实速率前进
   （A1 成立 ⇒ 单调差 ≈ 采样间隔；若单调差跳变 ⇒ A1 推翻，W024 回拨检测规则需改）。

## E2. 重启清零单调钟 + boot_id 可观测性（验证假设 A2）

**状态：DEVICE_PENDING**

1. 记录当前 `monotonic_ms` 基线与启动时间。
2. 正常重启 iPhone；重启后记录首个 `monotonic_ms` 读数与开机时间。
3. 若可行，记录 `sysctl kern.boottime` 等价物（iOS 上可尝试
   `NSProcessInfo.systemUptime` 反推 boot_id 候选）。
4. **记录格式**：`{pre_reboot: {monotonic_ms, wall}, post_reboot: {monotonic_ms, wall, uptime_s}}`。
   **判定**：重启后单调读数是否显著回退/清零；`systemUptime` 与单调钟是否同源。

## E3. 长时间离线推算误差（校准 EXTRAPOLATION 误差模型）

**状态：DEVICE_PENDING**

1. 联网时与服务器完成一次锚定（记录 `server_received_at` 与
   `monotonic_ms_at_anchor`）。
2. 断网离线 **72 小时**，期间每小时拍摄 1 张（共 72 条样本，
   对齐 W-003 的 72h 模拟）。
3. 恢复网络，记录服务器时间与本地单调钟差。
4. **记录格式**：`[{seq, device_wall_time, monotonic_ms, captured_offline}, ...]`
   + 恢复时刻 `{server_time, monotonic_ms}`。
   **判定**：实测漂移率是否在 50ppm（0.18s/h）保守假设内；据此调整
   `EXTRAPOLATION_BASE_ERROR_S / MONOTONIC_DRIFT_PPM` 或维持保守值。

## E4. GNSS 精度分级实测（校准 A/B/C/D 阈值）

**状态：DEVICE_PENDING**

每档采集 ≥20 个 fix，记录 `horizontal_accuracy` 分布：

| 场景 | 预期档位 |
|---|---|
| 开阔天空、静置 | A（≤5m） |
| 城市街道、行走 | B（≤15m） |
| 楼旁/林荫 | C（≤50m） |
| 室内窗边 / 地库入口 | D（>50m 或无 fix） |

**记录格式**：`[{scene, fix_timestamp, lat, lon, horizontal_accuracy}, ...]`。
**判定**：`horizontal_accuracy` 是否如实反映误差（对照已知点位距离）；
地下/无 GNSS 场景回落行为（NO_LOCATION / STALE_FIX 出现频率）。

## E5. 模拟定位检测边界（验证假设 A3 的启发式信号）

**状态：DEVICE_PENDING**

1. 用 Xcode "Simulate Location" 注入静态坐标，采集 30 个 fix：
   观察 `is_simulated`（如能读取）、`horizontal_accuracy` 是否异常小/恒定。
2. 注入**连续变化**的模拟轨迹（人行速度），观察是否仍可辨识。
3. 对照组：真实行走 30 个 fix。
4. **记录格式**：`[{injected: bool, fix_timestamp, lat, lon, horizontal_accuracy, detectable_flags}, ...]`。
   **判定**：三个启发式信号（显式标志 / 连续 ≤1m 完美精度 / 速度矛盾）
   在真机上的触发率与误报率；`SIMULATION_SUSPECT` 锁存策略是否合理。
   **注意**：A3 声明"无公开 API 保证检出"——实验目的是标定启发式检出率，
   不是证明完备检测。

## E6. 时区切换双路径（UTC 连续 vs 手动改时区）

**状态：DEVICE_PENDING**

1. 自动时区：开启"自动设置"，飞行/模拟切换时区，采样墙钟+偏移。
2. 手动时区：关闭"自动设置"，手动改时区，采样墙钟+偏移。
3. **记录格式**：`[{utc_offset, device_wall_time, monotonic_ms}, ...]` 两条路径。
   **判定**：两条路径的"本地表示变化 vs UTC 值跳变"特征是否与
   `timezone_shift / manual_tz_change` 判定规则一致。

## E7. 时钟回拨 + 重启 + 长离线组合（W024 状态机全路径真机复核）

**状态：DEVICE_PENDING**

按模型测试 `test_session_walk_propagates_after_anomaly` 的剧本在真机重演：
锚定 → 离线拍摄 → 回拨 → 拍摄 → 重启 → 拍摄 → 恢复联网 → 重新锚定。
**记录格式**：完整事件日志（每步 `{wall, monotonic_ms, boot_id, event}`）。
**判定**：真机序列经 `assess_offline_session` 评估后的状态序列是否为
`SERVER_ANCHORED → EXTRAPOLATED_* → UNVERIFIABLE_ANOMALY →
DEVICE_REPORTED_ONLY(degraded) → DEGRADED_RECOVERED`。

---

## 回填要求

Mac 侧完成实验后，在本文件追加"实验结果"章节（保留原始数据引用），
并在 `docs/progress/` 对应进度文档中更新假设 A1-A3 的验证状态。
未完成前，任何文档不得引用"真机已验证"字样。
