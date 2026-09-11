# Open Quant DuckDB 数据库表说明

## 1. 文档说明

本文档依据项目根目录 `data.db` 在 2026-08-20 的数据库快照生成，描述当前实际存在的业务表、字段含义、数据关系和使用注意事项。

- 数据库：DuckDB
- 数据库文件：项目根目录 `data.db`
- Schema：`main`
- 日期字段通常采用 `YYYYMMDD` 格式
- 股票代码通常采用 `证券代码.交易所` 格式，例如 `000001.SZ`、`600519.SH`
- 当前所有业务表均未定义主键、唯一约束、外键和索引

## 2. 表总览

| 表名 | 当前行数 | 字段数 | 主要用途 | 典型业务键 |
| --- | ---: | ---: | --- | --- |
| `stock_basic` | 5,210 | 17 | A 股股票基础信息和上市状态 | `ts_code` |
| `stock_bar` | 1,823 | 11 | 个股日频 OHLCV 行情 | `ts_code + trade_date` |
| `daily_basic` | 1,824 | 18 | 个股每日估值、市值及股本指标 | `ts_code + trade_date` |
| `index_data` | 2,581 | 11 | 指数日频 OHLCV 行情 | `ts_code + trade_date` |
| `fina_indicator` | 103 | 25 | 财务报告期指标 | `ts_code + end_date + report_type` |
| `sw_industry` | 1 | 10 | 股票申万行业分类及变更记录 | `ts_code + change_date` |
| `pricing_factors` | 132 | 5 | 按交易日计算的市场定价因子 | `trade_date` |

行数是生成文档时的快照值，会随着数据同步和因子计算而变化。

## 3. 核心数据关系

```text
stock_basic
    │ ts_code
    ├────────────── stock_bar
    │                   │ ts_code + trade_date
    ├────────────── daily_basic
    │                   │
    │                   └── 计算 MKT / SMB / HML / UMD
    │                                  │ trade_date
    ├────────────── fina_indicator     └── pricing_factors
    │
    └────────────── sw_industry

index_data：独立保存指数行情，可作为市场基准或回测基准。
```

数据库中没有声明物理外键，上述关系由业务代码和字段值维护。

## 4. `stock_basic`：股票基础信息表

保存 A 股证券代码、名称、交易所、上市板块和上市状态等低频信息。AKShare 版本由 `get_basic()` 获取，并采用覆盖方式更新。

| 字段 | DuckDB 类型 | 含义 |
| --- | --- | --- |
| `ts_code` | `VARCHAR` | 股票代码，含交易所后缀 |
| `symbol` | `VARCHAR` | 六位股票代码 |
| `name` | `VARCHAR` | 股票简称 |
| `area` | `INTEGER` | 所属地区；当前数据全空导致类型被推断为整数 |
| `industry` | `INTEGER` | 所属行业；当前数据全空导致类型被推断为整数 |
| `fullname` | `INTEGER` | 公司中文全称；当前数据全空导致类型被推断为整数 |
| `enname` | `INTEGER` | 公司英文全称；当前数据全空导致类型被推断为整数 |
| `cnspell` | `INTEGER` | 股票简称拼音；当前数据全空导致类型被推断为整数 |
| `market` | `VARCHAR` | 上市板块，例如主板、创业板、科创板 |
| `exchange` | `VARCHAR` | 交易所代码，如 `SH`、`SZ`、`BJ` |
| `curr_type` | `VARCHAR` | 交易货币代码 |
| `list_status` | `VARCHAR` | 上市状态，如 `L` 表示上市、`D` 表示退市 |
| `list_date` | `VARCHAR` | 上市日期，格式 `YYYYMMDD` |
| `delist_date` | `INTEGER` | 退市日期；当前数据全空导致类型被推断为整数 |
| `is_hs` | `INTEGER` | 是否为沪深港通标的；当前数据全空导致类型被推断为整数 |
| `act_name` | `INTEGER` | 实际控制人名称；当前数据全空导致类型被推断为整数 |
| `act_ent_type` | `INTEGER` | 实际控制人企业性质；当前数据全空导致类型被推断为整数 |

注意：`area`、`industry`、`fullname` 等语义上应为字符串的字段目前显示为 `INTEGER`，这是使用全空 Pandas 列创建表时的类型推断结果，不代表业务类型。以后写入非空字符串前应先将这些列迁移为 `VARCHAR`。

## 5. `stock_bar`：股票日行情表

保存个股每日未复权行情，是收益率、定价因子、特质波动率和交易回测的基础数据。

| 字段 | DuckDB 类型 | 含义 |
| --- | --- | --- |
| `ts_code` | `VARCHAR` | 股票代码，含交易所后缀 |
| `trade_date` | `VARCHAR` | 交易日期，格式 `YYYYMMDD` |
| `close` | `DOUBLE` | 收盘价，单位：元 |
| `open` | `DOUBLE` | 开盘价，单位：元 |
| `high` | `DOUBLE` | 最高价，单位：元 |
| `low` | `DOUBLE` | 最低价，单位：元 |
| `pre_close` | `DOUBLE` | 前一交易日收盘价，单位：元 |
| `change` | `DOUBLE` | 涨跌额，单位：元 |
| `pct_chg` | `DOUBLE` | 涨跌幅，单位：百分比 |
| `vol` | `DOUBLE` | 成交量，单位：手 |
| `amount` | `DOUBLE` | 成交金额，单位：元 |

建议将 `(ts_code, trade_date)` 视为业务唯一键。当前写入逻辑没有数据库唯一约束，重复同步后应检查重复记录。

## 6. `daily_basic`：每日估值指标表

保存个股每日估值、股本和市值数据。`compute_pricing_factors()` 会按 `ts_code + trade_date` 与 `stock_bar` 关联，其中 `total_mv`、`pb` 是 SMB、HML 因子计算的核心字段。

| 字段 | DuckDB 类型 | 含义 |
| --- | --- | --- |
| `ts_code` | `VARCHAR` | 股票代码，含交易所后缀 |
| `trade_date` | `VARCHAR` | 交易日期，格式 `YYYYMMDD` |
| `close` | `DOUBLE` | 当日收盘价，单位：元 |
| `turnover_rate` | `DOUBLE` | 换手率，单位：百分比 |
| `turnover_rate_f` | `DOUBLE` | 自由流通股换手率，单位：百分比 |
| `volume_ratio` | `DOUBLE` | 量比 |
| `pe` | `DOUBLE` | 静态市盈率 |
| `pe_ttm` | `DOUBLE` | 滚动市盈率（TTM） |
| `pb` | `DOUBLE` | 市净率 |
| `ps` | `DOUBLE` | 静态市销率 |
| `ps_ttm` | `DOUBLE` | 滚动市销率（TTM） |
| `dv_ratio` | `DOUBLE` | 股息率，单位：百分比 |
| `dv_ttm` | `DOUBLE` | 滚动股息率（TTM），单位：百分比 |
| `total_share` | `DOUBLE` | 总股本，单位：万股 |
| `float_share` | `DOUBLE` | 流通股本，单位：万股 |
| `free_share` | `DOUBLE` | 自由流通股本，单位：万股 |
| `total_mv` | `DOUBLE` | 总市值，单位：万元 |
| `circ_mv` | `DOUBLE` | 流通市值，单位：万元 |

AKShare 免费接口未提供的字段会保留为 `NULL`。建议将 `(ts_code, trade_date)` 视为业务唯一键。

## 7. `index_data`：指数日行情表

保存指数每日行情，可用于市场走势分析、回测基准和指数收益比较。

| 字段 | DuckDB 类型 | 含义 |
| --- | --- | --- |
| `ts_code` | `VARCHAR` | 指数代码，含交易所后缀 |
| `trade_date` | `VARCHAR` | 交易日期，格式 `YYYYMMDD` |
| `close` | `DOUBLE` | 收盘点位 |
| `open` | `DOUBLE` | 开盘点位 |
| `high` | `DOUBLE` | 最高点位 |
| `low` | `DOUBLE` | 最低点位 |
| `pre_close` | `DOUBLE` | 前一交易日收盘点位 |
| `change` | `DOUBLE` | 涨跌点数 |
| `pct_chg` | `DOUBLE` | 涨跌幅，单位：百分比 |
| `vol` | `BIGINT` | 成交量，单位：手 |
| `amount` | `DOUBLE` | 成交金额，单位：元 |

建议将 `(ts_code, trade_date)` 视为业务唯一键。

## 8. `fina_indicator`：财务指标表

保存上市公司按报告期披露的盈利能力、偿债能力和营运能力指标。该表是低频数据，一只股票通常对应多个报告期。

| 字段 | DuckDB 类型 | 含义 |
| --- | --- | --- |
| `ts_code` | `VARCHAR` | 股票代码，含交易所后缀 |
| `ann_date` | `VARCHAR` | 公告日期，格式 `YYYYMMDD` |
| `end_date` | `VARCHAR` | 报告期截止日期，格式 `YYYYMMDD` |
| `report_type` | `VARCHAR` | 财务报告类型 |
| `currency` | `VARCHAR` | 报告币种 |
| `eps` | `DOUBLE` | 基本每股收益，单位：元/股 |
| `dt_eps` | `DOUBLE` | 扣除非经常性损益后的每股收益，单位：元/股 |
| `bps` | `DOUBLE` | 每股净资产，单位：元/股 |
| `ocfps` | `DOUBLE` | 每股经营活动现金流，单位：元/股 |
| `total_revenue` | `DOUBLE` | 营业总收入，单位：元 |
| `parent_netprofit` | `DOUBLE` | 归属于母公司股东的净利润，单位：元 |
| `profit_dedt` | `DOUBLE` | 扣除非经常性损益后的净利润，单位：元 |
| `roe` | `DOUBLE` | 加权净资产收益率，单位：百分比 |
| `roe_dt` | `DOUBLE` | 扣非加权净资产收益率，单位：百分比 |
| `roa` | `DOUBLE` | 总资产净利率，单位：百分比 |
| `netprofit_margin` | `DOUBLE` | 销售净利率，单位：百分比 |
| `grossprofit_margin` | `DOUBLE` | 销售毛利率，单位：百分比 |
| `current_ratio` | `DOUBLE` | 流动比率 |
| `quick_ratio` | `DOUBLE` | 速动比率 |
| `cash_ratio` | `DOUBLE` | 现金流量比率 |
| `debt_to_assets` | `DOUBLE` | 资产负债率，单位：百分比 |
| `assets_turn` | `DOUBLE` | 总资产周转率，单位：次 |
| `inv_turn` | `DOUBLE` | 存货周转率，单位：次 |
| `ar_turn` | `DOUBLE` | 应收账款周转率，单位：次 |
| `roic` | `DOUBLE` | 投入资本回报率，单位：百分比 |

同一报告期可能存在不同报告类型或修订版本，使用时应结合 `ann_date` 和 `report_type` 去重，并避免使用公告日之后才公开的数据进行历史回测。

## 9. `sw_industry`：申万行业分类表

保存股票的申银万国行业分类和行业归属变更日期。

| 字段 | DuckDB 类型 | 含义 |
| --- | --- | --- |
| `ts_code` | `VARCHAR` | 股票代码，含交易所后缀 |
| `name` | `VARCHAR` | 股票简称 |
| `change_date` | `VARCHAR` | 行业归属变更日期，格式 `YYYYMMDD` |
| `industry_standard_code` | `VARCHAR` | 行业分类标准编码 |
| `industry_standard` | `VARCHAR` | 行业分类标准名称 |
| `industry_code` | `VARCHAR` | 行业编码 |
| `industry_section` | `VARCHAR` | 行业门类 |
| `industry_subclass` | `VARCHAR` | 行业次类 |
| `industry_major` | `VARCHAR` | 行业大类 |
| `industry_middle` | `VARCHAR` | 行业中类 |

行业归属可能随时间变化。历史分析应选择不晚于目标交易日的最新一条行业记录，避免直接使用当前行业造成未来数据泄漏。

## 10. `pricing_factors`：定价因子表

由 `factor_calc.compute_pricing_factors()` 根据 `stock_bar` 和 `daily_basic` 计算。计算使用上一个月的股票月收益、市值和市净率对股票分组，再形成当日因子收益。

| 字段 | DuckDB 类型 | 含义 |
| --- | --- | --- |
| `trade_date` | `BIGINT` | 交易日期，数值形式的 `YYYYMMDD` |
| `MKT` | `DOUBLE` | 市场因子：当日样本股票平均收益率 |
| `SMB` | `DOUBLE` | 规模因子：小市值组合收益减大市值组合收益 |
| `HML` | `DOUBLE` | 价值因子：低市净率组合收益减高市净率组合收益 |
| `UMD` | `DOUBLE` | 动量因子：上月高收益组合收益减上月低收益组合收益 |

当前因子计算要求每个交易日至少有 9 只有效股票。少于 9 只时，该交易日会返回空结果。正式研究应使用更完整的股票池，最低数量只适合验证程序流程。

## 11. 数据质量与结构注意事项

### 11.1 日期类型不统一

`stock_bar`、`daily_basic`、`index_data` 使用 `VARCHAR` 日期，而 `pricing_factors.trade_date` 当前为 `BIGINT`。DuckDB 在某些查询中会进行隐式转换，但建议后续统一为 `VARCHAR(YYYYMMDD)` 或 `DATE`，减少关联和比较时的类型风险。

### 11.2 缺少数据库约束

当前表没有主键或唯一约束，`append` 写入依赖业务代码保证不重复。建议定期检查：

```sql
SELECT ts_code, trade_date, COUNT(*) AS row_count
FROM stock_bar
GROUP BY ts_code, trade_date
HAVING COUNT(*) > 1;
```

`daily_basic`、`index_data` 也可使用相同方式检查。

### 11.3 `CREATE TABLE AS` 的类型推断

`db_utils.write_to_db()` 首次建表使用 DataFrame 自动推断字段类型。全空列可能被推断为不符合业务含义的类型，当前 `stock_basic` 已出现这种情况。建议长期方案是维护显式 DDL，再执行 `INSERT`，不要依赖首批数据推断正式表结构。

### 11.4 因子样本范围

`pricing_factors` 的质量取决于 `stock_bar` 与 `daily_basic` 在相同交易日的股票交集。可用以下 SQL 检查每日有效样本数：

```sql
SELECT
    b.trade_date,
    COUNT(DISTINCT b.ts_code) AS usable_stocks
FROM stock_bar AS b
INNER JOIN daily_basic AS d
    ON b.ts_code = d.ts_code
   AND b.trade_date = d.trade_date
WHERE b.pct_chg IS NOT NULL
  AND d.total_mv IS NOT NULL
  AND d.pb IS NOT NULL
GROUP BY b.trade_date
ORDER BY b.trade_date;
```

## 12. 常用查询示例

查询指定股票行情与每日估值：

```sql
SELECT
    b.ts_code,
    b.trade_date,
    b.close,
    b.pct_chg,
    d.pe_ttm,
    d.pb,
    d.total_mv
FROM stock_bar AS b
INNER JOIN daily_basic AS d
    ON b.ts_code = d.ts_code
   AND b.trade_date = d.trade_date
WHERE b.ts_code = '600519.SH'
ORDER BY b.trade_date;
```

查询定价因子：

```sql
SELECT trade_date, MKT, SMB, HML, UMD
FROM pricing_factors
ORDER BY trade_date;
```

查询某股票最新财务指标：

```sql
SELECT *
FROM fina_indicator
WHERE ts_code = '600519.SH'
ORDER BY end_date DESC, ann_date DESC;
```
