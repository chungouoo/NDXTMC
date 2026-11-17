# NDXTMC · iPhone 晨报数据包

在 iPhone 上点运行，由 GitHub 云端抓取 Nasdaq 数据、计算指标并打包。电脑可以关机；不用安装 iOS Python App，也不用填写 API Key。

**[▶ 运行数据更新](https://github.com/chungouoo/NDXTMC/actions/workflows/generate.yml)**　 · 　**[↓ 下载最近成功的 ZIP](https://github.com/chungouoo/NDXTMC/releases/latest/download/LATEST_Morning_Report_Data_Pack.zip)**　 · 　**[查看数据日期与校验结果](https://github.com/chungouoo/NDXTMC/releases/latest)**

> 部署时仓库为 **Private**。为满足“严格免费”，工作流设置了运行前保护：**仓库为 Private 时，任务直接跳过，不分配运行器**。公开仓库的标准 Linux 运行器免费。公开后可手动运行；`public` 事件也会触发首次生成。详见 [免费说明](docs/FREE_USAGE.md)。

## iPhone 怎么用

1. 用 Safari 登录自己的 GitHub，打开上方「运行数据更新」。
2. 点 **Run workflow**，分支选 **main**，再点绿色 **Run workflow**。若手机页面隐藏了按钮，可用 Safari 的「请求桌面网站」。
3. 等待运行完成、显示绿色成功，再打开「查看数据日期与校验结果」。
4. 确认 **美股交易日**，下载 `LATEST_Morning_Report_Data_Pack.zip`，在 Safari 下载列表或「文件」App 中找到它，然后上传给 ChatGPT 生成晨报。

成功运行耗时取决于 Nasdaq 响应；工作流最多运行 15 分钟。页面关闭后云端会继续。**下载链接指向最近一次成功结果**，失败时不会伪装成更新成功，请始终核对 Release 中的交易日。

## 生成内容

| 文件 | 内容 |
|---|---|
| `01_index_latest.csv` | NDXTMC、NDXT、NDX 的收盘、前收和 1/5/20 日涨跌 |
| `02_ndxtmc_indicators.csv` | Wilder RSI14、MA20/40/50/200、收盘高点回撤 |
| `03_index_relative_performance.csv` | 三条指数相对表现与 NDXTMC/NDXT 差值 |
| `04_breadth_latest.csv` | 当期成分股均线广度、RSI 分布、涨跌家数与有效覆盖率 |
| `05_quality_report.md` | 质量检查、警告和数据局限 |
| `06_manifest.json` | 交易日、行数、文件大小与 SHA256 |
| `07_morning_report_input.json` | 可直接供晨报读取的结构化输入 |
| `NDXTMC_index_history.csv` | 2022-03-21 起的官方指数 EOD |
| `NDXT_index_history.csv` / `NDX_index_history.csv` | 2025-01-02 起的官方指数 EOD |
| `NDXTMC_47stocks_history.csv` | 当期成分股历史日线；保留旧文件名兼容晨报 |
| `failed_tickers.csv` / `anomaly_check.csv` | 下载失败名单、极端涨跌检查 |

每次成功还提供 `Nasdaq_Source_Audit.zip`（原始响应及独立验算结果）与 ZIP 的 `.sha256` 文件。

## 数据规则

- 每次从 Nasdaq 官方 `WeightingData` 重新取名单，核对证券身份与指数返回的数量，不把成分数永久写死为 47。名单变更会显示实际数量。
- 自动识别纽约交易日、夏令时、节假日、特殊休市及提前收市。目标是**最近一个已经收盘的美股交易日**；官方 EOD 尚未发布则报错，不悄悄使用更早一天。
- 指数抓完整历史，再重复核对最近两天的官方 EOD；对已核验的 2026-11-13 指数快照检查是否出现冲突。重复请求仍是同一官方来源，不等于独立供应商验证。
- 个股核对 symbol、总条数、重复、连续性、当期日期、OHLC 和数值有效性。最多 4 个并发；只对临时网络/5xx 重试。遇到权限或限流错误会失败，不绕过限制。
- RSI14 使用 Wilder 平滑。上市时间不足的股票不补造历史，广度同时给有效分母与覆盖率。
- 绝对单日涨跌达到 40% 会登记。前次已接受数据包中 CRWV 2025-04-01、NBIS 2025-09-09 的对应原始价格保留并警告；新异常或已接受异常的价格变化会阻止发布，等待核查。
- 打包后由另一个 ZIP 读取器独立复算 RSI、均线、收益率、广度，并验证 12 个文件的哈希。
- `DAILY_READY` 只代表当前核心数据通过检查。**复权状态未知、历史时点成分回测及 159509 权重映射未完成**；不输出交易建议。

## 本地运行与复核

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m scripts.morning_pack --output output
python -m scripts.validate_pack output/LATEST_Morning_Report_Data_Pack.zip
```

仅用于开发的历史回放：`--fixtures 原始JSON目录 --as-of YYYY-MM-DD`。回放文件命名为 `HISTORICAL_REPLAY_Data_Pack.zip`，`DAILY_READY=false`，不能通过实时发布校验。

本项目由前期 Nasdaq 47 股下载器、官方历史端点与晨报数据包格式迁移而来。当前不配置定时运行；每次由你在手机手动触发。
