# 免费部署的边界与保护

核对日期：2026-11-17（北京时间）。

## 已配置的零费用路线

- 标准 GitHub 托管 Linux 运行器：`ubuntu-24.04`。
- 仅允许**公开仓库**分配运行器；私有时整个 job 被跳过。
- 不用 Larger Runner、GitHub Actions 缓存、计费的 Artifact 存储、Packages、Git LFS、付费 API 或外部服务器。
- 数据 ZIP 作为 GitHub Release 文件发布。使用运行时临时 `GITHUB_TOKEN`，不需要新建 PAT。
- 每次最多 15 分钟、并发为 1，防止重复点击同时抓取。
- 没有付费降级逻辑。免费数据源失败就报错，保留上一次成功 Release 的原日期。

## 为什么私有仓库暂不运行

GitHub 为私有仓库提供账户共享的免费计算额度，超额可能计费；仅设置单次超时不能保证整个账户不超额。当前连接不能核查或设置账户计费预算，因此不能把“通常在免费额度内”承诺成“绝不收费”。

本仓库只包含公开行情采集与计算代码，不含用户持仓、账户凭据和原始上传 ZIP。若改为 Public，代码和后续成功 Release 中的公开市场数据都可被其他人查看。公开仓库标准运行器目前免费。

如需保持 Private，必须先由仓库所有者核实账户 Actions **$0 且超额停止**的计费设置，然后明确授权调整运行保护。在此之前，手动点 Run workflow 也只会跳过，不能得到数据包。

## 官方依据

- [GitHub Actions 计费](https://docs.github.com/en/billing/concepts/product-billing/github-actions)：公开仓库标准运行器免费；私有仓库使用共享计算额度，超额可能计费；Larger Runner 另外计费。
- [Actions 计费与使用](https://docs.github.com/en/actions/concepts/billing-and-usage)
- [Release 文件限制](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)：每文件小于 2 GiB，Release 总大小与下载带宽没有配额限制。
- [手动运行工作流](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
- [Nasdaq 官方交易日程](https://www.nasdaq.com/market-activity/stock-market-holiday-schedule)

免费以 GitHub 和数据源当期政策为准。本次没有开通付费服务，也没有修改账户支付设置。
