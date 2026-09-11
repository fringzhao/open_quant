"""get_data_ak 的简单运行测试。"""

from datetime import datetime, timedelta

import pandas as pd

from quant_infra import db_utils
from quant_infra.get_data_ak import (
    get_basic,
    get_daily_basic,
    get_dates_todo,
    get_financial,
    get_industry,
    get_ins,
    get_stock_data,
)


def test_get_stock_data():
    """按自然月分段读取平安银行指定区间的行情。"""
    stock_code = "600519.SH"
    start_date = "20260601"
    end_date = "20260801"

    current_date = datetime.strptime(start_date, "%Y%m%d")
    final_date = datetime.strptime(end_date, "%Y%m%d")
    monthly_results = []

    while current_date <= final_date:
        next_month = (current_date.replace(day=28) + timedelta(days=4)).replace(day=1)
        current_month_end = next_month - timedelta(days=1)
        segment_end = min(current_month_end, final_date)

        segment_start_str = current_date.strftime("%Y%m%d")
        segment_end_str = segment_end.strftime("%Y%m%d")
        print(f"正在获取月度区间：{segment_start_str}~{segment_end_str}")
        monthly_df = get_stock_data(
            stock_code=stock_code,
            start_date=segment_start_str,
            end_date=segment_end_str,
        )
        if not monthly_df.empty:
            monthly_results.append(monthly_df)

        current_date = segment_end + timedelta(days=1)

    if not monthly_results:
        raise RuntimeError(f"未获取到股票 {stock_code} 的行情数据")

    stock_df = (
        pd.concat(monthly_results, ignore_index=True)
        .drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )
    if stock_df.empty:
        raise RuntimeError(f"未获取到股票 {stock_code} 的行情数据")

    print(
        f"get_stock_data 测试成功，共 {len(stock_df)} 条，"
        f"日期范围：{stock_df['trade_date'].min()}~{stock_df['trade_date'].max()}"
    )
    print(stock_df.tail(5).to_string(index=False))
    return stock_df


def test_get_daily_basic():
    """获取贵州茅台指定区间的每日估值，并验证数据库结果。"""
    stock_code = "600519.SH"
    start_date = "20260101"
    end_date = "20260818"

    summary = get_daily_basic(
        stock_codes=[stock_code],
        start_date=start_date,
        end_date=end_date,
        max_workers=1,
        batch_size=1,
    )
    if summary["failed"]:
        raise RuntimeError(f"get_daily_basic 下载失败：{summary['failed']}")

    daily_basic_df = db_utils.read_sql(
        "SELECT * FROM daily_basic "
        f"WHERE ts_code='{stock_code}' "
        f"AND trade_date >= '{start_date}' AND trade_date <= '{end_date}' "
        "ORDER BY trade_date"
    )
    if daily_basic_df.empty:
        raise RuntimeError(f"未获取到股票 {stock_code} 的每日指标")
    if daily_basic_df["total_mv"].isna().all():
        raise AssertionError("daily_basic.total_mv 全部为空")
    if daily_basic_df["pb"].isna().all():
        raise AssertionError("daily_basic.pb 全部为空")
    if daily_basic_df.duplicated(subset=["ts_code", "trade_date"]).any():
        raise AssertionError("daily_basic 中存在重复记录")

    print(
        f"get_daily_basic 测试成功，本次新增 {summary['downloaded_rows']} 条，"
        f"区间内共 {len(daily_basic_df)} 条"
    )
    print(
        daily_basic_df[
            ["ts_code", "trade_date", "close", "pe_ttm", "pb", "total_mv"]
        ].tail(5).to_string(index=False)
    )
    return daily_basic_df


def test_get_dates_todo():
    """测试贵州茅台股票行情表的待更新交易日计算。"""
    start_date = "20260101"
    dates_to_download = get_dates_todo(
        table_name="stock_bar",
        ts_code="002959.SZ",
        start_date=start_date,
    )

    if dates_to_download is None:
        print("get_dates_todo 测试成功：stock_bar 数据已是最新")
        return None
    if dates_to_download != sorted(set(dates_to_download)):
        raise AssertionError("待更新交易日存在重复或未按升序排列")
    if any(date < start_date for date in dates_to_download):
        raise AssertionError("待更新交易日早于 start_date")

    print(
        f"get_dates_todo 测试成功，共 {len(dates_to_download)} 个待更新交易日，"
        f"日期范围：{dates_to_download[0]}~{dates_to_download[-1]}"
    )
    print(f"最近 5 个待更新交易日：{dates_to_download[-5:]}")
    return dates_to_download

# 读取沪深300的成分股Code，写入到csv文件中
def test_get_ins():
    """获取沪深 300 成分股并验证代码格式。"""
    index_code = "000300.SH"
    constituent_codes = get_ins(index_code)
    if not constituent_codes:
        raise RuntimeError(f"未获取到指数 {index_code} 的成分股")
    if not all(code.endswith((".SH", ".SZ", ".BJ")) for code in constituent_codes):
        raise AssertionError("指数成分股中存在不规范的 ts_code")

    print(f"get_ins 测试成功，{index_code} 共 {len(constituent_codes)} 只成分股")
    print(f"前 10 只成分股：{sorted(constituent_codes)[:10]}")
    return constituent_codes

# 读取A股市场所有股票列表数据
def test_get_basic():
    """获取全市场股票基本信息并验证核心字段。"""
    basic_df = get_basic(force=True)
    if basic_df.empty:
        raise RuntimeError("未获取到股票基本信息")
    required_columns = {"ts_code", "symbol", "name", "exchange", "list_status"}
    missing = required_columns - set(basic_df.columns)
    if missing:
        raise AssertionError(f"stock_basic 缺少字段：{sorted(missing)}")
    if basic_df["ts_code"].duplicated().any():
        raise AssertionError("stock_basic 中存在重复股票代码")

    print(f"get_basic 测试成功，共 {len(basic_df)} 只股票")
    print(basic_df[["ts_code", "name", "market", "exchange"]].head(10).to_string(index=False))
    return basic_df

# 获取某支股票的财务指标
def test_get_financial():
    """获取贵州茅台财务指标并验证关键财务字段。"""
    stock_code = "600519.SH"
    financial_df = get_financial(
        stock_codes=[stock_code],
        force=True,
        max_workers=1,
    )
    if financial_df.empty:
        raise RuntimeError(f"未获取到股票 {stock_code} 的财务指标")
    required_columns = {"ts_code", "ann_date", "end_date", "eps", "roe"}
    missing = required_columns - set(financial_df.columns)
    if missing:
        raise AssertionError(f"fina_indicator 缺少字段：{sorted(missing)}")
    if financial_df["end_date"].isna().all():
        raise AssertionError("fina_indicator.end_date 全部为空")

    print(f"get_financial 测试成功，{stock_code} 共 {len(financial_df)} 期财务指标")
    print(
        financial_df[
            ["ts_code", "ann_date", "end_date", "report_type", "eps", "roe"]
        ].tail(5).to_string(index=False)
    )
    return financial_df


def test_get_industry():
    """获取贵州茅台申万行业归属并验证分类口径。"""
    stock_code = "600519.SH"
    industry_df = get_industry(
        stock_codes=[stock_code],
        force=True,
        max_workers=1,
    )
    if industry_df.empty:
        raise RuntimeError(f"未获取到股票 {stock_code} 的申万行业数据")
    required_columns = {
        "ts_code", "change_date", "industry_standard", "industry_code",
    }
    missing = required_columns - set(industry_df.columns)
    if missing:
        raise AssertionError(f"sw_industry 缺少字段：{sorted(missing)}")
    if not industry_df["industry_standard"].astype(str).str.contains("申银万国").all():
        raise AssertionError("sw_industry 中混入了非申银万国分类数据")

    print(f"get_industry 测试成功，{stock_code} 共 {len(industry_df)} 条行业记录")
    print(industry_df.tail(5).to_string(index=False))
    return industry_df


def main():
    test_get_stock_data()    # 拉取股票行情数据
    # test_get_daily_basic()
    # test_get_dates_todo()
    # test_get_ins()

    # test_get_basic()       # 拉去股票基本信息
    
    # test_get_financial()
    # test_get_industry()


if __name__ == "__main__":
    main()
