# NDXTMC · iPhone 晨报数据包

在 iPhone 上点运行，由 GitHub 云端抓取 Nasdaq 数据、计算指标并打包。电脑可以关机；不用安装 iOS Python App，也不用填写 API Key。

**来源说明：指数与当期名单来自 Nasdaq 官方。个股优先 Nasdaq；其接口拒绝 GitHub 云端访问时，全部成分股统一改用 Yahoo Finance 公开历史接口。包内逐行 Source、JSON、质量报告和 Release 都会标明实际来源，不将备用数据写成 Nasdaq 官方数据。**

**[▶ 运行数据更新](https://github.com/chungouoo/NDXTMC/actions/workflows/generate.yml)**　 · 　**[↓ 下载最近成功的 ZIP](https://github.com/chungouoo/NDXTMC/releases/latest/download/LATEST_Morning_Report_Data_Pack.zip)**　 · 　**[查看数据日期与校验结果](https://github.com/chungouoo/NDXTMC/releases/latest)**

> 当前配置使用公开仓库的免费标准 Linux 运行器，并保留运行前保护：**仓库为 Private 时，任务直接跳过，不分配运行器**。私有仓库本身也能运行，免费额度与账户计费限制需另行核实。详见 [免费说明](docs/FREE_USAGE.md)。

## iPhone 怎么用

1. GitHub App 中打开 `NDXTMC → Actions → iPhone 一键生成晨报数据包`。也可用手机浏览器打开上方「运行数据更新」。
2. 点 **运行工作流 / Run workflow**，分支选 **main**，再确认运行。若浏览器隐藏了按钮，可用 Safari 的「请求桌面网站」。
3. 等待运行完成、显示绿色成功，再打开「查看数据日期与校验结果」。
4. 确认 **美股交易日**，下载 `LATEST_Morning_Report_Data_Pack.zip`，在 Safari 下载列表或「文件」App 中找到它，然后上传给 ChatGPT 生成晨报。

成功运行耗时取决于数据源响应；工作流最多运行 15 分钟。页面关闭后云端会继续。**下载链接指向最近一次成功结果**，失败时不会伪装成更新成功，请始终核对 Release 中的交易日。

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

每次成功还提供 `Nasdaq_Source_Audit.zip`（保留旧文件名；包含实际使用的 Nasdaq/Yahoo 原始响应及独立验算结果）与 ZIP 的 `.sha256` 文件。

实测与当前部署状态见 [验证记录](docs/VALIDATION.md)。

## 数据规则

- 每次从 Nasdaq 官方 `WeightingData` 重新取名单，核对证券身份与指数返回的数量，不把成分数永久写死为 47。名单变更会显示实际数量。
- 自动识别纽约交易日、夏令时、节假日、特殊休市及提前收市。目标是**最近一个已经收盘的美股交易日**；官方 EOD 尚未发布则报错，不悄悄使用更早一天。
- 指数抓完整历史，再重复核对最近两天的官方 EOD；对已核验的 2026-11-13 指数快照检查是否出现冲突。重复请求仍是同一官方来源，不等于独立供应商验证。
- 个股先检查一个代码的连通性。Nasdaq 拒绝访问时停止向该接口发送其余请求，全部股票统一使用 Yahoo；两个来源都不可用则失败，不绕过限制。
- 首只股票成功但其余 Nasdaq 个股有任何失败时，丢弃整组 Nasdaq 股票数据，全部成分股重新从 Yahoo 获取，避免部分成功时仍无法出包。HTTP 会话按线程隔离并正常保留匿名响应 cookie；GET 不携带表单 Content-Type，POST 保留正确表单格式。
- 两个来源共用 symbol、日期、重复、连续性、OHLC 与数值校验。Nasdaq 检查 totalRecords；Yahoo 检查数组对齐、USD、纽约时区、日线频率及上市日起的完整性。最多 4 个并发，仅对临时网络/5xx 重试。
- Yahoo 使用 `indicators.quote.close`，不使用 `adjclose`，不在本地修复或重算价格。供应商可能回溯调整分拆数据，仍明确保留复权状态警告。整组个股不能混用两个供应商。
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

本项目由前期 Nasdaq 47 股下载器、官方历史端点与晨报数据包格式迁移而来。从 2026-11-18 起，由原有「纳指晨报」任务每天北京时间 08:00 启动完整流程：触发 GitHub 更新、等待校验和发布、读取本轮已验证数据、复算指标并补充宏观/情绪研究，最后在原晨报对话推送完整分析。单独的数据更新任务合并停用；手动运行入口保留，不依赖电脑开机。

自动交接使用 `data/latest_morning_input.json`，包含本轮 request_id、提交 SHA、run_id、Release、摘要、完整官方指数序列及独立审计结果，作为一个文件原子发布。晨报必须匹配本轮身份与北京时间日期，避免把上次 READY 文件或上次 Release 当成本次成功。详情见 [自动晨报流程](docs/AUTOMATION.md)。

GitHub App 的手动运行支持见 [官方公告](https://github.blog/changelog/2024-07-30-run-workflows-set-as-workflow_dispatch-manually/)。备用行情接口免费使用不等于可用性承诺；两家接口都失败时不会更新 Release。
