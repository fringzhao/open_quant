# Tushare 免费替代方案与改造计划

## 一、背景与目标

当前 `src/quant_infra/get_data.py` 使用 Tushare 获取股票及行情数据，并依赖环境变量 `TS_TOKEN`。由于没有 Tushare 账号，本次计划使用无需账号的免费数据方案替代相关接口。

改造目标：

- 移除对 Tushare、`TS_TOKEN` 和 Tushare 积分权限的依赖。
- 免费获取沪深京 A 股日行情、指数行情、指数成分、股票列表及必要的估值数据。
- 尽量保持现有业务函数、DuckDB 表名和字段名不变。
- 保证 `factor_calc.py`、`factor_analyze.py` 和 `trade.py` 等下游模块继续运行。
- 加入重试、限速、缓存、增量更新、去重及数据校验机制。

## 二、推荐方案

推荐采用 **AKShare + 东方财富/中证指数公开数据源** 替代 Tushare。

选择理由：

- 免费，不需要注册账号或配置 Token。
- 覆盖沪深京 A 股日线、指数行情、股票列表、指数成分、估值及财务数据。
- 返回 Pandas DataFrame，与当前代码的衔接成本较低。
- `stock_zh_a_hist` 支持指定股票、日期范围和复权方式。
- `index_zh_a_hist` 支持指数历史行情。
- `index_stock_cons_csindex` 可以从中证指数来源获取指数成分股。

相关资料：

- [AKShare 股票数据文档](https://akshare.akfamily.xyz/data/stock/stock.html)
- [AKShare 指数数据文档](https://akshare.akfamily.xyz/data/index/index.html)
- [AKShare 更新记录](https://akshare.akfamily.xyz/changelog.html)

### 方案限制

AKShare 是对东方财富、新浪、网易、中证指数等公开数据源的封装。它免费且覆盖面广，但没有商业 SLA；如果上游网页或接口发生变化，部分接口可能短暂失效。

因此，改造时需要同时加入：

- 网络请求重试和超时。
- 合理的请求限速和并发控制。
- 本地缓存及断点续传。
- 返回字段、日期范围和数据覆盖率校验。
- 将来切换备用数据源的适配层。

BaoStock 也可以免费使用，且日 K 线接口相对稳定，但它不能完整提供项目所需的 `daily_basic.total_mv` 等每日指标，指数及行业覆盖也较弱。因此，不建议将 BaoStock 作为本项目唯一数据源，可考虑将其作为日 K 线备用数据源。

## 三、Tushare 接口替换关系

| 当前 Tushare 接口 | 建议替代接口或数据源 | 兼容处理 |
|---|---|---|
| `pro.daily()` | `ak.stock_zh_a_hist()` | 将中文字段转换为 `ts_code/trade_date/open/high/low/close/vol/amount/pct_chg` |
| `pro.daily_basic()` | AKShare 网易历史行情、东方财富历史估值接口组合 | 生成 `turnover_rate/pb/pe/total_mv/circ_mv` 等字段 |
| `pro.index_daily()` | `ak.index_zh_a_hist()` | 补算 `pre_close/change/pct_chg` |
| `pro.index_weight()` | `ak.index_stock_cons_csindex()` | 将“成分券代码”转换为 `con_code` |
| `pro.trade_cal()` | 优先根据指数实际行情日期生成，AKShare 日历作为辅助 | 统一输出 `cal_date` |
| `pro.stock_basic()` | `ak.stock_info_a_code_name()` 等股票列表接口 | 补齐 `ts_code/symbol/name/exchange/list_status` |
| `pro.fina_indicator()` | `stock_financial_analysis_indicator_em()` 等财务接口 | 建立字段映射，无法等价的字段填空或明确删除 |
| `pro.index_member_all()` | AKShare 行业板块及成分接口 | 注意免费源的行业分类不一定等同申万行业 |

## 四、关键架构调整

当前 `get_data_by_date()` 的下载方式是“按交易日获取当日全市场数据”，而 AKShare 的主要历史行情接口是“按股票获取指定日期区间”。因此不能只替换一行接口调用，需要改变下载方向：

```text
当前：交易日 → 当日全市场股票
改造：股票列表 → 每只股票缺失的日期区间
```

建议在数据获取模块中增加统一的数据源适配层：

```text
get_data.py
  ├─ 对外业务函数（保持现有函数名）
  ├─ AkShareDataProvider
  ├─ 字段标准化函数
  ├─ 股票和指数代码转换函数
  └─ 重试、限速、增量下载及校验逻辑
```

以下公开函数尽量保持名称和调用方式不变：

- `get_stock_data_by_date()`
- `get_daily_basic()`
- `get_index_data()`
- `get_ins()`
- `get_trade()`
- `get_basic()`
- `get_financial()`
- `get_industry()`

这样可以避免大范围修改下游模块。

## 五、分阶段修改计划

### 第一阶段：确定兼容数据契约

为现有 DuckDB 表固定最小必要字段。

#### `stock_bar`

```text
ts_code, trade_date, open, high, low, close, vol, amount, pct_chg
```

#### `daily_basic`

```text
ts_code, trade_date, total_mv, pb
```

可根据数据源覆盖情况继续提供：

```text
turnover_rate, pe, pe_ttm, ps, ps_ttm, circ_mv
```

#### `index_data`

```text
ts_code, trade_date, open, high, low, close, vol, amount, pct_chg
```

#### `stock_basic`

```text
ts_code, symbol, name
```

其中 `daily_basic.total_mv` 和 `daily_basic.pb` 是必须字段，因为 `factor_calc.py` 使用它们计算 SMB 和 HML。

### 第二阶段：引入 AKShare 和代码转换

- 在 `pyproject.toml` 中删除 `tushare` 依赖并增加 `akshare`。
- 从 `get_data.py` 删除 `TS_TOKEN`、`_get_pro_client()` 及相关错误提示。
- 建立统一的证券代码转换函数。

代码转换示例：

```text
600000       → 600000.SH
000001       → 000001.SZ
430xxx/8xxxx → 北京市场代码后缀
000300.SH    → AKShare 指数代码 000300
```

所有外部接口返回的数据必须先经过字段标准化和类型转换，再写入 DuckDB。

### 第三阶段：改造股票行情下载

- 首先获取全部 A 股股票列表。
- 查询每只股票在 `stock_bar` 中已有的最大交易日期。
- 按股票下载缺失日期区间，不再逐交易日请求全市场数据。
- 并发数建议默认为 4～8，不继续使用无上限的 `n_jobs=-1`。
- 每完成一批股票就写入数据库并保存 checkpoint，避免任务中断后从头开始。
- 写库前按照 `ts_code + trade_date` 去重。
- 保留当前涨跌幅异常过滤规则，但将阈值配置化。
- 对失败股票保存清单，并在主任务结束后重试。

日线建议先使用不复权价格，与当前 Tushare `daily()` 的语义最接近。如果未来需要前复权或后复权数据，应增加独立字段或数据表，不能直接覆盖原始行情。

### 第四阶段：补齐每日估值数据

每日估值是本次改造风险最高的部分。

建议实施方式：

1. 使用 AKShare 的网易历史行情接口获取历史总市值和流通市值。
2. 使用可提供历史估值的数据接口取得 PB、PE 等指标。
3. 按照 `ts_code + trade_date` 合并为 `daily_basic`。
4. 对关键字段进行缺失率和覆盖率检查。
5. `total_mv` 或 `pb` 缺失超过约定阈值时，本次更新应报告失败，不能静默完成。
6. 第一版优先保证项目实际使用的 `total_mv` 和 `pb`，其他 Tushare 字段后续按业务需求增加。

需要统一并记录市值单位。Tushare 的市值字段通常以万元为单位，而其他公开接口可能以元为单位；写库前必须转换成项目约定单位。

### 第五阶段：替换指数及基础信息接口

#### 指数行情

- 使用 `ak.index_zh_a_hist()`。
- 将日期和行情字段转换为当前 `index_data` 表结构。
- 根据前一日收盘价补算 `pre_close`、`change` 和 `pct_chg`。

#### 指数成分股

- 使用 `ak.index_stock_cons_csindex()` 获取中证系列指数最新成分股。
- 将成分券代码转换为 `000001.SZ` 或 `600000.SH` 格式。
- 保持输出文件 `Data/Metadata/{index_code}_ins.csv` 不变。

需要注意：免费接口通常提供最新或当前成分股，不一定能完整还原历史任意日期的指数成分。当前代码本身也主要使用近期成分，因此第一版可保持这一语义，但应在文档中注明，避免产生生存者偏差误解。

#### 股票基本信息

- 使用 A 股代码名称列表接口获取股票代码和名称。
- 根据代码规则生成交易所和 Tushare 风格的 `ts_code`。
- 如果免费接口不能提供完整上市状态和上市日期，应允许这些非核心字段为空。

#### 交易日历

- 不完全依赖可能只有固定日期范围的静态日历接口。
- 优先从上证指数等主要指数的实际历史行情日期生成交易日历。
- 每个交易日收盘后再更新当天数据，时间阈值设为可配置项。
- 输出仍保存为 `Data/Metadata/trade_day.csv`，字段保持 `cal_date`。

### 第六阶段：财务指标和行业数据迁移

这部分与核心行情分开实施，避免阻塞行情系统上线。

#### 财务指标

- 使用 AKShare 东方财富财务分析指标接口。
- 逐一建立 Tushare `fina_indicator` 字段到 AKShare 字段的映射表。
- 对无法一一对应的字段明确标记，不做含义不一致的强行映射。
- 保留半年更新和本地更新时间记录机制。

#### 行业数据

- 明确项目是否必须使用申万行业分类。
- 如果仅用于一般行业分组，可采用东方财富行业板块及成分。
- 如果必须保持申万口径，需要寻找明确提供申万分类的免费来源，不能直接把东方财富行业命名为 `sw_industry`。
- 必要时将表名改为通用的 `stock_industry`，并增加 `source`、`industry_standard` 和 `level` 字段。

### 第七阶段：可靠性建设

统一实现以下机制：

- 请求超时。
- 指数退避重试。
- 随机请求间隔，避免集中访问。
- 可配置并发数。
- 失败股票及失败日期日志。
- 分批写库和断点续传。
- 接口返回空表时区分“确实无数据”和“请求异常”。
- 写库前主键去重。
- 数据源名称及抓取时间记录。
- 关键接口失败时给出清晰错误信息。

建议为行情表建立逻辑唯一键：

```text
stock_bar:   ts_code + trade_date
daily_basic: ts_code + trade_date
index_data:  ts_code + trade_date
```

## 六、测试和验收计划

### 单元测试

- 沪市、深市、创业板、科创板和北交所代码转换。
- 中文字段到现有英文数据库字段的映射。
- 日期转换为 `YYYYMMDD`。
- 数值字段和空值转换。
- 成交量及市值单位转换。
- `pct_chg` 补算。
- 重复数据去重。

### 集成测试

- 选取沪深北各一只股票下载指定区间行情。
- 对比公开行情页面的开高低收、涨跌幅、成交量和成交额。
- 验证停牌日、新股上市日、退市股票及除权日。
- 连续运行两次更新，确认不会产生重复记录。
- 模拟任务中断，确认可以从 checkpoint 继续。
- 验证指数行情和指数成分文件可以被现有回测代码读取。

### 数据质量测试

- 检查每个交易日的股票覆盖数量。
- 检查 `stock_bar` 和 `daily_basic` 的日期交集及股票交集。
- 检查 `open/high/low/close` 的价格关系。
- 检查成交量、成交额和市值是否出现异常单位变化。
- 检查 `total_mv`、`pb` 的缺失比例。
- 抽样与第二公开数据源交叉核验。

### 下游回归测试

- 运行定价因子计算，确认 SMB、HML、MKT、UMD 可以生成。
- 运行因子评估，确认基准指数收益字段完整。
- 运行交易模拟，确认股票名称和收盘价过滤正常。
- 对比迁移前后的相同区间结果，对差异给出数据源或复权口径解释。

## 七、建议实施顺序

建议拆成三个可以独立验收的改动：

1. **行情最小闭环**：股票列表、交易日历、股票日线、指数行情和指数成分。
2. **因子数据闭环**：实现 `daily_basic.total_mv/pb`，验证 SMB 和 HML 计算。
3. **扩展数据迁移**：财务指标及行业分类。

第一部分完成后，股票行情、收益计算和基础回测即可运行。第二部分完成后，当前定价因子流程恢复完整。财务及行业接口可在第三部分继续适配，不阻塞核心行情功能。

## 八、最终建议

本次改造不应只把 `tushare` 导入替换为 `akshare`，而应建立一个小型数据源适配层：

```text
业务函数 → 统一数据契约 → AKShare Provider → 免费公开数据源
```

业务模块只依赖统一的数据表及字段，不直接依赖某个第三方接口。这样即使未来某个免费接口失效，也只需要更换 Provider 或单个接口映射，不必再次修改因子计算、回测和交易模块。

