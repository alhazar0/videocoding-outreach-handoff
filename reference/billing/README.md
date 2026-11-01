# 外部续费排期依赖参考

这里补入 3000 项目 build/billing-local.mjs 实际引用的 renewalSchedule.js 及其原测试，来自本地 videocoding-renewal-reminders 项目。两文件未修改，SHA-256 见 handoff/external-reference-manifest.json。

这只是排期计算与本地 IP 时区解析模块，不是完整产品账单后端、退款接口或发送程序。原插件仍引用原项目路径，本次没有接通账单插件或改生产逻辑。接手人可以在独立开发任务中改为仓库内路径并验证。

scheduledReminderAt / withinSendWindow 使用 Node 标准库，以下 3 项原测试已通过：

```bash
node --test reference/billing/renewalSchedule.test.js
```

resolveIpTimezone 另需 geoip-lite 及本地数据；原模块指定的隔离 vendor 目录当前不存在，本仓库不携带 GeoIP 数据或声明已验证定位。不要为了让它返回结果而把 IP 发给未经批准的外部服务。此参考目录使用 CommonJS package 边界，不影响主应用 ESM。

原本地账单来源、缓存与 coupon audit 已单独放入私有数据包。其中 coupon audit 是发码/回信审核而非核销流水，账单缓存不等于现在的 Stripe 状态。私有记录的日期、范围和缺口必须保留。
