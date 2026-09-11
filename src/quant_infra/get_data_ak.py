"""使用 AKShare 获取公开市场数据。

沿用现有 DuckDB 表结构，减少对因子计算及回测模块的影响。
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
import re
import time

import pandas as pd

from quant_infra import db_utils
from quant_infra.const import (
    BASIC_INFO_PATH,
    BASIC_RENEW_DAYS,
    FETCH_LOG_PATH,
    FINANCIAL_RENEW_DAYS,
    INDUSTRY_RENEW_DAYS,
    START_DATE,
)


INDEX_DATA_COLUMNS = [
    "ts_code",
    "trade_date",
    "close",
    "open",
    "high",
    "low",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
]
STOCK_BAR_COLUMNS = INDEX_DATA_COLUMNS.copy()
DAILY_BASIC_COLUMNS = [
    "ts_code",
    "trade_date",
    "close",
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
]
STOCK_BASIC_COLUMNS = [
    "ts_code", "symbol", "name", "area", "industry", "fullname", "enname",
    "cnspell", "market", "exchange", "curr_type", "list_status", "list_date",
    "delist_date", "is_hs", "act_name", "act_ent_type",
]
FINANCIAL_COLUMNS = [
    "ts_code", "ann_date", "end_date", "report_type", "currency", "eps",
    "dt_eps", "bps", "ocfps", "total_revenue", "parent_netprofit",
    "profit_dedt", "roe", "roe_dt", "roa", "netprofit_margin",
    "grossprofit_margin", "current_ratio", "quick_ratio", "cash_ratio",
    "debt_to_assets", "assets_turn", "inv_turn", "ar_turn", "roic",
]
INDUSTRY_COLUMNS = [
    "ts_code", "name", "change_date", "industry_standard_code",
    "industry_standard", "industry_code", "industry_section",
    "industry_subclass", "industry_major", "industry_middle",
]

_INDEX_CODE_PATTERN = re.compile(r"^(\d{6})\.(SH|SZ|BJ|CSI)$", re.IGNORECASE)


def _validate_index_code(index_code):
    """校验代码，返回 Tushare 风格代码和带市场前缀的 AKShare 代码。"""
    if not isinstance(index_code, str):
        raise TypeError("index_code 必须是字符串，例如 '000300.SH'")

    normalized_code = index_code.strip().upper()
    match = _INDEX_CODE_PATTERN.fullmatch(normalized_code)
    if not match:
        raise ValueError(
            "index_code 格式不正确，应为六位代码加市场后缀，"
            "例如 '000300.SH' 或 '399001.SZ'"
        )
    market_prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj", "CSI": "csi"}
    return normalized_code, f"{market_prefix[match.group(2).upper()]}{match.group(1)}"


def _latest_completed_date(now=None):
    """沿用原模块口径：18 点后更新当日，否则只更新到前一自然日。"""
    now = now or datetime.now()
    latest = now.date() if now.hour >= 18 else now.date() - timedelta(days=1)
    return latest.strftime("%Y%m%d")


def _read_local_index_data(index_code):
    """读取本地指数数据；首次运行、表不存在时返回标准空表。"""
    try:
        return db_utils.read_sql(
            f"SELECT * FROM index_data WHERE ts_code='{index_code}' "
            "ORDER BY trade_date"
        )
    except RuntimeError:
        raise
    except Exception:
        return pd.DataFrame(columns=INDEX_DATA_COLUMNS)


def _get_local_max_date(index_code):
    try:
        result = db_utils.read_sql(
            f"SELECT MAX(trade_date) AS max_date FROM index_data "
            f"WHERE ts_code='{index_code}'"
        )
    except RuntimeError:
        raise
    except Exception:
        return None

    if result.empty or pd.isna(result.iloc[0, 0]):
        return None
    return pd.to_datetime(str(result.iloc[0, 0])).strftime("%Y%m%d")


def _normalize_index_data(raw_df, index_code):
    """将 AKShare 指数行情转换为原 Tushare `index_daily` 表结构。"""
    if raw_df is None or raw_df.empty:
        return pd.DataFrame(columns=INDEX_DATA_COLUMNS)

    column_map = {
        "日期": "trade_date",
        "date": "trade_date",
        "开盘": "open",
        "open": "open",
        "收盘": "close",
        "close": "close",
        "最高": "high",
        "high": "high",
        "最低": "low",
        "low": "low",
        "成交量": "vol",
        "volume": "vol",
        "成交额": "amount",
        "amount": "amount",
        "涨跌幅": "pct_chg",
        "涨跌额": "change",
    }
    df = raw_df.rename(columns=column_map).copy()
    required = {"trade_date", "open", "close", "high", "low", "vol"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"AKShare 指数行情缺少必要字段: {sorted(missing)}")
    if "amount" not in df.columns:
        # 新浪指数接口不提供成交额，保留兼容列而不伪造数值。
        df["amount"] = float("nan")

    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.strftime("%Y%m%d")
    numeric_columns = ["open", "close", "high", "low", "vol", "amount"]
    for column in ("change", "pct_chg"):
        if column in df.columns:
            numeric_columns.append(column)
        else:
            df[column] = pd.NA
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = df.dropna(subset=["trade_date", "close"]).sort_values("trade_date")
    df["pre_close"] = df["close"] - df["change"]
    missing_pre_close = df["pre_close"].isna()
    df.loc[missing_pre_close, "pre_close"] = df["close"].shift(1)[missing_pre_close]

    missing_change = df["change"].isna()
    df.loc[missing_change, "change"] = (
        df.loc[missing_change, "close"] - df.loc[missing_change, "pre_close"]
    )
    missing_pct_chg = df["pct_chg"].isna()
    df.loc[missing_pct_chg, "pct_chg"] = (
        df.loc[missing_pct_chg, "change"]
        / df.loc[missing_pct_chg, "pre_close"]
        * 100
    )

    df.insert(0, "ts_code", index_code)
    return (
        df[INDEX_DATA_COLUMNS]
        .drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        .reset_index(drop=True)
    )


def _fetch_index_data(ak, akshare_symbol, start_date, end_date):
    """获取指数行情；沪深指数使用新浪并以东方财富作为备用。"""
    fetchers = []
    if akshare_symbol.startswith(("sh", "sz")):
        fetchers.append(
            ("新浪", lambda: ak.stock_zh_index_daily(symbol=akshare_symbol))
        )
    fetchers.append(
        (
            "东方财富",
            lambda: ak.stock_zh_index_daily_em(
                symbol=akshare_symbol,
                start_date=start_date,
                end_date=end_date,
            ),
        )
    )

    errors = []
    for source_name, fetcher in fetchers:
        for attempt in range(1, 3):
            try:
                result = fetcher()
                if result is not None and not result.empty:
                    return result
                errors.append(f"{source_name}第 {attempt} 次返回空数据")
            except Exception as exc:
                errors.append(f"{source_name}第 {attempt}次：{exc}")
            if attempt < 2:
                time.sleep(2)

    error_summary = "; ".join(errors)
    raise RuntimeError(f"所有 AKShare 指数数据源均请求失败：{error_summary}")


def _normalize_date_argument(value, argument_name):
    """校验日期参数并转换为 YYYYMMDD。"""
    try:
        return datetime.strptime(str(value), "%Y%m%d").strftime("%Y%m%d")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{argument_name} 必须是 YYYYMMDD 格式，例如 '20260101'") from exc


def get_trade(start_date, end_date):
    """获取 A 股交易日历，保存到 CSV 并返回 ``cal_date`` 列。

    优先使用 AKShare 的新浪交易日历；若该接口请求失败，则使用上证指数
    实际行情日期作为已发生交易日的回退数据。
    """
    start_date = _normalize_date_argument(start_date, "start_date")
    end_date = _normalize_date_argument(end_date, "end_date")
    if start_date > end_date:
        raise ValueError("start_date 不能晚于 end_date")

    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError(
            "未安装 AKShare，请先安装项目依赖，例如执行 `pip install -e .`"
        ) from exc

    errors = []
    trade_dates = None
    # 最多企图获取两次
    for attempt in range(1, 3):
        try:
            calendar_df = ak.tool_trade_date_hist_sina()
            if calendar_df is not None and not calendar_df.empty:
                date_column = (
                    "trade_date" if "trade_date" in calendar_df.columns else calendar_df.columns[0]
                )
                trade_dates = calendar_df[date_column]
                break
            errors.append(f"新浪交易日历第 {attempt} 次返回空数据")
        except Exception as exc:
            errors.append(f"新浪交易日历第 {attempt} 次：{exc}")
        if attempt < 2:
            time.sleep(2)

    if trade_dates is None:
        try:
            index_df = _fetch_index_data(
                ak,
                "sh000001",
                start_date=start_date,
                end_date=end_date,
            )
            date_column = "date" if "date" in index_df.columns else "日期"
            trade_dates = index_df[date_column]
        except Exception as exc:
            errors.append(f"上证指数行情回退：{exc}")
            raise RuntimeError(
                "通过 AKShare 获取交易日历失败：" + "; ".join(errors)
            ) from exc

    result = pd.DataFrame(
        {"cal_date": pd.to_datetime(trade_dates, errors="coerce").dt.strftime("%Y%m%d")}
    )
    result = (
        result.dropna(subset=["cal_date"])
        .query("cal_date >= @start_date and cal_date <= @end_date")
        .drop_duplicates(subset=["cal_date"])
        .sort_values("cal_date")
        .reset_index(drop=True)
    )

    trade_day_path = Path(BASIC_INFO_PATH) / "trade_day.csv"
    trade_day_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(trade_day_path, index=False)
    return result


def get_dates_todo(table_name, ts_code=None, start_date=START_DATE):
    """返回指定数据表尚未更新的交易日列表，无缺失时返回 ``None``。

    Args:
        table_name: DuckDB 表名，表中需要包含 ``trade_date`` 字段。
        ts_code: 可选证券代码；提供时只检查该证券的数据进度。
        start_date: 首次下载的起始日期，格式为 YYYYMMDD。
    """
    if not isinstance(table_name, str) or not re.fullmatch(r"[A-Za-z_]\w*", table_name):
        raise ValueError("table_name 不是合法的数据库表名")
    if ts_code is not None:
        ts_code = str(ts_code).strip().upper()
        if not re.fullmatch(r"[A-Z0-9._-]+", ts_code):
            raise ValueError("ts_code 包含非法字符")
    start_date = _normalize_date_argument(start_date, "start_date")
    latest_date = _latest_completed_date()

    trade_day_path = Path(BASIC_INFO_PATH) / "trade_day.csv"
    trade_days = None
    if trade_day_path.exists():
        try:
            trade_days = pd.read_csv(trade_day_path, dtype={"cal_date": str})
            if "cal_date" not in trade_days.columns:
                trade_days = None
        except Exception:
            trade_days = None

    calendar_max_date = None
    if trade_days is not None and not trade_days.empty:
        normalized_dates = pd.to_datetime(
            trade_days["cal_date"], errors="coerce"
        ).dt.strftime("%Y%m%d")
        trade_days = pd.DataFrame({"cal_date": normalized_dates}).dropna()
        if not trade_days.empty:
            calendar_max_date = trade_days["cal_date"].max()

    if calendar_max_date is None or calendar_max_date < latest_date:
        trade_days = get_trade(start_date, latest_date)

    try:
        if ts_code is None:
            query = f"SELECT MAX(trade_date) AS max_date FROM {table_name}"
        else:
            query = (
                f"SELECT MAX(trade_date) AS max_date FROM {table_name} "
                f"WHERE ts_code='{ts_code}'"
            )
        max_date_df = db_utils.read_sql(query)
        table_max_date = (
            None
            if max_date_df.empty or pd.isna(max_date_df.iloc[0, 0])
            else pd.to_datetime(str(max_date_df.iloc[0, 0])).strftime("%Y%m%d")
        )
    except RuntimeError:
        raise
    except Exception:
        # 首次运行时数据表不存在，视为没有历史数据。
        table_max_date = None

    dates_to_download = (
        trade_days.loc[
            (trade_days["cal_date"] >= start_date)
            & (trade_days["cal_date"] <= latest_date)
            & (trade_days["cal_date"] > (table_max_date or "00000000")),
            "cal_date",
        ]
        .astype(str)
        .drop_duplicates()
        .sort_values()
        .tolist()
    )
    return dates_to_download or None


def get_last_fetch_date(table_name):
    """读取低频数据表上次成功更新时间。"""
    fetch_log_path = Path(FETCH_LOG_PATH)
    if not fetch_log_path.exists():
        return None
    try:
        log_df = pd.read_csv(fetch_log_path, dtype=str)
    except (OSError, pd.errors.EmptyDataError):
        return None
    if not {"table_name", "last_fetch_date"}.issubset(log_df.columns):
        return None
    row = log_df[log_df["table_name"] == table_name]
    return None if row.empty else row.iloc[-1]["last_fetch_date"]


def set_last_fetch_date(table_name):
    """记录低频数据表本次成功更新时间。"""
    fetch_log_path = Path(FETCH_LOG_PATH)
    fetch_log_path.parent.mkdir(parents=True, exist_ok=True)
    if fetch_log_path.exists():
        try:
            log_df = pd.read_csv(fetch_log_path, dtype=str)
        except (OSError, pd.errors.EmptyDataError):
            log_df = pd.DataFrame(columns=["table_name", "last_fetch_date"])
        if not {"table_name", "last_fetch_date"}.issubset(log_df.columns):
            log_df = pd.DataFrame(columns=["table_name", "last_fetch_date"])
        log_df = log_df[log_df["table_name"] != table_name]
    else:
        log_df = pd.DataFrame(columns=["table_name", "last_fetch_date"])
    new_row = pd.DataFrame(
        {"table_name": [table_name], "last_fetch_date": [datetime.now().strftime("%Y%m%d")]}
    )
    pd.concat([log_df, new_row], ignore_index=True).to_csv(fetch_log_path, index=False)


def _cache_is_fresh(table_name, renew_days):
    last_fetch = get_last_fetch_date(table_name)
    if not last_fetch:
        return False
    try:
        return datetime.now() - datetime.strptime(last_fetch, "%Y%m%d") < timedelta(days=renew_days)
    except ValueError:
        return False


def get_ins(index_code):
    """获取中证指数最新成分股，保存 CSV 并返回 ts_code 集合。"""
    normalized_code, _ = _validate_index_code(index_code)
    symbol = normalized_code.split(".", 1)[0]
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("未安装 AKShare，请先安装项目依赖") from exc

    last_error = None
    raw_df = None
    for attempt in range(1, 3):
        try:
            raw_df = ak.index_stock_cons_csindex(symbol=symbol)
            if raw_df is not None and not raw_df.empty:
                break
        except Exception as exc:
            last_error = exc
        if attempt < 2:
            time.sleep(2)
    if raw_df is None or raw_df.empty:
        raise RuntimeError(f"通过 AKShare 获取指数 {normalized_code} 成分股失败") from last_error

    required = {"成分券代码", "交易所"}
    missing = required - set(raw_df.columns)
    if missing:
        raise ValueError(f"指数成分股缺少字段：{sorted(missing)}")

    exchange_map = {
        "上海证券交易所": "SH", "深圳证券交易所": "SZ", "北京证券交易所": "BJ",
    }
    components = []
    for row in raw_df[["成分券代码", "交易所"]].itertuples(index=False, name=None):
        code = str(row[0]).strip().zfill(6)
        market = exchange_map.get(str(row[1]).strip())
        components.append(f"{code}.{market}" if market else _to_stock_codes(code)[0])

    result = pd.DataFrame({"con_code": sorted(set(components))})
    output_path = Path(BASIC_INFO_PATH) / f"{normalized_code}_ins.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False)
    print(f"指数成分股获取成功：{normalized_code} | 成分数：{len(result)} | 文件：{output_path}")
    return set(result["con_code"])


def _normalize_stock_basic(raw_df):
    """将 AKShare 股票列表转换为 Tushare stock_basic 结构。"""
    code_column = next((c for c in ("code", "代码", "证券代码") if c in raw_df.columns), None)
    name_column = next(
        (c for c in ("name", "code_name", "名称", "证券简称") if c in raw_df.columns),
        None,
    )
    if code_column is None or name_column is None:
        raise ValueError("AKShare 股票列表缺少代码或名称字段")

    rows = []
    for code, name in raw_df[[code_column, name_column]].itertuples(index=False, name=None):
        try:
            code_text = str(code).strip()
            baostock_match = re.fullmatch(r"(sh|sz|bj)\.(\d{6})", code_text, re.IGNORECASE)
            symbol_text = baostock_match.group(2) if baostock_match else code_text.split(".", 1)[0].zfill(6)
            ts_code, symbol = _to_stock_codes(symbol_text)
        except ValueError:
            continue
        exchange = ts_code.rsplit(".", 1)[1]
        if exchange == "BJ":
            market = "北交所"
        elif symbol.startswith(("688", "689")):
            market = "科创板"
        elif symbol.startswith(("300", "301")):
            market = "创业板"
        else:
            market = "主板"
        source_row = raw_df.loc[raw_df[code_column] == code].iloc[0]
        source_status = str(source_row.get("status", "1"))
        list_date = pd.to_datetime(source_row.get("ipoDate"), errors="coerce")
        delist_date = pd.to_datetime(source_row.get("outDate"), errors="coerce")
        rows.append({
            "ts_code": ts_code, "symbol": symbol, "name": str(name).strip(),
            "market": market, "exchange": exchange, "curr_type": "CNY",
            "list_status": "L" if source_status in {"1", "L", "nan"} else "D",
            "list_date": None if pd.isna(list_date) else list_date.strftime("%Y%m%d"),
            "delist_date": None if pd.isna(delist_date) else delist_date.strftime("%Y%m%d"),
        })
    result = pd.DataFrame(rows)
    for column in STOCK_BASIC_COLUMNS:
        if column not in result.columns:
            result[column] = None
    return result[STOCK_BASIC_COLUMNS].drop_duplicates("ts_code").sort_values("ts_code").reset_index(drop=True)


def _fetch_stock_basic_baostock():
    """通过 BaoStock 匿名服务获取当前上市股票，作为 AKShare 备用源。"""
    try:
        import baostock as bs
    except ImportError as exc:
        raise RuntimeError("未安装 baostock") from exc

    login_result = bs.login()
    if login_result.error_code != "0":
        raise RuntimeError(f"BaoStock 登录失败：{login_result.error_msg}")
    try:
        query_result = bs.query_stock_basic()
        if query_result.error_code != "0":
            raise RuntimeError(f"BaoStock 查询失败：{query_result.error_msg}")
        rows = []
        while query_result.next():
            row = dict(zip(query_result.fields, query_result.get_row_data()))
            code = row.get("code", "")
            is_a_share = code.startswith("sh.6") or code.startswith(("sz.0", "sz.3"))
            if row.get("type") == "1" and row.get("status") == "1" and is_a_share:
                rows.append(row)
        return pd.DataFrame(rows)
    finally:
        bs.logout()


def _fetch_stock_basic_from_local_bar():
    """从本地行情恢复最低可用股票列表，名称暂以代码代替。"""
    local_codes = db_utils.read_sql(
        "SELECT DISTINCT ts_code FROM stock_bar WHERE ts_code IS NOT NULL ORDER BY ts_code"
    )
    if local_codes.empty:
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "code": local_codes["ts_code"].astype(str),
            "name": local_codes["ts_code"].astype(str),
        }
    )


def get_basic(force=False):
    """获取沪深京 A 股基本信息，缓存一年并写入 ``stock_basic``。"""
    if not force and _cache_is_fresh("stock_basic", BASIC_RENEW_DAYS):
        try:
            return db_utils.read_sql("SELECT * FROM stock_basic ORDER BY ts_code")
        except Exception:
            pass
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("未安装 AKShare，请先安装项目依赖") from exc

    errors = []
    raw_df = None
    for source_name, fetcher in (
        ("BaoStock", _fetch_stock_basic_baostock),
        ("东方财富行情列表", ak.stock_zh_a_spot_em),
        ("沪深京列表", ak.stock_info_a_code_name),
    ):
        for attempt in range(1, 3):
            try:
                candidate = fetcher()
                if candidate is not None and not candidate.empty:
                    raw_df = candidate
                    print(f"股票基本信息读取成功 | 数据源：{source_name} | 记录数：{len(candidate)}")
                    break
            except Exception as exc:
                errors.append(f"{source_name}第 {attempt} 次：{exc}")
            if attempt < 2:
                time.sleep(2)
        if raw_df is not None:
            break
    if raw_df is None or raw_df.empty:
        try:
            raw_df = _fetch_stock_basic_from_local_bar()
            if raw_df is not None and not raw_df.empty:
                print(
                    "警告：公网股票列表均不可用，已从 stock_bar 恢复股票代码；"
                    "股票名称暂以代码代替"
                )
        except Exception as exc:
            errors.append(f"本地 stock_bar：{exc}")
    if raw_df is None or raw_df.empty:
        raise RuntimeError("通过 AKShare 获取股票基本信息失败：" + "; ".join(errors))

    result = _normalize_stock_basic(raw_df)
    if result.empty:
        raise RuntimeError("股票基本信息标准化后为空")
    # BaoStock 当前主要覆盖沪深市场；把本地已有但列表缺失的代码（如北交所）补入。
    try:
        local_basic = _normalize_stock_basic(_fetch_stock_basic_from_local_bar())
        missing_local = local_basic[~local_basic["ts_code"].isin(result["ts_code"])]
        if not missing_local.empty:
            result = (
                pd.concat([result, missing_local], ignore_index=True)
                .drop_duplicates("ts_code", keep="first")
                .sort_values("ts_code")
                .reset_index(drop=True)
            )
            print(f"从本地 stock_bar 补充股票基本信息：{len(missing_local)} 条")
    except Exception:
        pass
    db_utils.write_to_db(result, "stock_basic", save_mode="replace")
    set_last_fetch_date("stock_basic")
    return result


def _resolve_requested_stock_codes(stock_codes):
    if stock_codes is None:
        basic_df = get_basic()
        return basic_df["ts_code"].dropna().astype(str).drop_duplicates().tolist()
    if isinstance(stock_codes, str):
        stock_codes = [stock_codes]
    return list(dict.fromkeys(_to_stock_codes(code)[0] for code in stock_codes))


def _normalize_financial(raw_df, ts_code):
    """转换东方财富主要财务指标为稳定的核心字段。"""
    rename_map = {
        "NOTICE_DATE": "ann_date", "REPORT_DATE": "end_date",
        "REPORT_TYPE": "report_type", "CURRENCY": "currency",
        "EPSJB": "eps", "EPSKCJB": "dt_eps", "BPS": "bps",
        "MGJYXJJE": "ocfps", "TOTALOPERATEREVE": "total_revenue",
        "PARENTNETPROFIT": "parent_netprofit", "KCFJCXSYJLR": "profit_dedt",
        "ROEJQ": "roe", "ROEKCJQ": "roe_dt", "ZZCJLL": "roa",
        "XSJLL": "netprofit_margin", "XSMLL": "grossprofit_margin",
        "LD": "current_ratio", "SD": "quick_ratio", "XJLLB": "cash_ratio",
        "ZCFZL": "debt_to_assets", "TOAZZL": "assets_turn",
        "CHZZL": "inv_turn", "YSZKZZL": "ar_turn", "ROIC": "roic",
    }
    df = raw_df.rename(columns=rename_map).copy()
    for date_column in ("ann_date", "end_date"):
        if date_column not in df.columns:
            df[date_column] = None
        df[date_column] = pd.to_datetime(df[date_column], errors="coerce").dt.strftime("%Y%m%d")
    for column in FINANCIAL_COLUMNS:
        if column not in df.columns and column != "ts_code":
            df[column] = float("nan") if column not in {"report_type", "currency"} else None
    numeric_columns = set(FINANCIAL_COLUMNS) - {
        "ts_code", "ann_date", "end_date", "report_type", "currency",
    }
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce").astype(float)
    df.insert(0, "ts_code", ts_code)
    return (
        df.dropna(subset=["end_date"])[FINANCIAL_COLUMNS]
        .drop_duplicates(subset=["ts_code", "end_date", "report_type"], keep="last")
        .sort_values("end_date", ascending=False)
        .reset_index(drop=True)
    )


def _fetch_single_financial(ak, ts_code):
    errors = []
    for attempt in range(1, 3):
        try:
            raw_df = ak.stock_financial_analysis_indicator_em(
                symbol=ts_code, indicator="按报告期"
            )
            if raw_df is not None and not raw_df.empty:
                result = _normalize_financial(raw_df, ts_code)
                print(f"财务指标读取成功：{ts_code} | 记录数：{len(result)}")
                return result, None
            errors.append(f"第 {attempt} 次返回空数据")
        except Exception as exc:
            errors.append(f"第 {attempt} 次：{exc}")
        if attempt < 2:
            time.sleep(2)
    return pd.DataFrame(columns=FINANCIAL_COLUMNS), "; ".join(errors)


def get_financial(stock_codes=None, force=False, max_workers=4, allow_partial=False):
    """获取主要财务指标并写入 ``fina_indicator``，默认每半年更新。"""
    if not force and _cache_is_fresh("fina_indicator", FINANCIAL_RENEW_DAYS):
        try:
            return db_utils.read_sql("SELECT * FROM fina_indicator")
        except Exception:
            pass
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("未安装 AKShare，请先安装项目依赖") from exc
    if not isinstance(max_workers, int) or max_workers < 1:
        raise ValueError("max_workers 必须是正整数")
    codes = _resolve_requested_stock_codes(stock_codes)
    frames, failures = [], {}
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(codes)))) as executor:
        futures = {executor.submit(_fetch_single_financial, ak, code): code for code in codes}
        for future in as_completed(futures):
            code = futures[future]
            try:
                frame, error = future.result()
            except Exception as exc:
                frame, error = pd.DataFrame(), str(exc)
            if error:
                failures[code] = error
            elif not frame.empty:
                frames.append(frame)

    if failures and not allow_partial:
        raise RuntimeError(f"{len(failures)} 只股票财务指标下载失败，未覆盖原表：{failures}")
    if not frames:
        raise RuntimeError("未获取到可写入的财务指标")
    result = pd.concat(frames, ignore_index=True).sort_values(["ts_code", "end_date"])
    db_utils.write_to_db(result, "fina_indicator", save_mode="replace")
    if not failures:
        set_last_fetch_date("fina_indicator")
    else:
        print(f"警告：已写入部分财务数据，失败股票数：{len(failures)}")
    return result


def _normalize_industry(raw_df, ts_code):
    rename_map = {
        "新证券简称": "name", "变更日期": "change_date",
        "分类标准编码": "industry_standard_code", "分类标准": "industry_standard",
        "行业编码": "industry_code", "行业门类": "industry_section",
        "行业次类": "industry_subclass", "行业大类": "industry_major",
        "行业中类": "industry_middle",
    }
    df = raw_df.rename(columns=rename_map).copy()
    if "industry_standard" in df.columns:
        df = df[df["industry_standard"].astype(str).str.contains("申银万国", na=False)]
    for column in INDUSTRY_COLUMNS:
        if column not in df.columns and column != "ts_code":
            df[column] = None
    df["change_date"] = pd.to_datetime(df["change_date"], errors="coerce").dt.strftime("%Y%m%d")
    df.insert(0, "ts_code", ts_code)
    return (
        df[INDUSTRY_COLUMNS]
        .drop_duplicates(subset=["ts_code", "change_date", "industry_code"], keep="last")
        .sort_values("change_date")
        .reset_index(drop=True)
    )


def _fetch_single_industry(ak, ts_code):
    _, symbol = _to_stock_codes(ts_code)
    errors = []
    for attempt in range(1, 3):
        try:
            raw_df = ak.stock_industry_change_cninfo(
                symbol=symbol, start_date=START_DATE, end_date=_latest_completed_date()
            )
            if raw_df is not None and not raw_df.empty:
                result = _normalize_industry(raw_df, ts_code)
                print(f"申万行业读取成功：{ts_code} | 记录数：{len(result)}")
                return result, None
            errors.append(f"第 {attempt} 次返回空数据")
        except Exception as exc:
            errors.append(f"第 {attempt} 次：{exc}")
        if attempt < 2:
            time.sleep(2)
    return pd.DataFrame(columns=INDUSTRY_COLUMNS), "; ".join(errors)


def get_industry(stock_codes=None, force=False, max_workers=4, allow_partial=False):
    """获取股票申万行业归属记录并写入 ``sw_industry``，默认每年更新。"""
    if not force and _cache_is_fresh("sw_industry", INDUSTRY_RENEW_DAYS):
        try:
            return db_utils.read_sql("SELECT * FROM sw_industry")
        except Exception:
            pass
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("未安装 AKShare，请先安装项目依赖") from exc
    if not isinstance(max_workers, int) or max_workers < 1:
        raise ValueError("max_workers 必须是正整数")
    codes = _resolve_requested_stock_codes(stock_codes)
    frames, failures = [], {}
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(codes)))) as executor:
        futures = {executor.submit(_fetch_single_industry, ak, code): code for code in codes}
        for future in as_completed(futures):
            code = futures[future]
            try:
                frame, error = future.result()
            except Exception as exc:
                frame, error = pd.DataFrame(), str(exc)
            if error:
                failures[code] = error
            elif not frame.empty:
                frames.append(frame)

    if failures and not allow_partial:
        raise RuntimeError(f"{len(failures)} 只股票行业数据下载失败，未覆盖原表：{failures}")
    if not frames:
        raise RuntimeError("未获取到申万行业归属数据")
    result = pd.concat(frames, ignore_index=True).sort_values(["ts_code", "change_date"])
    db_utils.write_to_db(result, "sw_industry", save_mode="replace")
    if not failures:
        set_last_fetch_date("sw_industry")
    else:
        print(f"警告：已写入部分行业数据，失败股票数：{len(failures)}")
    return result


def _to_stock_codes(stock_code):
    """返回标准 ts_code 和 AKShare 使用的六位股票代码。"""
    value = str(stock_code).strip().upper()
    match = re.fullmatch(r"(\d{6})(?:\.(SH|SZ|BJ))?", value)
    if not match:
        raise ValueError(f"股票代码格式不正确：{stock_code}")

    symbol, supplied_market = match.groups()
    inferred_market = "SH" if symbol.startswith("6") else (
        "BJ" if symbol.startswith(("4", "8", "9")) else "SZ"
    )
    if supplied_market and supplied_market != inferred_market:
        raise ValueError(f"股票代码与市场后缀不匹配：{stock_code}")
    return f"{symbol}.{inferred_market}", symbol


def _get_all_stock_codes(ak):
    """获取当前 A 股代码列表并转换为 ts_code。"""
    errors = []
    for source_name, fetcher in (
        ("东方财富行情列表", ak.stock_zh_a_spot_em),
        ("沪深京列表", ak.stock_info_a_code_name),
    ):
        for attempt in range(1, 3):
            try:
                stock_df = fetcher()
                if stock_df is not None and not stock_df.empty:
                    return _normalize_stock_basic(stock_df)["ts_code"].tolist()
            except Exception as exc:
                errors.append(f"{source_name}第 {attempt} 次：{exc}")
            if attempt < 2:
                time.sleep(2)
    raise RuntimeError("通过 AKShare 获取 A 股代码列表失败：" + "; ".join(errors))


def _get_stock_bar_max_dates(stock_codes):
    """一次读取各股票本地最大交易日，避免逐股票建立数据库连接。"""
    try:
        local_df = db_utils.read_sql(
            "SELECT ts_code, MAX(trade_date) AS max_date "
            "FROM stock_bar GROUP BY ts_code"
        )
    except RuntimeError:
        raise
    except Exception:
        return {}

    wanted = set(stock_codes)
    result = {}
    for row in local_df.itertuples(index=False):
        if row.ts_code in wanted and not pd.isna(row.max_date):
            result[row.ts_code] = pd.to_datetime(str(row.max_date)).strftime("%Y%m%d")
    return result


def _normalize_stock_bar(raw_df, ts_code, volume_scale=1.0):
    """将 AKShare A 股历史行情转换为原 Tushare daily 表结构。"""
    if raw_df is None or raw_df.empty:
        return pd.DataFrame(columns=STOCK_BAR_COLUMNS)

    column_map = {
        "日期": "trade_date",
        "date": "trade_date",
        "开盘": "open",
        "open": "open",
        "收盘": "close",
        "close": "close",
        "最高": "high",
        "high": "high",
        "最低": "low",
        "low": "low",
        "成交量": "vol",
        "volume": "vol",
        "成交额": "amount",
        "amount": "amount",
        "涨跌幅": "pct_chg",
        "涨跌额": "change",
    }
    df = raw_df.rename(columns=column_map).copy()
    required = {"trade_date", "open", "close", "high", "low", "vol", "amount"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"AKShare 股票行情缺少必要字段: {sorted(missing)}")

    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.strftime("%Y%m%d")
    for column in ("open", "close", "high", "low", "vol", "amount"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df["vol"] = df["vol"] * volume_scale
    for column in ("change", "pct_chg"):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
        else:
            df[column] = float("nan")

    df = df.dropna(subset=["trade_date", "close"]).sort_values("trade_date")
    df["pre_close"] = df["close"] - df["change"]
    missing_pre_close = df["pre_close"].isna()
    df.loc[missing_pre_close, "pre_close"] = df["close"].shift(1)[missing_pre_close]
    missing_change = df["change"].isna()
    df.loc[missing_change, "change"] = (
        df.loc[missing_change, "close"] - df.loc[missing_change, "pre_close"]
    )
    missing_pct_chg = df["pct_chg"].isna()
    df.loc[missing_pct_chg, "pct_chg"] = (
        df.loc[missing_pct_chg, "change"]
        / df.loc[missing_pct_chg, "pre_close"]
        * 100
    )
    df.insert(0, "ts_code", ts_code)
    return (
        df[STOCK_BAR_COLUMNS]
        .drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        .reset_index(drop=True)
    )

# 获取某只股票的行情数据
def _fetch_single_stock_bar(ak, ts_code, start_date, end_date):
    """下载一只股票的缺失行情；失败时返回错误信息。"""
    normalized_code, symbol = _to_stock_codes(ts_code)
    market = normalized_code.rsplit(".", 1)[1].lower()
    prefixed_symbol = f"{market}{symbol}"
    request_start = (
        datetime.strptime(start_date, "%Y%m%d") - timedelta(days=10)
    ).strftime("%Y%m%d")
    fetchers = [
        (
            "东方财富",
            lambda: (
                ak.stock_zh_a_hist(
                    symbol=symbol,
                    period="daily",
                    start_date=request_start,
                    end_date=end_date,
                    adjust="",
                    timeout=15,
                ),
                1.0,
            ),
        )
    ]
    if market in {"sh", "sz"}:
        fetchers.append(
            (
                "新浪",
                lambda: (
                    ak.stock_zh_a_daily(
                        symbol=prefixed_symbol,
                        start_date=request_start,
                        end_date=end_date,
                        adjust="",
                    ),
                    0.01,  # 新浪 volume 为股，统一转换为 Tushare 的手。
                ),
            )
        )

    errors = []
    for source_name, fetcher in fetchers:
        for attempt in range(1, 3):
            try:
                raw_df, volume_scale = fetcher()
                if raw_df is None or raw_df.empty:
                    errors.append(f"{source_name}第 {attempt} 次返回空数据")
                else:
                    normalized = _normalize_stock_bar(
                        raw_df, ts_code, volume_scale=volume_scale
                    )
                    result = normalized[
                        (normalized["trade_date"] >= start_date)
                        & (normalized["trade_date"] <= end_date)
                    ]
                    if not result.empty:
                        print(
                            f"股票行情读取成功：{ts_code} | 数据源：{source_name} | "
                            f"记录数：{len(result)} | "
                            f"日期：{result['trade_date'].min()}~{result['trade_date'].max()}"
                        )
                    return result, None
            except Exception as exc:
                errors.append(f"{source_name}第 {attempt} 次：{exc}")
            if attempt < 2:
                time.sleep(2)
    return pd.DataFrame(columns=STOCK_BAR_COLUMNS), "; ".join(errors)


def get_stock_data_by_date(
    stock_codes=None,
    start_date=None,
    end_date=None,
    max_workers=4,
    batch_size=50,
):
    """按股票增量获取 A 股日行情并写入 ``stock_bar``。

    无参调用时更新全市场；测试或小范围更新时可传入股票代码列表和日期范围。
    """
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError(
            "未安装 AKShare，请先安装项目依赖，例如执行 `pip install -e .`"
        ) from exc

    start_date = _normalize_date_argument(start_date or START_DATE, "start_date")
    latest_date = _latest_completed_date()
    end_date = _normalize_date_argument(end_date or latest_date, "end_date")
    end_date = min(end_date, latest_date)
    if start_date > end_date:
        raise ValueError("start_date 不能晚于 end_date")
    if not isinstance(max_workers, int) or max_workers < 1:
        raise ValueError("max_workers 必须是正整数")
    if not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError("batch_size 必须是正整数")

    if stock_codes is None:
        normalized_codes = _get_all_stock_codes(ak)
    else:
        if isinstance(stock_codes, str):
            stock_codes = [stock_codes]
        normalized_codes = [_to_stock_codes(code)[0] for code in stock_codes]
    normalized_codes = list(dict.fromkeys(normalized_codes))

    local_max_dates = _get_stock_bar_max_dates(normalized_codes)
    tasks = []
    for ts_code in normalized_codes:
        local_max = local_max_dates.get(ts_code)
        missing_start = start_date
        if local_max:
            missing_start = max(
                missing_start,
                (datetime.strptime(local_max, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d"),
            )
        if missing_start <= end_date:
            tasks.append((ts_code, missing_start))

    if not tasks:
        print("股票日行情数据已是最新")
        return {"requested": len(normalized_codes), "downloaded_rows": 0, "failed": {}}

    total_rows = 0
    failures = {}
    for offset in range(0, len(tasks), batch_size):
        batch = tasks[offset:offset + batch_size]
        frames = []
        with ThreadPoolExecutor(max_workers=min(max_workers, len(batch))) as executor:
            futures = {
                executor.submit(
                    _fetch_single_stock_bar, ak, ts_code, missing_start, end_date
                ): ts_code
                for ts_code, missing_start in batch
            }
            for future in as_completed(futures):
                ts_code = futures[future]
                try:
                    frame, error = future.result()
                except Exception as exc:
                    frame, error = pd.DataFrame(), str(exc)
                if error:
                    failures[ts_code] = error
                elif not frame.empty:
                    frames.append(frame)

        if frames:
            batch_df = pd.concat(frames, ignore_index=True)
            batch_df = batch_df[batch_df["pct_chg"].abs() < 35]
            batch_df = batch_df.sort_values(["trade_date", "ts_code"])
            db_utils.write_to_db(batch_df, "stock_bar", save_mode="append")
            total_rows += len(batch_df)
        print(f"股票行情进度：{min(offset + batch_size, len(tasks))}/{len(tasks)}")

    if failures:
        print(f"警告：{len(failures)} 只股票下载失败")
    return {
        "requested": len(normalized_codes),
        "downloaded_rows": total_rows,
        "failed": failures,
    }


def _read_stock_bar_range(ts_code, start_date, end_date):
    """读取一只股票指定日期范围的本地行情。"""
    try:
        return db_utils.read_sql(
            "SELECT * FROM stock_bar "
            f"WHERE ts_code='{ts_code}' "
            f"AND trade_date >= '{start_date}' AND trade_date <= '{end_date}' "
            "ORDER BY trade_date"
        )
    except RuntimeError:
        raise
    except Exception:
        return pd.DataFrame(columns=STOCK_BAR_COLUMNS)


def get_stock_data(stock_code, start_date, end_date):
    """读取一只股票指定日期范围的行情，缺失时下载并增量写库。

    本地数据的最早和最晚日期覆盖请求边界时直接返回。未覆盖时从 AKShare
    获取请求区间，并以 ``ts_code + trade_date`` 为键排除数据库已有记录。
    """
    ts_code, _ = _to_stock_codes(stock_code)
    start_date = _normalize_date_argument(start_date, "start_date")
    end_date = _normalize_date_argument(end_date, "end_date")
    latest_date = _latest_completed_date()
    end_date = min(end_date, latest_date)
    if start_date > end_date:
        raise ValueError("start_date 不能晚于 end_date 或当前可用行情日期")

    local_df = _read_stock_bar_range(ts_code, start_date, end_date)
    if (
        not local_df.empty
        and str(local_df["trade_date"].min()) <= start_date
        and str(local_df["trade_date"].max()) >= end_date
    ):
        print(
            f"股票行情已存在：{ts_code} | 记录数：{len(local_df)} | "
            f"日期：{local_df['trade_date'].min()}~{local_df['trade_date'].max()}"
        )
        return local_df

    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError(
            "未安装 AKShare，请先安装项目依赖，例如执行 `pip install -e .`"
        ) from exc

    downloaded_df, error = _fetch_single_stock_bar(
        ak, ts_code, start_date, end_date
    )
    if error:
        raise RuntimeError(f"股票 {ts_code} 行情下载失败：{error}")

    if not downloaded_df.empty:
        downloaded_df = downloaded_df[downloaded_df["pct_chg"].abs() < 35]
        existing_dates = set(local_df["trade_date"].astype(str)) if not local_df.empty else set()
        new_data = downloaded_df[
            ~downloaded_df["trade_date"].astype(str).isin(existing_dates)
        ].copy()
        if not new_data.empty:
            db_utils.write_to_db(
                new_data.sort_values(["trade_date", "ts_code"]),
                "stock_bar",
                save_mode="append",
            )
        else:
            print(f"股票行情无需写入：{ts_code}，请求区间内没有新增数据")

    return _read_stock_bar_range(ts_code, start_date, end_date)


def _get_daily_basic_max_dates(stock_codes):
    """读取各股票 daily_basic 表中的最大交易日。"""
    try:
        local_df = db_utils.read_sql(
            "SELECT ts_code, MAX(trade_date) AS max_date "
            "FROM daily_basic GROUP BY ts_code"
        )
    except RuntimeError:
        raise
    except Exception:
        return {}

    wanted = set(stock_codes)
    result = {}
    for row in local_df.itertuples(index=False):
        if row.ts_code in wanted and not pd.isna(row.max_date):
            result[row.ts_code] = pd.to_datetime(str(row.max_date)).strftime("%Y%m%d")
    return result


def _normalize_daily_basic(raw_df, ts_code):
    """将 AKShare 估值数据转换为 Tushare daily_basic 字段和单位。"""
    if raw_df is None or raw_df.empty:
        return pd.DataFrame(columns=DAILY_BASIC_COLUMNS)

    column_map = {
        "数据日期": "trade_date",
        "当日收盘价": "close",
        "总市值": "total_mv",
        "流通市值": "circ_mv",
        "总股本": "total_share",
        "流通股本": "float_share",
        "PE(TTM)": "pe_ttm",
        "PE(静)": "pe",
        "市净率": "pb",
        "市销率": "ps_ttm",
    }
    df = raw_df.rename(columns=column_map).copy()
    required = {"trade_date", "total_mv", "pb"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"AKShare 每日估值数据缺少必要字段: {sorted(missing)}")

    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.strftime("%Y%m%d")
    source_numeric_columns = [
        "close", "pe", "pe_ttm", "pb", "ps_ttm",
        "total_share", "float_share", "total_mv", "circ_mv",
    ]
    for column in source_numeric_columns:
        if column not in df.columns:
            df[column] = float("nan")
        df[column] = pd.to_numeric(df[column], errors="coerce").astype(float)

    # AKShare 返回元和股；Tushare daily_basic 的对应字段单位为万元和万股。
    for column in ("total_share", "float_share", "total_mv", "circ_mv"):
        df[column] = df[column] / 10000.0

    for column in DAILY_BASIC_COLUMNS:
        if column not in df.columns and column not in {"ts_code", "trade_date"}:
            df[column] = float("nan")

    df.insert(0, "ts_code", ts_code)
    return (
        df.dropna(subset=["trade_date"])[DAILY_BASIC_COLUMNS]
        .drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )


def _fetch_single_daily_basic(ak, ts_code, start_date, end_date):
    """获取一只股票指定区间的每日估值数据。"""
    _, symbol = _to_stock_codes(ts_code)
    errors = []
    for attempt in range(1, 3):
        try:
            raw_df = ak.stock_value_em(symbol=symbol)
            normalized = _normalize_daily_basic(raw_df, ts_code)
            result = normalized[
                (normalized["trade_date"] >= start_date)
                & (normalized["trade_date"] <= end_date)
            ]
            if not result.empty:
                print(
                    f"每日指标读取成功：{ts_code} | 数据源：东方财富 | "
                    f"记录数：{len(result)} | "
                    f"日期：{result['trade_date'].min()}~{result['trade_date'].max()}"
                )
            return result, None
        except Exception as exc:
            errors.append(f"第 {attempt} 次：{exc}")
            if attempt < 2:
                time.sleep(2)
    return pd.DataFrame(columns=DAILY_BASIC_COLUMNS), "; ".join(errors)


def get_daily_basic(
    stock_codes=None,
    start_date=None,
    end_date=None,
    max_workers=4,
    batch_size=50,
):
    """按股票增量获取每日估值指标并写入 ``daily_basic``。

    无参调用时更新全市场。免费接口暂不提供的字段保留为 NaN；核心字段
    ``total_mv`` 和 ``pb`` 可直接供现有 SMB、HML 因子计算使用。
    """
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError(
            "未安装 AKShare，请先安装项目依赖，例如执行 `pip install -e .`"
        ) from exc

    start_date = _normalize_date_argument(start_date or START_DATE, "start_date")
    latest_date = _latest_completed_date()
    end_date = _normalize_date_argument(end_date or latest_date, "end_date")
    end_date = min(end_date, latest_date)
    if start_date > end_date:
        raise ValueError("start_date 不能晚于 end_date")
    if not isinstance(max_workers, int) or max_workers < 1:
        raise ValueError("max_workers 必须是正整数")
    if not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError("batch_size 必须是正整数")

    if stock_codes is None:
        normalized_codes = _get_all_stock_codes(ak)
    else:
        if isinstance(stock_codes, str):
            stock_codes = [stock_codes]
        normalized_codes = [_to_stock_codes(code)[0] for code in stock_codes]
    normalized_codes = list(dict.fromkeys(normalized_codes))

    local_max_dates = _get_daily_basic_max_dates(normalized_codes)
    tasks = []
    for ts_code in normalized_codes:
        missing_start = start_date
        local_max = local_max_dates.get(ts_code)
        if local_max:
            missing_start = max(
                missing_start,
                (datetime.strptime(local_max, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d"),
            )
        if missing_start <= end_date:
            tasks.append((ts_code, missing_start))

    if not tasks:
        print("每日指标数据已是最新")
        return {"requested": len(normalized_codes), "downloaded_rows": 0, "failed": {}}

    total_rows = 0
    failures = {}
    for offset in range(0, len(tasks), batch_size):
        batch = tasks[offset:offset + batch_size]
        frames = []
        with ThreadPoolExecutor(max_workers=min(max_workers, len(batch))) as executor:
            futures = {
                executor.submit(
                    _fetch_single_daily_basic, ak, ts_code, missing_start, end_date
                ): ts_code
                for ts_code, missing_start in batch
            }
            for future in as_completed(futures):
                ts_code = futures[future]
                try:
                    frame, error = future.result()
                except Exception as exc:
                    frame, error = pd.DataFrame(), str(exc)
                if error:
                    failures[ts_code] = error
                elif not frame.empty:
                    frames.append(frame)

        if frames:
            batch_df = pd.concat(frames, ignore_index=True)
            batch_df = batch_df.sort_values(["trade_date", "ts_code"])
            db_utils.write_to_db(batch_df, "daily_basic", save_mode="append")
            total_rows += len(batch_df)
        print(f"每日指标进度：{min(offset + batch_size, len(tasks))}/{len(tasks)}")

    if failures:
        print(f"警告：{len(failures)} 只股票的每日指标下载失败")
    return {
        "requested": len(normalized_codes),
        "downloaded_rows": total_rows,
        "failed": failures,
    }


def get_index_data(index_code):
    """增量获取指数日频行情，并返回该指数的全部本地数据。

    Args:
        index_code: Tushare 风格指数代码，例如 ``000300.SH``、``399001.SZ``。

    Returns:
        与原 ``get_data.get_index_data`` 兼容的 DataFrame，字段顺序与
        Tushare ``index_daily`` 一致。
    """
    normalized_code, akshare_symbol = _validate_index_code(index_code)
    latest_date = _latest_completed_date()
    local_max_date = _get_local_max_date(normalized_code)

    if local_max_date and local_max_date >= latest_date:
        return _read_local_index_data(normalized_code)

    first_missing_date = (
        (datetime.strptime(local_max_date, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        if local_max_date
        else START_DATE
    )

    # 增量更新时向前多取十天，用于在接口未返回涨跌额时计算首日昨收价。
    request_start_date = (
        datetime.strptime(first_missing_date, "%Y%m%d") - timedelta(days=10)
    ).strftime("%Y%m%d")

    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError(
            "未安装 AKShare，请先安装项目依赖，例如执行 `pip install -e .`"
        ) from exc

    try:
        raw_df = _fetch_index_data(
            ak,
            akshare_symbol,
            start_date=request_start_date,
            end_date=latest_date,
        )
    except Exception as exc:
        raise RuntimeError(f"通过 AKShare 获取指数 {normalized_code} 行情失败") from exc

    new_data = _normalize_index_data(raw_df, normalized_code)
    new_data = new_data[
        (new_data["trade_date"] >= first_missing_date)
        & (new_data["trade_date"] <= latest_date)
    ]
    if not new_data.empty:
        db_utils.write_to_db(new_data, "index_data", save_mode="append")

    return _read_local_index_data(normalized_code)


def test_get_index_data():
    """运行一次沪深 300 指数行情下载测试。"""
    index_code = "000300.SH"
    print(f"开始测试 get_index_data，指数代码：{index_code}")

    index_df = get_index_data(index_code)
    if index_df.empty:
        print(f"未获取到指数 {index_code} 的行情数据")
        return

    print(
        f"获取成功，共 {len(index_df)} 条，"
        f"日期范围：{index_df['trade_date'].min()} 至 {index_df['trade_date'].max()}"
    )
    print("最近 5 条行情：")
    print(index_df.sort_values("trade_date").tail(5).to_string(index=False))

def test_get_trade():
    """测试交易日历获取及 CSV 保存功能。"""
    start_date = START_DATE
    end_date = _latest_completed_date()
    print(f"开始测试 get_trade，日期范围：{start_date} 至 {end_date}")

    trade_df = get_trade(start_date, end_date)
    if trade_df.empty:
        raise RuntimeError(
            f"get_trade 未返回 {start_date} 至 {end_date} 范围内的交易日"
        )

    if trade_df["cal_date"].duplicated().any():
        raise AssertionError("get_trade 返回了重复交易日")
    if not trade_df["cal_date"].is_monotonic_increasing:
        raise AssertionError("get_trade 返回的交易日未按升序排列")

    trade_day_path = Path(BASIC_INFO_PATH) / "trade_day.csv"
    if not trade_day_path.exists():
        raise AssertionError(f"交易日历文件未生成：{trade_day_path}")

    print(
        f"get_trade 测试成功，共 {len(trade_df)} 个交易日，"
        f"日期范围：{trade_df['cal_date'].min()} 至 {trade_df['cal_date'].max()}"
    )
    print(f"CSV 文件：{trade_day_path}")
    print("最近 5 个交易日：")
    print(trade_df.tail(5).to_string(index=False))
    return trade_df


def test_get_stock_data_by_date():
    """用两只样本股票测试行情下载、增量入库和字段完整性。"""
    stock_codes = ["000001.SZ", "600519.SH"]
    end_date = _latest_completed_date()
    start_date = (
        datetime.strptime(end_date, "%Y%m%d") - timedelta(days=60)
    ).strftime("%Y%m%d")
    print(
        f"开始测试 get_stock_data_by_date，股票：{stock_codes}，"
        f"日期范围：{start_date} 至 {end_date}"
    )

    summary = get_stock_data_by_date(
        stock_codes=stock_codes,
        start_date=start_date,
        end_date=end_date,
        max_workers=2,
        batch_size=2,
    )
    if summary["failed"]:
        raise RuntimeError(f"样本股票下载失败：{summary['failed']}")

    quoted_codes = ", ".join(f"'{code}'" for code in stock_codes)
    stock_df = db_utils.read_sql(
        "SELECT * FROM stock_bar "
        f"WHERE ts_code IN ({quoted_codes}) "
        f"AND trade_date >= '{start_date}' AND trade_date <= '{end_date}' "
        "ORDER BY ts_code, trade_date"
    )
    if stock_df.empty:
        raise AssertionError("stock_bar 中未找到样本股票行情")

    missing = set(STOCK_BAR_COLUMNS) - set(stock_df.columns)
    if missing:
        raise AssertionError(f"stock_bar 缺少字段：{sorted(missing)}")
    if stock_df.duplicated(subset=["ts_code", "trade_date"]).any():
        raise AssertionError("stock_bar 中存在重复的股票和交易日")

    print(
        f"get_stock_data_by_date 测试成功，本次新增 {summary['downloaded_rows']} 条，"
        f"样本区间共 {len(stock_df)} 条"
    )
    print(stock_df.groupby("ts_code")["trade_date"].agg(["count", "min", "max"]))
    print("最近行情：")
    print(stock_df.groupby("ts_code", group_keys=False).tail(2).to_string(index=False))
    return stock_df


if __name__ == "__main__":
    test_get_stock_data_by_date()
