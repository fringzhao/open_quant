from __future__ import annotations

import math
import os
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path

import duckdb
from flask import Flask, jsonify, request


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
DB_PATH = Path(os.environ.get("OPEN_QUANT_DB_PATH", REPO_ROOT / "data.db")).resolve()
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

app = Flask(__name__)
sync_lock = threading.Lock()
jobs_lock = threading.Lock()
sync_jobs: dict[str, dict] = {}
BAR_PAGE_SIZES = {20, 30, 50, 100}
INDEX_OPTIONS = {"000300": "沪深300"}
INDEX_FETCH_CODES = {"000300": "000300.SH"}
INDEX_DATA_OPTIONS = {"000300.SH": "沪深300"}
index_sync_lock = threading.Lock()
index_data_sync_lock = threading.Lock()


def api_error(message: str, status: int = 400):
    return jsonify({"message": message}), status


def database_error(exc: Exception):
    message = str(exc)
    if "Could not set lock" in message or "Conflicting lock" in message:
        return api_error("数据库正被其他程序占用，请关闭 TablePlus 等连接后重试。", 503)
    app.logger.exception("Database operation failed")
    return api_error("数据库访问失败，请查看后端日志。", 500)


def connect_db(read_only: bool = True):
    if not DB_PATH.exists():
        raise FileNotFoundError(f"数据库文件不存在：{DB_PATH}")
    return duckdb.connect(str(DB_PATH), read_only=read_only)


def normalize_date(value: str | None, field_name: str) -> str:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").strftime("%Y%m%d")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是 YYYY-MM-DD 格式。") from exc


def validate_bar_range(start_value: str | None, end_value: str | None):
    start_date = normalize_date(start_value, "开始日期")
    end_date = normalize_date(end_value, "结束日期")
    if start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期。")
    return start_date, end_date


def validate_index_code(value: str | None) -> str:
    index_code = str(value or "").strip().upper()
    if index_code not in INDEX_OPTIONS:
        raise ValueError("当前仅支持沪深300（000300）。")
    return index_code


def validate_index_data_code(value: str | None) -> str:
    index_code = str(value or "").strip().upper()
    if index_code not in INDEX_DATA_OPTIONS:
        raise ValueError("当前仅支持沪深300（000300.SH）。")
    return index_code


def ensure_index_stock_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS index_stock (
            id BIGINT PRIMARY KEY,
            index_code VARCHAR NOT NULL,
            stock_code VARCHAR NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (index_code, stock_code)
        )
        """
    )


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "http://localhost:8071"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return response


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "database": str(DB_PATH)})


@app.get("/api/index-stocks")
def list_index_stocks():
    try:
        index_code = validate_index_code(request.args.get("index_code", "000300"))
        stock_code = (request.args.get("stock_code") or "").strip()
        page = max(1, request.args.get("page", 1, type=int))
        page_size = 20
        clauses = ["i.index_code = ?"]
        params = [index_code]
        if stock_code:
            clauses.append("i.stock_code ILIKE ?")
            params.append(f"%{stock_code}%")
        where_sql = " WHERE " + " AND ".join(clauses)

        with connect_db(read_only=False) as conn:
            ensure_index_stock_table(conn)
            total = conn.execute(
                f"SELECT COUNT(*) FROM index_stock i{where_sql}", params
            ).fetchone()[0]
            total_pages = max(1, math.ceil(total / page_size))
            page = min(page, total_pages)
            rows = conn.execute(
                f"""
                SELECT i.id, i.index_code, i.stock_code, b.name AS stock_name,
                       i.created_at
                FROM index_stock i
                LEFT JOIN stock_basic b ON b.ts_code = i.stock_code
                {where_sql}
                ORDER BY i.stock_code
                LIMIT ? OFFSET ?
                """,
                [*params, page_size, (page - 1) * page_size],
            ).fetch_df()
        clean_rows = rows.astype(object).where(rows.notna(), None)
        return jsonify({
            "items": clean_rows.to_dict("records"),
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "index_name": INDEX_OPTIONS[index_code],
        })
    except ValueError as exc:
        return api_error(str(exc))
    except Exception as exc:
        return database_error(exc)


@app.post("/api/index-stocks/sync")
def sync_index_stocks():
    try:
        payload = request.get_json(silent=True) or {}
        index_code = validate_index_code(payload.get("index_code"))
    except ValueError as exc:
        return api_error(str(exc))

    if not index_sync_lock.acquire(blocking=False):
        return api_error("该指数正在同步，请稍候。", 409)
    try:
        from quant_infra.get_data_ak import get_ins

        fetch_code = INDEX_FETCH_CODES[index_code]
        components = sorted({str(code).strip().upper() for code in get_ins(fetch_code) if code})
        if not components:
            raise RuntimeError(f"指数 {index_code} 未返回任何成分股，已保留原数据。")

        created_at = datetime.now()
        with connect_db(read_only=False) as conn:
            ensure_index_stock_table(conn)
            conn.execute("BEGIN TRANSACTION")
            try:
                conn.execute(
                    "DELETE FROM index_stock WHERE index_code IN (?, ?)",
                    [index_code, fetch_code],
                )
                next_id = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM index_stock").fetchone()[0]
                conn.executemany(
                    "INSERT INTO index_stock (id, index_code, stock_code, created_at) VALUES (?, ?, ?, ?)",
                    [
                        (next_id + offset, index_code, stock_code, created_at)
                        for offset, stock_code in enumerate(components)
                    ],
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return jsonify({
            "message": f"{INDEX_OPTIONS[index_code]}成分股同步完成，共写入 {len(components)} 条。",
            "count": len(components),
        })
    except Exception as exc:
        message = str(exc)
        if "Could not set lock" in message or "Conflicting lock" in message:
            return database_error(exc)
        app.logger.exception("Index stock sync failed")
        return api_error(message.splitlines()[0] or "指数成分股同步失败。", 500)
    finally:
        index_sync_lock.release()


@app.get("/api/index-data")
def list_index_data():
    try:
        index_code = validate_index_data_code(
            request.args.get("index_code", "000300.SH")
        )
        page = max(1, request.args.get("page", 1, type=int))
        page_size = 20
        with connect_db() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM index_data WHERE ts_code = ?", [index_code]
            ).fetchone()[0]
            total_pages = max(1, math.ceil(total / page_size))
            page = min(page, total_pages)
            rows = conn.execute(
                """
                SELECT ts_code, trade_date, close, open, high, low, pre_close,
                       change, pct_chg, vol, amount
                FROM index_data
                WHERE ts_code = ?
                ORDER BY trade_date DESC
                LIMIT ? OFFSET ?
                """,
                [index_code, page_size, (page - 1) * page_size],
            ).fetch_df()
        clean_rows = rows.astype(object).where(rows.notna(), None)
        return jsonify({
            "items": clean_rows.to_dict("records"),
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "index_name": INDEX_DATA_OPTIONS[index_code],
        })
    except ValueError as exc:
        return api_error(str(exc))
    except Exception as exc:
        return database_error(exc)


@app.post("/api/index-data/sync")
def sync_index_data():
    try:
        payload = request.get_json(silent=True) or {}
        index_code = validate_index_data_code(payload.get("index_code"))
    except ValueError as exc:
        return api_error(str(exc))

    if not index_data_sync_lock.acquire(blocking=False):
        return api_error("该指数指标正在同步，请稍候。", 409)
    try:
        from quant_infra.get_data_ak import get_index_data

        result = get_index_data(index_code)
        return jsonify({
            "message": f"{INDEX_DATA_OPTIONS[index_code]}指标同步完成，当前共有 {len(result)} 条数据。",
            "count": int(len(result)),
        })
    except Exception as exc:
        message = str(exc)
        if "Could not set lock" in message or "Conflicting lock" in message:
            return database_error(exc)
        app.logger.exception("Index data sync failed")
        return api_error(message.splitlines()[0] or "指数指标同步失败。", 500)
    finally:
        index_data_sync_lock.release()


@app.get("/api/stock-basic")
def list_stock_basic():
    try:
        page = max(1, request.args.get("page", 1, type=int))
        page_size = request.args.get("page_size", 20, type=int)
        page_size = min(100, max(10, page_size or 20))
        exchange = (request.args.get("exchange") or "").strip().upper()
        code = (request.args.get("code") or "").strip()
        name = (request.args.get("name") or "").strip()

        if exchange and exchange not in {"SH", "SZ"}:
            return api_error("exchange 仅支持 SH 或 SZ。")

        clauses, params = [], []
        if exchange:
            clauses.append("exchange = ?")
            params.append(exchange)
        if code:
            clauses.append("(ts_code ILIKE ? OR symbol ILIKE ?)")
            params.extend([f"%{code}%", f"%{code}%"])
        if name:
            clauses.append("name ILIKE ?")
            params.append(f"%{name}%")
        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        with connect_db() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM stock_basic{where_sql}", params
            ).fetchone()[0]
            total_pages = max(1, math.ceil(total / page_size))
            page = min(page, total_pages)
            offset = (page - 1) * page_size
            rows = conn.execute(
                f"""
                SELECT ts_code, symbol, name, exchange, market, industry,
                       area, list_status, list_date
                FROM stock_basic{where_sql}
                ORDER BY ts_code LIMIT ? OFFSET ?
                """,
                [*params, page_size, offset],
            ).fetch_df()
        clean_rows = rows.astype(object).where(rows.notna(), None)
        return jsonify({
            "items": clean_rows.to_dict("records"),
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        })
    except (ValueError, TypeError):
        return api_error("分页参数格式不正确。")
    except Exception as exc:
        return database_error(exc)


@app.get("/api/stock-basic/<path:ts_code>")
def stock_basic_detail(ts_code: str):
    try:
        with connect_db() as conn:
            cursor = conn.execute("SELECT * FROM stock_basic WHERE ts_code = ?", [ts_code])
            row = cursor.fetchone()
            if row is None:
                return api_error("未找到该股票。", 404)
            columns = [item[0] for item in cursor.description]
        return jsonify(dict(zip(columns, row)))
    except Exception as exc:
        return database_error(exc)


@app.get("/api/stock-basic/<path:ts_code>/bars")
def list_stock_bars(ts_code: str):
    try:
        start_date, end_date = validate_bar_range(
            request.args.get("start_date"), request.args.get("end_date")
        )
        page = max(1, request.args.get("page", 1, type=int))
        page_size = request.args.get("page_size", 20, type=int)
        if page_size not in BAR_PAGE_SIZES:
            return api_error("page_size 仅支持 20、30、50 或 100。")

        params = [ts_code.upper(), start_date, end_date]
        where_sql = " WHERE ts_code = ? AND trade_date >= ? AND trade_date <= ?"
        with connect_db() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM stock_bar{where_sql}", params
            ).fetchone()[0]
            total_pages = max(1, math.ceil(total / page_size))
            page = min(page, total_pages)
            rows = conn.execute(
                f"""
                SELECT ts_code, trade_date, open, high, low, close, pre_close,
                       change, pct_chg, vol, amount
                FROM stock_bar{where_sql}
                ORDER BY trade_date DESC LIMIT ? OFFSET ?
                """,
                [*params, page_size, (page - 1) * page_size],
            ).fetch_df()
        clean_rows = rows.astype(object).where(rows.notna(), None)
        return jsonify({
            "items": clean_rows.to_dict("records"),
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        })
    except ValueError as exc:
        return api_error(str(exc))
    except Exception as exc:
        return database_error(exc)


@app.post("/api/stock-basic/<path:ts_code>/bars/sync")
def sync_stock_bars(ts_code: str):
    try:
        payload = request.get_json(silent=True) or {}
        start_date, end_date = validate_bar_range(
            payload.get("start_date"), payload.get("end_date")
        )
        from quant_infra.get_data_ak import get_stock_data

        result = get_stock_data(
            stock_code=ts_code.upper(),
            start_date=start_date,
            end_date=end_date,
        )
        return jsonify({
            "message": f"行情同步完成，当前区间共 {len(result)} 条记录。",
            "count": int(len(result)),
        })
    except ValueError as exc:
        return api_error(str(exc))
    except Exception as exc:
        message = str(exc)
        if "Could not set lock" in message or "Conflicting lock" in message:
            return database_error(exc)
        app.logger.exception("Stock bar sync failed")
        return api_error(message.splitlines()[0] or "行情同步失败。", 500)


@app.get("/api/stock-basic/<path:ts_code>/daily-basic")
def list_daily_basic(ts_code: str):
    try:
        start_date, end_date = validate_bar_range(
            request.args.get("start_date"), request.args.get("end_date")
        )
        page = max(1, request.args.get("page", 1, type=int))
        page_size = request.args.get("page_size", 20, type=int)
        if page_size not in BAR_PAGE_SIZES:
            return api_error("page_size 仅支持 20、30、50 或 100。")

        params = [ts_code.upper(), start_date, end_date]
        where_sql = " WHERE ts_code = ? AND trade_date >= ? AND trade_date <= ?"
        with connect_db() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM daily_basic{where_sql}", params
            ).fetchone()[0]
            total_pages = max(1, math.ceil(total / page_size))
            page = min(page, total_pages)
            rows = conn.execute(
                f"""
                SELECT ts_code, trade_date, close, turnover_rate, turnover_rate_f,
                       volume_ratio, pe, pe_ttm, pb, ps, ps_ttm, dv_ratio, dv_ttm,
                       total_share, float_share, free_share, total_mv, circ_mv
                FROM daily_basic{where_sql}
                ORDER BY trade_date DESC LIMIT ? OFFSET ?
                """,
                [*params, page_size, (page - 1) * page_size],
            ).fetch_df()
        clean_rows = rows.astype(object).where(rows.notna(), None)
        return jsonify({
            "items": clean_rows.to_dict("records"),
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        })
    except ValueError as exc:
        return api_error(str(exc))
    except Exception as exc:
        return database_error(exc)


@app.post("/api/stock-basic/<path:ts_code>/daily-basic/calculate")
def calculate_daily_basic(ts_code: str):
    try:
        payload = request.get_json(silent=True) or {}
        start_date, end_date = validate_bar_range(
            payload.get("start_date"), payload.get("end_date")
        )
        from quant_infra.get_data_ak import get_daily_basic

        normalized_code = ts_code.upper()
        result = get_daily_basic(
            stock_codes=[normalized_code],
            start_date=start_date,
            end_date=end_date,
            max_workers=1,
            batch_size=1,
        )
        failures = result.get("failed", {})
        if failures:
            failure = failures.get(normalized_code) or next(iter(failures.values()))
            raise RuntimeError(f"股票 {normalized_code} 每日指标计算失败：{failure}")
        return jsonify({
            "message": f"指标计算完成，本次新增 {int(result.get('downloaded_rows', 0))} 条数据。",
            "downloaded_rows": int(result.get("downloaded_rows", 0)),
        })
    except ValueError as exc:
        return api_error(str(exc))
    except Exception as exc:
        message = str(exc)
        if "Could not set lock" in message or "Conflicting lock" in message:
            return database_error(exc)
        app.logger.exception("Daily basic calculation failed")
        return api_error(message.splitlines()[0] or "每日指标计算失败。", 500)


def update_job(job_id: str, **changes):
    with jobs_lock:
        sync_jobs[job_id].update(changes)


def run_sync(job_id: str):
    try:
        update_job(job_id, status="running", progress=10, message="正在连接股票数据源…")
        from quant_infra.get_data_ak import get_basic

        update_job(job_id, progress=25, message="正在获取并整理最新股票基础数据…")
        result = get_basic(force=True)
        update_job(job_id, progress=90, message="数据已写入，正在完成校验…")
        count = int(len(result))
        update_job(
            job_id,
            status="completed",
            progress=100,
            message=f"同步完成，共更新 {count} 条股票数据。",
            count=count,
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
    except Exception as exc:
        update_job(
            job_id,
            status="failed",
            progress=100,
            message=str(exc).splitlines()[0],
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
    finally:
        sync_lock.release()


@app.post("/api/stock-basic/sync")
def start_sync():
    if not sync_lock.acquire(blocking=False):
        with jobs_lock:
            active = next(
                (job for job in sync_jobs.values() if job["status"] in {"pending", "running"}),
                None,
            )
        return jsonify(active), 200

    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "status": "pending",
        "progress": 0,
        "message": "同步任务已创建。",
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    with jobs_lock:
        sync_jobs[job_id] = job
    threading.Thread(target=run_sync, args=(job_id,), daemon=True).start()
    return jsonify(job), 202


@app.get("/api/stock-basic/sync/<job_id>")
def sync_status(job_id: str):
    with jobs_lock:
        job = sync_jobs.get(job_id)
        if job is None:
            return api_error("同步任务不存在。", 404)
        return jsonify(job.copy())


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8072, debug=True, use_reloader=False)
