# `get_data.py` 方法分析报告

## 1. 文件定位

`src/quant_infra/get_data.py` 是项目的行情与基础数据采集模块，主要负责：

1. 通过 Tushare Pro 获取股票、指数、交易日历、财务指标和行业成分数据。
2. 利用本地交易日历和 DuckDB 中的最新日期计算增量更新范围。
3. 将小型元数据保存为 CSV，将主要数据保存到 DuckDB。
4. 使用 `joblib` 并行抓取按日期或按股票拆分的数据。

其直接依赖关系为：

```text
get_data.py
├── Tushare Pro：远程数据源
├── pandas：DataFrame 清洗、合并和 CSV 读写
├── joblib + tqdm：并行调度和进度显示
├── const.py：路径、更新周期和限频常量
└── db_utils.py：DuckDB 查询与写入
```

## 2. 全局配置

### `token`

模块导入时使用 `os.getenv('TS_TOKEN')` 读取 Tushare Token。由于值在导入时就被固定，如果程序在导入模块后才修改环境变量，`token` 不会自动刷新。

### 关键常量

| 常量 | 当前值/来源 | 作用 |
|---|---|---|
| `DB_PATH` | `const.py` | DuckDB 数据库文件路径 |
| `BASIC_INFO_PATH` | `Data/Metadata` | 交易日历、指数成分和更新日志的目录 |
| `FETCH_LOG_PATH` | `Data/Metadata/fetch_log.csv` | 低频数据的最后更新日期 |
| `START_DATE` | `20160101` | 首次获取交易日历的起始日 |
| `LIMIT_SLEEP_SECONDS` | 15 秒 | 命中 Tushare 频率限制后的等待时间 |
| `BASIC_RENEW_DAYS` | 365 天 | 股票基本信息刷新周期 |
| `FINANCIAL_RENEW_DAYS` | 180 天 | 财务指标刷新周期 |
| `INDUSTRY_RENEW_DAYS` | 365 天 | 行业成分刷新周期 |

## 3. 方法详解

### 3.1 `_get_pro_client()`

**作用：** 统一创建 Tushare Pro 客户端。

- 输入：无，使用模块级 `token`。
- 输出：Tushare Pro API 客户端。
- 异常：未配置 `TS_TOKEN` 时抛出 `RuntimeError`。
- 调用者：几乎所有远程数据获取方法。

### 3.2 `get_ins(index_code)`

**作用：** 获取某指数上一个完整自然月的成分股。

- 输入：`index_code`，如 `000906.SH`。
- 日期范围：自动计算上月第一天至最后一天。
- Tushare 接口：`index_weight`。
- 持久化：去重后的 `con_code` 写入 `Data/Metadata/{index_code}_ins.csv`。
- 返回：成分股代码的 `set`；无数据时返回空集合。
- 异常：所有错误统一包装为“获取指数成分股失败”，并保留原异常链。

### 3.3 `get_trade(start_date, end_date)`

**作用：** 获取上交所指定时间段的开市日历。

- 输入：`YYYYMMDD` 格式的起止日期。
- Tushare 接口：`trade_cal(exchange='SSE', is_open='1')`。
- 持久化：覆盖写入 `Data/Metadata/trade_day.csv`。
- 返回：只包含 `cal_date` 的 DataFrame。
- 用途：为 `get_dates_todo()` 提供真实交易日集合。

### 3.4 `fetch_bar_by_single_date(date)`

**作用：** 获取单个交易日的全市场股票日线数据。

- Tushare 接口：`daily(trade_date=date)`。
- 数据清洗：删除 `abs(pct_chg) >= 35` 的记录。
- 重试：最多 4 次。命中“最多访问”时等待 15 秒，其他异常等待 2 秒。
- 节流：成功时等待 0.8 秒。
- 返回：DataFrame；4 次后仍失败则返回 `None`。

### 3.5 `fetch_basic_by_single_date(date)`

**作用：** 获取单个交易日的每日指标。

- Tushare 接口：`daily_basic(trade_date=date)`。
- 数据内容：换手率、量比、PE、PB、PS、股息率、总股本、总市值等。
- 重试：最多 4 次，限频等待 15 秒，其他错误等待 2 秒。
- 节流：成功时等待 1.5 秒。
- 返回：DataFrame 或 `None`。

### 3.6 `get_data_by_date(single_function, table_name)`

**作用：** 按交易日并行抓取数据的通用调度器。

- 输入：
  - `single_function`：接收单个日期并返回 DataFrame 的函数。
  - `table_name`：写入的 DuckDB 表名。
- 流程：
  1. 用 `get_dates_todo()` 计算待下载日期。
  2. 用 `Parallel(n_jobs=-1)` 调用所有 CPU 核心并行下载。
  3. 过滤 `None` 和空 DataFrame。
  4. 合并结果，按 `trade_date` 和 `ts_code` 降序排列。
  5. 以 `append` 模式写入 DuckDB。
- 返回：没有显式返回值。

### 3.7 `get_stock_data_by_date()`

**作用：** 获取增量股票日线数据。

该方法是一个便捷入口，等价于：

```python
get_data_by_date(fetch_bar_by_single_date, table_name='stock_bar')
```

数据最终追加到 `stock_bar` 表。

### 3.8 `get_daily_basic()`

**作用：** 获取增量每日基本指标。

该方法是一个便捷入口，等价于：

```python
get_data_by_date(fetch_basic_by_single_date, table_name='daily_basic')
```

数据最终追加到 `daily_basic` 表。

### 3.9 `get_index_data(index_code)`

**作用：** 增量更新并返回指定指数的全部本地日线数据。

- 输入：指数代码 `index_code`。
- 本地数据已是最新：直接查询 `index_data` 表并返回指定指数数据。
- 需要更新：调用 `index_daily` 一次性获取待下载日期范围，追加到 `index_data`。
- 返回：更新后从 DuckDB 重新查询的该指数全量 DataFrame。

### 3.10 `get_dates_todo(table_name, ts_code=None, start_date=START_DATE)`

**作用：** 计算某数据表还需要下载哪些交易日。这是增量更新的核心方法。

- 可用数据截止日：
  - 当前时间在 18:00 及之后，使用当天。
  - 18:00 之前，使用前一天。
  - 如果落在周末，回退到周五。
- 交易日历：
  - 从 `trade_day.csv` 读取。
  - 如果日历不存在或过期，调用 `get_trade(start_date, latest_day)` 整体刷新。
- 数据库进度：查询目标表的 `MAX(trade_date)`。传入 `ts_code` 时只查指定代码。
- 返回：大于数据库最新日期的升序交易日列表；无须更新时隐式返回 `None`。
- 异常策略：数据库锁定类 `RuntimeError` 原样抛出；其他查询异常被当作“首次运行/表不存在”。

### 3.11 `get_basic()`

**作用：** 获取股票基本信息，并使用 365 天缓存。

- 先通过 `get_last_fetch_date('stock_basic')` 检查更新日期。
- 缓存未过期：从 DuckDB 的 `stock_basic` 表返回数据。
- 缓存过期/不存在：调用 Tushare `stock_basic()`。
- 持久化：以 `replace` 模式整表替换 `stock_basic`，再更新日志。
- 返回：股票基本信息 DataFrame。

### 3.12 `fetch_finan_by_single_stock(ts_code)`

**作用：** 获取单只股票的财务指标历史数据。

- Tushare 接口：`fina_indicator(ts_code=ts_code)`。
- 重试：最多 4 次；限频时等待 15 秒，其他错误等待 1 秒。
- 节流：成功后等待 0.8 秒。
- 返回：DataFrame 或 `None`。

### 3.13 `get_last_fetch_date(table_name)`

**作用：** 查询低频数据的最后成功抓取日期。

- 数据源：`fetch_log.csv`。
- 输入：逻辑表名。
- 返回：`YYYYMMDD` 字符串；日志不存在或无对应记录时返回 `None`。

### 3.14 `set_last_fetch_date(table_name)`

**作用：** 将某逻辑表的最后抓取日期更新为当天。

- 先读取现有 `fetch_log.csv`。
- 删除相同 `table_name` 的旧记录。
- 追加新记录后覆盖写回 CSV。
- 每个表最终只保留一条日志。

### 3.15 `get_financial()`

**作用：** 全量抓取所有股票的财务指标，并使用 180 天缓存。

- 如果 `fina_indicator` 距上次抓取不足 180 天，直接返回。
- 通过 `get_basic()` 获取全部 `ts_code`。
- 使用 `Parallel(n_jobs=-1)` 按股票并行调用 `fetch_finan_by_single_stock()`。
- 合并所有非空结果。
- 以 `replace` 模式整表覆盖 `fina_indicator`。
- 只有存在有效数据并成功写库后才更新抓取日志。
- 源码注释估计全量执行约 30 分钟。

### 3.16 `get_industry()`

**作用：** 获取申万行业成分数据，并使用 365 天缓存。

- 缓存未过期：直接返回 DuckDB 中的 `sw_industry` 表。
- 缓存过期/不存在：循环调用 `index_member_all(limit=3000, offset=...)` 分页获取。
- 终止条件：某页 DataFrame 为空。
- 持久化：合并所有页后，以 `replace` 模式写入 `sw_industry`。
- 返回：行业成分 DataFrame。

### 3.17 主程序入口

直接执行 `get_data.py` 时，当前只调用：

```python
get_basic()
```

因此直接运行该文件只会刷新/读取股票基本信息，不会自动执行日线、每日指标、指数、财务和行业数据的全部更新。

## 4. 数据存储映射

| 数据 | Tushare 接口 | 存储位置 | 写入模式 |
|---|---|---|---|
| 指数成分股 | `index_weight` | `{index_code}_ins.csv` | CSV 覆盖 |
| 交易日历 | `trade_cal` | `trade_day.csv` | CSV 覆盖 |
| 股票日线 | `daily` | DuckDB `stock_bar` | 追加 |
| 每日基本指标 | `daily_basic` | DuckDB `daily_basic` | 追加 |
| 指数日线 | `index_daily` | DuckDB `index_data` | 追加 |
| 股票基本信息 | `stock_basic` | DuckDB `stock_basic` | 替换 |
| 财务指标 | `fina_indicator` | DuckDB `fina_indicator` | 替换 |
| 行业成分 | `index_member_all` | DuckDB `sw_industry` | 替换 |
| 低频抓取日志 | 本地生成 | `fetch_log.csv` | CSV 覆盖 |

## 5. 核心调用流程

### 日频数据更新

```text
get_stock_data_by_date / get_daily_basic
                  │
                  ▼
          get_data_by_date
                  │
                  ├── get_dates_todo
                  │       ├── 检查/刷新 trade_day.csv
                  │       └── 查询 DuckDB MAX(trade_date)
                  │
                  ├── 按交易日并行调用 fetch_*_by_single_date
                  └── 合并后 append 到 DuckDB
```

### 低频数据更新

```text
get_basic / get_financial / get_industry
                  │
                  ├── get_last_fetch_date
                  ├── 未过期 -> 读取本地数据或直接返回
                  └── 已过期 -> 调用 Tushare
                              ├── replace DuckDB 表
                              └── set_last_fetch_date
```

## 6. 当前实现的风险与改进建议

### 6.1 `DB_PATH` 是 Windows 绝对路径

`const.py` 当前配置为：

```python
DB_PATH = r'C:\file\private_quant\Data\data.db'
```

当项目在 macOS/Linux 运行时，这不是预期的跨平台绝对路径，可能在当前目录生成带反斜杠和冒号的异常文件名。建议改为基于 `REPO_ROOT` 的路径，或从本地配置读取。

### 6.2 Token 仍只从环境变量读取

当前所分析的源码仍是 `os.getenv('TS_TOKEN')`，未看到 `.env` 加载代码。若需本地配置文件，应在读取 `token` 前调用 `load_dotenv()`，并确保 `.env` 被 Git 忽略。

### 6.3 并行度与 Tushare 限频可能冲突

`Parallel(n_jobs=-1)` 会使用所有 CPU 核心。每个工作进程自己 `sleep`，但无法实现全局请求速率限制，因此仍可能瞬间超过 Tushare 频率上限。建议显式设置较小的 `n_jobs`，或使用共享限速器。

### 6.4 重试失败可能静默丢数据

单日/单股票方法在 4 次失败后返回 `None`，上层只是过滤该结果，不会汇总失败日期或股票。如果同一批次的其他数据成功写库，后续增量判断可能不再自动补拉缺口。建议记录失败任务清单并在写入前验证完整性。

### 6.5 追加写入没有去重保护

`stock_bar`、`daily_basic` 和 `index_data` 使用纯 `INSERT` 追加。如果重复执行相同日期范围，或中途写入后进度判定异常，可能生成重复记录。建议使用业务键去重，或实现先删除目标日期后插入的幂等写入。

### 6.6 `MAX(trade_date)` 空值处理不稳健

如果表存在但没有数据，`MAX(trade_date)` 会返回空值。当前直接执行 `str(result.iloc[0, 0])`，得到的可能是 `'None'`、`'nan'` 或 `'NaT'`，再与 `YYYYMMDD` 做字符串比较，结果不可靠。建议先用 `pd.isna()` 判断，空值统一当作 `'0'`。

### 6.7 过宽的异常捕获会掩盖真实故障

`get_dates_todo()` 将除 `RuntimeError` 外的所有查询错误都当作“表不存在”。字段名错误、SQL 语法错误、数据类型异常等也会被忽略。建议只捕获 DuckDB 的“表不存在”异常。

### 6.8 SQL 使用字符串拼接

`table_name` 和 `ts_code` 被直接拼入 SQL。当前调用大多来自内部常量，但如果将来接入外部输入，会有 SQL 注入或非法标识符风险。值参数应使用参数化查询，表名应通过白名单验证。

### 6.9 `get_industry()` 的空结果边界

如果第一页就为空，`all_data` 仍是空列表，`pd.concat(all_data)` 会抛出 `ValueError: No objects to concatenate`。建议在合并前检查 `all_data`。

### 6.10 更新日志与数据库不在同一事务中

DuckDB 数据和 `fetch_log.csv` 是分两步写入的，无法保证原子性。当程序在两步之间中断时，数据与日志可能不一致。可考虑将抓取日志也放入 DuckDB，并与数据写入共用事务。

### 6.11 未使用的导入

`numpy as np` 和 `math` 在当前文件中没有使用，可删除以降低噪声。

## 7. 结论

`get_data.py` 已经形成一套清晰的“Tushare 获取 → 增量判断/低频缓存 → DuckDB/CSV 落盘”流程。其中 `get_dates_todo()` 是日频增量同步的核心，`get_data_by_date()` 是通用并行调度器，`get_last_fetch_date()` / `set_last_fetch_date()` 则控制低频数据的刷新周期。

在正式长期运行前，建议优先处理三项问题：

1. 将 `DB_PATH` 改为可移植的本地配置。
2. 为日频数据增加幂等写入和失败任务补拉机制。
3. 使并行度与 Tushare 账号的实际请求限频匹配。

