# 部署验证记录

验证日期：2026-11-17（北京时间）。

## 一体化晨报交接验证

[本次全流程运行](https://github.com/chungouoo/NDXTMC/actions/runs/44042307782) 已通过 24 项测试，并成功发布数据包及 `data/latest_morning_input.json`。

读取方通过 GitHub 文件接口实际取得 READY JSON，核对本轮 request_id、run_id、生成提交、北京时间请求日期、实际交易日和 47/47 覆盖率。JSON 含 NDXTMC 1,170 条、NDXT/NDX 各470条完整官方指数序列；读取方另用 JavaScript 独立复算 RSI14、MA20/40/50/200及三条指数1/5/20日收益，全部与输入一致。

原「纳指晨报」任务已配置为从 2026-11-18 起每日北京时间08:00串行完成取数、校验、读取交接、原有深入研究及原对话推送。单独的数据包任务已暂停合并；原有研究规范保留。首次完整定时执行仍待次日实际运行，不能把本次配置与数据交接验证称为已经发生的次日推送。

## 当前结论

**GitHub 云端抓取、打包、独立验算、Release 发布和实际下载均已通过。**

- [成功的 GitHub Actions 运行](https://github.com/chungouoo/NDXTMC/actions/runs/44038926561)
- [已发布的数据包](https://github.com/chungouoo/NDXTMC/releases/tag/data-2026-11-16-44038926561-1)
- [下载此版本 ZIP](https://github.com/chungouoo/NDXTMC/releases/download/data-2026-11-16-44038926561-1/LATEST_Morning_Report_Data_Pack.zip)

数据发布于北京时间 **15:28:32**，对应 **2026-11-16 美股 EOD**。运行使用公开仓库的标准 ubuntu-24.04 运行器，没有付费 API、Actions Artifact 或 Actions 缓存。

## Nasdaq 兼容性复核（15:28）

- [短历史诊断](https://github.com/chungouoo/NDXTMC/actions/runs/44037939605) 返回 HTTP 200，取得 AAPL 两行数据。
- [完整历史对照诊断](https://github.com/chungouoo/NDXTMC/actions/runs/44038225895) 在同一运行器上，普通 GET 和带原有 Content-Type 的 GET 均返回 HTTP 200、470 行 AAPL 历史。
- 正式 47 股生产任务使用正常匿名 HTTP 会话、区分 GET/POST 后，Nasdaq 首次取数仍返回 HTTP 403，随后 Yahoo 完整接管并发布成功。
- 因而当前未能稳定消除 Nasdaq 的 403。具体是否与出口、会话或服务端规则有关未核实，不能断言整个 GitHub 云端永久被封禁，也不能把恢复归因于某个请求头。
- 保留 Nasdaq 优先、失败使用 Yahoo 的生产策略；新增“Nasdaq 部分个股失败时整组重新从 Yahoo 获取”的保护。所有个股仍来自同一供应商，避免混源广度。

## 原因与修复

Nasdaq 的指数 HistoryData 和成分名单 WeightingData 均可在 GitHub 云端使用，但个股 api.nasdaq.com 对云端请求返回 HTTP 403。原版因此 47/47 个股均失败。补齐常规请求信息后，单次连通性探测仍被拒绝。

修复后先探测一只股票；Nasdaq 拒绝访问就停止请求其余股票，全部成分股统一使用 Yahoo Finance chart 公开历史接口。**指数与名单仍来自 Nasdaq 官方**。逐行 Source、JSON 的 source_policy、manifest、质量报告、Release 都记录个股实际供应商与降级原因。两家供应商都不可用时阻止发布；不绕过权限或限流，不将旧包伪装成新包。

## 验证结果

| 检查 | 结果 |
|---|---|
| 自动测试 | 17 项通过（GitHub 云端实测） |
| Nasdaq 指数与当期成分名单 | 通过 |
| 个股供应商 | 全部 47 只统一为 Yahoo Finance chart Close |
| 数据日期 | 2026-11-16，47/47 股完整覆盖 |
| 个股历史 | 22,004 行 |
| 指数历史条数 | NDXTMC 1,170；NDXT 470；NDX 470 |
| 结果 ZIP | 13 文件，766,932 字节 |
| 独立 ZIP 验算 | PASS_WITH_SOURCE_CAVEAT，0 错误 |
| manifest | 12 个文件大小与 SHA256 全部通过 |
| Release 发布 | 成功，包含数据包、SHA256、源响应审计包、validation.json |
| 下载复核 | 从公开 Release 实际下载成功；大小与 SHA256 一致，再次独立验算通过 |

本次发布 ZIP 的 SHA256：

4c228e129711a3e7ff3db04a4c4b1b1a0c0fd9f69c63151a7da0dfc23a63af33

## 数据口径与局限

- Yahoo 使用 indicators.quote.close，不使用 adjclose；不在本地修复或调整价格。供应商仍可能回溯调整历史分拆口径，复权状态警告保留。
- 两源历史价格不能视为逐行等同。早先对本地取得的 Nasdaq 历史与 15:02 发布包比较 22,004 行收盘价，最大历史绝对差约 3.3593 美元（TRI，2025-07-14）；2026-11-16 最大绝对差约 0.00504 美元。差异的具体企业行动或供应商原因未核实，未用比例强行修正。
- 本次 MA40 广度两源均为 10/47；这不是所有日期或所有指标永远一致的承诺。整组个股必须使用同一来源，不拼接供应商。
- 指数重复核查属于同源一致性检查，没有声称获得另一供应商的独立认证。
- 历史时点成分回测、159509 权重映射未完成；DAILY_READY 仅代表当期核心数据通过所列检查。
- GitHub App 支持手动运行。真实 iPhone 的下载交互未在部署环境实测；公开 Release 的 ZIP 已实际下载并验证。

免费政策与私有仓库说明见 [免费说明](FREE_USAGE.md)。
