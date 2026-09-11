# OpenQuant 数据维护平台

## 启动后端（8072）

在仓库根目录执行：

```bash
source .venv/bin/activate
pip install -e .
python maintenance-platform/backend/app.py
```

## 启动前端（8071）

另开一个终端执行：

```bash
cd maintenance-platform/frontend
npm install
npm run dev
```

浏览器访问 <http://127.0.0.1:8071>。

> DuckDB 不支持多个进程同时持有写连接。同步前请关闭 TablePlus 中对 `data.db` 的连接。
