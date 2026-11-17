# 部署验证记录

验证日期：2026-11-17（北京时间）。

## 当前结论

**GitHub 云端抓取、打包、独立验算、Release 发布和实际下载均已通过。**

- [成功的 GitHub Actions 运行](https://github.com/chungouoo/NDXTMC/actions/runs/44035573665)
- [已发布的数据包](https://github.com/chungouoo/NDXTMC/releases/tag/data-2026-11-16-44035573665-1)
- [下载此版本 ZIP](https://github.com/chungouoo/NDXTMC/releases/download/data-2026-11-16-44035573665-1/LATEST_Morning_Report_Data_Pack.zip)

数据发布于北京时间 **15:02:22**，对应 **2026-11-16 美股 EOD**。运行使用公开仓库的标准 ubuntu-24.04 运行器，没有付费 API、Actions Artifact 或 Actions 缓存。

## 原因与修复

Nasdaq 的指数 HistoryData 和成分名单 WeightingData 均可在 GitHub 云端使用，但个股 api.nasdaq.com 对云端请求返回 HTTP 403。原版因此 47/47 个股均失败。补齐常规请求信息后，单次连通性探测仍被拒绝。

修复后先探测一只股票；Nasdaq 拒绝访问就停止请求其余股票，全部成分股统一使用 Yahoo Finance chart 公开历史接口。**指数与名单仍来自 Nasdaq 官方**。逐行 Source、JSON 的 source_policy、manifest、质量报告、Release 都记录个股实际供应商与降级原因。两家供应商都不可用时阻止发布；不绕过权限或限流，不将旧包伪装成新包。

## 验证结果

| 检查 | 结果 |
|---|---|
| 自动测试 | 15 项通过（GitHub 云端实测） |
| Nasdaq 指数与当期成分名单 | 通过 |
| 个股供应商 | 全部 47 只统一为 Yahoo Finance chart Close |
| 数据日期 | 2026-11-16，47/47 股完整覆盖 |
| 个股历史 | 22,004 行 |
| 指数历史条数 | NDXTMC 1,170；NDXT 470；NDX 470 |
| 结果 ZIP | 13 文件，766,930 字节 |
| 独立 ZIP 验算 | PASS_WITH_SOURCE_CAVEAT，0 错误 |
| manifest | 12 个文件大小与 SHA256 全部通过 |
| Release 发布 | 成功，包含数据包、SHA256、源响应审计包、validation.json |
| 下载复核 | 从公开 Release 实际下载成功；大小与 SHA256 一致，再次独立验算通过 |

本次发布 ZIP 的 SHA256：

c8b1dad6d5985bcd81acb7d8038cf2af27ef0b70e5886a6a3192dc0fc5bc8478

## 数据口径与局限

- Yahoo 使用 indicators.quote.close，不使用 adjclose；不在本地修复或调整价格。供应商仍可能回溯调整历史分拆口径，复权状态警告保留。
- 两源历史价格不能视为逐行等同。本次对本地取得的 Nasdaq 历史与发布包比较 22,004 行收盘价，最大历史绝对差约 3.3593 美元（TRI，2025-07-14）；2026-11-16 最大绝对差约 0.00504 美元。差异的具体企业行动或供应商原因未核实，未用比例强行修正。
- 本次 MA40 广度两源均为 10/47；这不是所有日期或所有指标永远一致的承诺。整组个股必须使用同一来源，不拼接供应商。
- 指数重复核查属于同源一致性检查，没有声称获得另一供应商的独立认证。
- 历史时点成分回测、159509 权重映射未完成；DAILY_READY 仅代表当期核心数据通过所列检查。
- GitHub App 支持手动运行。真实 iPhone 的下载交互未在部署环境实测；公开 Release 的 ZIP 已实际下载并验证。

免费政策与私有仓库说明见 [免费说明](FREE_USAGE.md)。
