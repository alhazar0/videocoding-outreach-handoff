# 验证结果

这是 2026-11-01 交接副本上的实际验证，不复用旧轮次的通过数字。

| 检查 | 结果 |
|---|---|
| Python unittest discover | 362 项中 356 通过，6 项错误 |
| 新增交接凭证开关 | 已包含在 362 项中，默认拒绝 Keychain/网络凭证读取，两项通过 |
| Node 单元检查 | 16 / 16 通过 |
| npm run build | 通过，退出码 0 |
| tsc --noEmit | 未通过，35 个诊断，Cloudflare/D1 类型及 implicit-any 类别 |
| 统计核对 | 接受数、分组分母、回复数、时间阈值、产品 ID 去重、日计数等 10 项核对通过 |
| schema checkpoint | 可在内存 SQLite 完整创建表、索引和触发器，quick_check ok，无联系人数据 |
| 生产动作 | 无发信、无开通、无恢复后台；原运行目录代码未改 |

6 项 Python 错误都位于 tests/test_send_coupon_replies.py 的旧最小 fixture：当前查询依赖 creator_profiles 等表，但测试未建立。此次没有修改生产 coupon 逻辑绕过错误，也没有称全仓通过。完整报错保存在交接包外层 private 日志，机器摘要在 handoff/validation.json。

验证复用了原环境安装的 node_modules 和 Python 3.13 虚拟环境；没有宣称在全新机器上执行过 npm ci。仓库不携带依赖目录，接手人须按 lockfile 重装并复验。构建产物和暂时依赖链接不会进入 Git。

开发机默认 /usr/bin/git 受损坏的 Xcode 选择影响；本次使用现有 /Library/Developer/CommandLineTools/usr/bin/git，不改系统 Xcode 配置。这是当前机器工具链问题，不是仓库源码问题。
