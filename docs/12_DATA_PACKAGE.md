# 加密数据包下载与解压

2026-11-09，按项目负责人要求，交接数据通过本仓库的 GitHub Release 提供。原始数据包制作于 2026-11-01，外联数据观察截止仍为 2026-10-20；本次只增加下载方式，没有刷新邮箱、产品状态或恢复外联。

## 下载

打开 [handoff-data-20261109 Release](https://github.com/alhazar0/videocoding-outreach-handoff/releases/tag/handoff-data-20261109)，在 Assets 下载：

- `videocoding-operations-20261101.encrypted.zip`：AES-256 加密的数据包，约 317 MB。
- `SHA256SUMS.txt`：加密文件 SHA-256。
- `encrypted-package-manifest.json`：大小、哈希、加密与验证方式。
- `HANDOFF-SUPPLEMENT.txt`：接手补充说明。

`git clone` 及 GitHub 自动生成的 Source code ZIP/TAR.GZ 仅含代码和文档，不含 Release 数据附件。解压密码由负责人在仓库之外单独提供，不存在 Git 历史、README 或 Release 文案中。

## 校验和解压

Windows PowerShell 可以先计算下载文件的 SHA-256，与 SHA256SUMS.txt 比较：

```powershell
Get-FileHash .\videocoding-operations-20261101.encrypted.zip -Algorithm SHA256
```

使用支持 AES 加密 ZIP 的解压工具，例如 7-Zip，打开文件并在提示时输入密码。不要把密码写进公开命令、日志或 issue。系统自带解压器若提示不支持格式，应使用支持 AES ZIP 的工具。

解密后得到 `PRIVATE-videocoding-operations-20261101.tar.gz`，继续解压到仓库目录之外的专用私有目录。工具可能先解出 `.tar`，再解开该文件。内层 TAR.GZ 的 SHA-256 应为：

```text
bfb80e704046e71976a11e57503d1e54cab99e0a191a2ea2187b7713366ad533
```

主要文件：

```text
private/
  creator-details.json
  protected-state.json
  operations-packet/
    README-PRIVATE.txt
    manifest.json
    databases/outreach.sqlite
    databases/lead-discovery.sqlite
    operations-evidence/
    runtime-analysis/
    runtime-source-reference/
    dashboard-private-data/
```

包内 manifest 提供文件哈希、数据库表计数和明确脱敏项。两份数据库是通过只读源连接使用 SQLite backup API 保存的一致性快照；邮箱/平台密钥须另行配置。

## 接收后的边界

先只读验证 47,901 个 contacts、30,551 次首次明确接受、34 次首次 unknown、82 个 active hold、22 个 pending service work 等基线。详见 [补充说明](13_HANDOFF_SUPPLEMENT.md)。数据库中旧自动模式不是恢复授权；旧 manifest、已接受或未知邮件、已开通记录均不可重放。

这些是真实私有运营数据。加密包可以从公开 Release 下载，但只有获授权接手人应得到密码；解密后不得重新公开。当前公开仓库仍待同事下载确认后按负责人安排改私有。
