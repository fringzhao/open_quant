<script setup>
import { computed, onMounted, reactive, ref } from 'vue'

const rows = ref([])
const activePage = ref('stocks')
const loading = ref(false)
const error = ref('')
const selected = ref(null)
const detailLoading = ref(false)
const barsVisible = ref(false)
const barsLoading = ref(false)
const barStock = reactive({ tsCode: '', name: '' })
const barFilters = reactive({ startDate: '', endDate: '' })
const barRows = ref([])
const barPager = reactive({ page: 1, pageSize: 20, total: 0, totalPages: 1 })
const barSync = reactive({ active: false, message: '', success: false })
const metricsVisible = ref(false)
const metricsLoading = ref(false)
const metricContext = ref('stock')
const metricStock = reactive({ tsCode: '', name: '' })
const metricFilters = reactive({ startDate: '', endDate: '' })
const metricRows = ref([])
const metricPager = reactive({ page: 1, pageSize: 20, total: 0, totalPages: 1 })
const metricCalculation = reactive({ active: false, message: '', success: false })
const filters = reactive({ exchange: '', code: '', name: '' })
const pager = reactive({ page: 1, pageSize: 20, total: 0, totalPages: 1 })
const sync = reactive({ active: false, progress: 0, message: '', status: '' })
const indexFilters = reactive({ indexCode: '000300', stockCode: '' })
const indexRows = ref([])
const indexLoading = ref(false)
const indexError = ref('')
const indexPager = reactive({ page: 1, pageSize: 20, total: 0, totalPages: 1 })
const indexSync = reactive({ active: false, message: '', success: false })
const indexDataFilters = reactive({ indexCode: '000300.SH' })
const indexDataRows = ref([])
const indexDataLoading = ref(false)
const indexDataError = ref('')
const indexDataPager = reactive({ page: 1, pageSize: 20, total: 0, totalPages: 1 })
const indexDataSync = reactive({ active: false, message: '', success: false })

const pageRange = computed(() => {
  const start = Math.max(1, pager.page - 2)
  const end = Math.min(pager.totalPages, start + 4)
  return Array.from({ length: Math.max(0, end - start + 1) }, (_, index) => start + index)
})

const barPageRange = computed(() => {
  const start = Math.max(1, barPager.page - 2)
  const end = Math.min(barPager.totalPages, start + 4)
  return Array.from({ length: Math.max(0, end - start + 1) }, (_, index) => start + index)
})

const metricPageRange = computed(() => {
  const start = Math.max(1, metricPager.page - 2)
  const end = Math.min(metricPager.totalPages, start + 4)
  return Array.from({ length: Math.max(0, end - start + 1) }, (_, index) => start + index)
})

const indexPageRange = computed(() => {
  const start = Math.max(1, indexPager.page - 2)
  const end = Math.min(indexPager.totalPages, start + 4)
  return Array.from({ length: Math.max(0, end - start + 1) }, (_, index) => start + index)
})

const indexDataPageRange = computed(() => {
  const start = Math.max(1, indexDataPager.page - 2)
  const end = Math.min(indexDataPager.totalPages, start + 4)
  return Array.from({ length: Math.max(0, end - start + 1) }, (_, index) => start + index)
})

async function api(url, options) {
  const response = await fetch(url, options)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.message || '请求失败，请稍后重试。')
  return data
}

function switchPage(page) {
  activePage.value = page
  if (page === 'indexStocks') loadIndexStocks(1)
  if (page === 'indexData') loadIndexData(1)
}

async function loadIndexData(page = 1) {
  indexDataLoading.value = true
  indexDataError.value = ''
  indexDataSync.message = ''
  const params = new URLSearchParams({ index_code: indexDataFilters.indexCode, page })
  try {
    const data = await api(`/api/index-data?${params}`)
    indexDataRows.value = data.items
    Object.assign(indexDataPager, {
      page: data.page, pageSize: data.page_size, total: data.total, totalPages: data.total_pages,
    })
  } catch (err) {
    indexDataRows.value = []
    indexDataError.value = err.message
  } finally {
    indexDataLoading.value = false
  }
}

async function syncIndexData() {
  indexDataSync.active = true
  indexDataSync.success = false
  indexDataSync.message = '正在同步沪深300指数指标…'
  indexDataError.value = ''
  try {
    const result = await api('/api/index-data/sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index_code: indexDataFilters.indexCode }),
    })
    indexDataSync.success = true
    await loadIndexData(1)
    indexDataSync.message = result.message
  } catch (err) {
    indexDataSync.message = ''
    indexDataError.value = `同步失败：${err.message}`
  } finally {
    indexDataSync.active = false
  }
}

async function loadIndexStocks(page = 1) {
  indexLoading.value = true
  indexError.value = ''
  indexSync.message = ''
  const params = new URLSearchParams({ index_code: indexFilters.indexCode, page })
  if (indexFilters.stockCode.trim()) params.set('stock_code', indexFilters.stockCode.trim())
  try {
    const data = await api(`/api/index-stocks?${params}`)
    indexRows.value = data.items
    Object.assign(indexPager, {
      page: data.page, pageSize: data.page_size, total: data.total, totalPages: data.total_pages,
    })
  } catch (err) {
    indexRows.value = []
    indexError.value = err.message
  } finally {
    indexLoading.value = false
  }
}

function resetIndexFilters() {
  Object.assign(indexFilters, { indexCode: '000300', stockCode: '' })
  loadIndexStocks(1)
}

async function syncIndexStocks() {
  indexSync.active = true
  indexSync.success = false
  indexSync.message = '正在获取并重建指数成分股…'
  indexError.value = ''
  try {
    const result = await api('/api/index-stocks/sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index_code: indexFilters.indexCode }),
    })
    indexSync.success = true
    await loadIndexStocks(1)
    indexSync.message = result.message
  } catch (err) {
    indexSync.message = ''
    indexError.value = `同步失败：${err.message}`
  } finally {
    indexSync.active = false
  }
}

function formatDateTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (!Number.isNaN(date.getTime())) {
    return date.toLocaleString('zh-CN', { hour12: false })
  }
  return String(value).replace('T', ' ').replace(/\.\d+$/, '')
}

async function load(page = 1) {
  loading.value = true
  error.value = ''
  const params = new URLSearchParams({ page, page_size: pager.pageSize })
  if (filters.exchange) params.set('exchange', filters.exchange)
  if (filters.code.trim()) params.set('code', filters.code.trim())
  if (filters.name.trim()) params.set('name', filters.name.trim())
  try {
    const data = await api(`/api/stock-basic?${params}`)
    rows.value = data.items
    Object.assign(pager, {
      page: data.page,
      pageSize: data.page_size,
      total: data.total,
      totalPages: data.total_pages,
    })
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

function reset() {
  Object.assign(filters, { exchange: '', code: '', name: '' })
  load(1)
}

async function showDetail(tsCode) {
  detailLoading.value = true
  selected.value = {}
  try {
    selected.value = await api(`/api/stock-basic/${encodeURIComponent(tsCode)}`)
  } catch (err) {
    selected.value = null
    error.value = err.message
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  selected.value = null
}

function localDate(date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function openBars(row) {
  const { start, end } = recentMonthRange()
  Object.assign(barStock, { tsCode: row.ts_code, name: row.name })
  Object.assign(barFilters, { startDate: localDate(start), endDate: localDate(end) })
  Object.assign(barPager, { page: 1, pageSize: 20, total: 0, totalPages: 1 })
  Object.assign(barSync, { active: false, message: '', success: false })
  barsVisible.value = true
  loadBars(1)
}

function recentMonthRange() {
  const end = new Date()
  const start = new Date(end)
  const originalDay = start.getDate()
  start.setDate(1)
  start.setMonth(start.getMonth() - 1)
  const lastDayOfTargetMonth = new Date(start.getFullYear(), start.getMonth() + 1, 0).getDate()
  start.setDate(Math.min(originalDay, lastDayOfTargetMonth))
  return { start, end }
}

function recentDaysRange(days) {
  const end = new Date()
  const start = new Date(end)
  start.setDate(start.getDate() - (days - 1))
  return { start, end }
}

function closeBars() {
  barsVisible.value = false
}

async function loadBars(page = 1) {
  if (!barFilters.startDate || !barFilters.endDate) {
    barSync.message = '请选择完整的日期范围。'
    barSync.success = false
    return
  }
  barsLoading.value = true
  barSync.message = ''
  const params = new URLSearchParams({
    start_date: barFilters.startDate,
    end_date: barFilters.endDate,
    page,
    page_size: barPager.pageSize,
  })
  try {
    const data = await api(`/api/stock-basic/${encodeURIComponent(barStock.tsCode)}/bars?${params}`)
    barRows.value = data.items
    Object.assign(barPager, {
      page: data.page, pageSize: data.page_size, total: data.total, totalPages: data.total_pages,
    })
  } catch (err) {
    barRows.value = []
    barSync.message = err.message
    barSync.success = false
  } finally {
    barsLoading.value = false
  }
}

function changeBarPageSize() {
  loadBars(1)
}

async function syncBars() {
  barSync.active = true
  barSync.message = '正在同步当前日期范围的行情数据…'
  barSync.success = false
  try {
    const result = await api(`/api/stock-basic/${encodeURIComponent(barStock.tsCode)}/bars/sync`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ start_date: barFilters.startDate, end_date: barFilters.endDate }),
    })
    barSync.message = result.message
    barSync.success = true
    await loadBars(1)
    barSync.message = result.message
  } catch (err) {
    barSync.message = `同步失败：${err.message}`
  } finally {
    barSync.active = false
  }
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: digits })
}

function openMetrics(row) {
  const { start, end } = recentMonthRange()
  metricContext.value = 'stock'
  Object.assign(metricStock, { tsCode: row.ts_code, name: row.name })
  Object.assign(metricFilters, { startDate: localDate(start), endDate: localDate(end) })
  Object.assign(metricPager, { page: 1, pageSize: 20, total: 0, totalPages: 1 })
  Object.assign(metricCalculation, { active: false, message: '', success: false })
  metricsVisible.value = true
  loadMetrics(1)
}

function openIndexMetrics(row) {
  const { start, end } = recentDaysRange(30)
  metricContext.value = 'index'
  Object.assign(metricStock, { tsCode: row.stock_code, name: row.stock_name })
  Object.assign(metricFilters, { startDate: localDate(start), endDate: localDate(end) })
  Object.assign(metricPager, { page: 1, pageSize: 20, total: 0, totalPages: 1 })
  Object.assign(metricCalculation, { active: false, message: '', success: false })
  metricsVisible.value = true
  loadMetrics(1)
}

function closeMetrics() {
  metricsVisible.value = false
}

async function loadMetrics(page = 1) {
  if (!metricFilters.startDate || !metricFilters.endDate) {
    Object.assign(metricCalculation, { message: '请选择完整的日期范围。', success: false })
    return
  }
  metricsLoading.value = true
  metricCalculation.message = ''
  const params = new URLSearchParams({
    start_date: metricFilters.startDate,
    end_date: metricFilters.endDate,
    page,
    page_size: metricPager.pageSize,
  })
  try {
    const data = await api(`/api/stock-basic/${encodeURIComponent(metricStock.tsCode)}/daily-basic?${params}`)
    metricRows.value = data.items
    Object.assign(metricPager, {
      page: data.page, pageSize: data.page_size, total: data.total, totalPages: data.total_pages,
    })
  } catch (err) {
    metricRows.value = []
    Object.assign(metricCalculation, { message: err.message, success: false })
  } finally {
    metricsLoading.value = false
  }
}

function changeMetricPageSize() {
  loadMetrics(1)
}

async function calculateMetrics() {
  metricCalculation.active = true
  metricCalculation.message = '正在计算当前日期范围的每日指标…'
  metricCalculation.success = false
  try {
    const result = await api(`/api/stock-basic/${encodeURIComponent(metricStock.tsCode)}/daily-basic/calculate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ start_date: metricFilters.startDate, end_date: metricFilters.endDate }),
    })
    metricCalculation.success = true
    await loadMetrics(1)
    metricCalculation.message = result.message
  } catch (err) {
    metricCalculation.message = `计算失败：${err.message}`
  } finally {
    metricCalculation.active = false
  }
}

function fieldLabel(key) {
  return ({
    ts_code: '股票代码', symbol: '证券代码', name: '股票名称', area: '所属地区',
    industry: '所属行业', fullname: '公司全称', enname: '英文名称', cnspell: '拼音缩写',
    market: '市场板块', exchange: '交易所', curr_type: '交易币种', list_status: '上市状态',
    list_date: '上市日期', delist_date: '退市日期', is_hs: '沪深港通',
    act_name: '实控人', act_ent_type: '实控人类型',
  })[key] || key
}

function exchangeName(value) {
  return value === 'SH' ? '沪市' : value === 'SZ' ? '深市' : value || '—'
}

async function startSync() {
  sync.active = true
  sync.progress = 2
  sync.status = 'pending'
  sync.message = '正在创建同步任务…'
  try {
    const job = await api('/api/stock-basic/sync', { method: 'POST' })
    pollSync(job.job_id)
  } catch (err) {
    sync.active = false
    error.value = err.message
  }
}

async function pollSync(jobId) {
  try {
    const job = await api(`/api/stock-basic/sync/${jobId}`)
    Object.assign(sync, { progress: job.progress, message: job.message, status: job.status })
    if (job.status === 'completed') {
      setTimeout(() => { sync.active = false }, 1800)
      load(1)
    } else if (job.status === 'failed') {
      sync.active = false
      error.value = `同步失败：${job.message}`
    } else {
      setTimeout(() => pollSync(jobId), 800)
    }
  } catch (err) {
    sync.active = false
    error.value = err.message
  }
}

onMounted(() => load())
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand"><span class="brand-mark">Q</span><span>OpenQuant</span></div>
      <nav>
        <div class="nav-caption">数据管理</div>
        <button class="nav-item" :class="{ active: activePage === 'stocks' }" @click="switchPage('stocks')"><span>▦</span>股票基础信息</button>
        <button class="nav-item" :class="{ active: activePage === 'indexStocks' }" @click="switchPage('indexStocks')"><span>◫</span>指数股票信息</button>
        <button class="nav-item" :class="{ active: activePage === 'indexData' }" @click="switchPage('indexData')"><span>⌁</span>指标行情信息</button>
      </nav>
      <div class="db-card">
        <span class="status-dot"></span>
        <div><strong>DuckDB</strong><small>data.db</small></div>
      </div>
    </aside>

    <main v-if="activePage === 'stocks'">
      <header>
        <div><div class="eyebrow">DATA MAINTENANCE</div><h1>股票基础信息</h1></div>
        <button class="sync-button" :disabled="sync.active" @click="startSync">
          <span :class="{ spinning: sync.active }">↻</span>{{ sync.active ? '同步中' : '同步最新数据' }}
        </button>
      </header>

      <section v-if="sync.active" class="sync-panel">
        <div class="sync-copy"><strong>{{ sync.message }}</strong><span>{{ sync.progress }}%</span></div>
        <div class="progress"><div :style="{ width: `${sync.progress}%` }"></div></div>
      </section>

      <section class="filter-card">
        <div class="field"><label>交易所</label><select v-model="filters.exchange"><option value="">全部市场</option><option value="SH">沪市</option><option value="SZ">深市</option></select></div>
        <div class="field"><label>股票代码</label><input v-model="filters.code" placeholder="如 600519" @keyup.enter="load(1)" /></div>
        <div class="field"><label>股票名称</label><input v-model="filters.name" placeholder="请输入股票名称" @keyup.enter="load(1)" /></div>
        <button class="primary" @click="load(1)">查询</button>
        <button class="ghost" @click="reset">重置</button>
      </section>

      <div v-if="error" class="alert"><span>!</span>{{ error }}<button @click="error = ''">×</button></div>

      <section class="table-card">
        <div class="table-meta"><div><strong>股票列表</strong><span>共 {{ pager.total.toLocaleString() }} 条记录</span></div></div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>股票代码</th><th>股票名称</th><th>交易所</th><th>市场板块</th><th>所属行业</th><th>所属地区</th><th>上市日期</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-if="loading"><td colspan="8"><div class="loading-line">正在读取数据…</div></td></tr>
              <tr v-else-if="!rows.length"><td colspan="8"><div class="empty">没有找到符合条件的股票</div></td></tr>
              <tr v-for="row in rows" :key="row.ts_code">
                <td><span class="code">{{ row.ts_code }}</span></td><td class="name">{{ row.name || '—' }}</td>
                <td><span class="tag">{{ exchangeName(row.exchange) }}</span></td><td>{{ row.market || '—' }}</td>
                <td>{{ row.industry || '—' }}</td><td>{{ row.area || '—' }}</td><td>{{ row.list_date || '—' }}</td>
                <td><div class="row-actions"><button class="link" @click="showDetail(row.ts_code)">详情</button><button class="bar-link" @click="openBars(row)">日行情</button><button class="metric-link" @click="openMetrics(row)">日指标</button></div></td>
              </tr>
            </tbody>
          </table>
        </div>
        <footer class="pagination">
          <span>第 {{ pager.page }} / {{ pager.totalPages }} 页</span>
          <div><button :disabled="pager.page === 1" @click="load(pager.page - 1)">‹</button><button v-for="page in pageRange" :key="page" :class="{ current: page === pager.page }" @click="load(page)">{{ page }}</button><button :disabled="pager.page === pager.totalPages" @click="load(pager.page + 1)">›</button></div>
        </footer>
      </section>
    </main>

    <main v-else-if="activePage === 'indexStocks'" class="index-main">
      <header>
        <div><div class="eyebrow">INDEX CONSTITUENTS</div><h1>指数股票信息</h1></div>
        <button class="sync-button" :disabled="indexSync.active" @click="syncIndexStocks">
          <span :class="{ spinning: indexSync.active }">↻</span>{{ indexSync.active ? '同步中…' : '同步股票code' }}
        </button>
      </header>

      <section class="filter-card index-filter-card">
        <div class="field"><label>指数类型</label><select v-model="indexFilters.indexCode"><option value="000300">沪深300</option></select></div>
        <div class="field"><label>股票code</label><input v-model="indexFilters.stockCode" placeholder="如 600519.SH" @keyup.enter="loadIndexStocks(1)" /></div>
        <button class="primary" @click="loadIndexStocks(1)">查询</button>
        <button class="ghost" @click="resetIndexFilters">重置</button>
      </section>

      <div v-if="indexError" class="alert"><span>!</span>{{ indexError }}<button @click="indexError = ''">×</button></div>
      <div v-if="indexSync.message" class="bar-message index-message" :class="{ success: indexSync.success }">{{ indexSync.message }}</div>

      <section class="table-card">
        <div class="table-meta"><div><strong>指数成分股</strong><span>共 {{ indexPager.total.toLocaleString() }} 条记录</span></div></div>
        <div class="table-wrap">
          <table class="index-table">
            <thead><tr><th>ID</th><th>指数类型</th><th>指数code</th><th>股票code</th><th>股票名称</th><th>创建时间</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-if="indexLoading"><td colspan="7"><div class="loading-line">正在读取指数成分股…</div></td></tr>
              <tr v-else-if="!indexRows.length"><td colspan="7"><div class="empty">暂无数据，请先同步指数成分股</div></td></tr>
              <tr v-for="item in indexRows" :key="item.id">
                <td>{{ item.id }}</td><td><span class="index-badge">沪深300</span></td><td><span class="code">{{ item.index_code }}</span></td>
                <td><span class="code">{{ item.stock_code }}</span></td><td class="name">{{ item.stock_name || '—' }}</td><td>{{ formatDateTime(item.created_at) }}</td>
                <td><button class="index-metric-button" @click="openIndexMetrics(item)">日指标</button></td>
              </tr>
            </tbody>
          </table>
        </div>
        <footer class="pagination">
          <span>第 {{ indexPager.page }} / {{ indexPager.totalPages }} 页，每页 20 条</span>
          <div><button :disabled="indexPager.page === 1" @click="loadIndexStocks(indexPager.page - 1)">‹</button><button v-for="page in indexPageRange" :key="page" :class="{ current: page === indexPager.page }" @click="loadIndexStocks(page)">{{ page }}</button><button :disabled="indexPager.page === indexPager.totalPages" @click="loadIndexStocks(indexPager.page + 1)">›</button></div>
        </footer>
      </section>
    </main>

    <main v-else class="index-data-main">
      <header>
        <div><div class="eyebrow">INDEX DAILY DATA</div><h1>指标行情信息</h1></div>
        <button class="sync-button" :disabled="indexDataSync.active" @click="syncIndexData">
          <span :class="{ spinning: indexDataSync.active }">↻</span>{{ indexDataSync.active ? '同步中…' : '同步指标' }}
        </button>
      </header>

      <section class="filter-card index-data-filter-card">
        <div class="field"><label>指数类型</label><select v-model="indexDataFilters.indexCode"><option value="000300.SH">沪深300</option></select></div>
        <button class="primary" @click="loadIndexData(1)">查询</button>
      </section>

      <div v-if="indexDataError" class="alert"><span>!</span>{{ indexDataError }}<button @click="indexDataError = ''">×</button></div>
      <div v-if="indexDataSync.message" class="bar-message index-message" :class="{ success: indexDataSync.success }">{{ indexDataSync.message }}</div>

      <section class="table-card">
        <div class="table-meta"><div><strong>沪深300每日指标</strong><span>共 {{ indexDataPager.total.toLocaleString() }} 条记录</span></div></div>
        <div class="table-wrap">
          <table class="index-data-table">
            <thead><tr><th>指数代码</th><th>交易日期</th><th>开盘</th><th>最高</th><th>最低</th><th>收盘</th><th>昨收</th><th>涨跌额</th><th>涨跌幅</th><th>成交量</th><th>成交额</th></tr></thead>
            <tbody>
              <tr v-if="indexDataLoading"><td colspan="11"><div class="loading-line">正在读取指数指标…</div></td></tr>
              <tr v-else-if="!indexDataRows.length"><td colspan="11"><div class="empty">暂无指数指标数据</div></td></tr>
              <tr v-for="item in indexDataRows" :key="`${item.ts_code}-${item.trade_date}`">
                <td><span class="code">{{ item.ts_code }}</span></td><td><span class="code">{{ item.trade_date }}</span></td>
                <td>{{ formatNumber(item.open) }}</td><td>{{ formatNumber(item.high) }}</td><td>{{ formatNumber(item.low) }}</td><td class="price">{{ formatNumber(item.close) }}</td>
                <td>{{ formatNumber(item.pre_close) }}</td><td :class="item.change > 0 ? 'up' : item.change < 0 ? 'down' : ''">{{ formatNumber(item.change) }}</td>
                <td :class="item.pct_chg > 0 ? 'up' : item.pct_chg < 0 ? 'down' : ''">{{ formatNumber(item.pct_chg) }}%</td>
                <td>{{ formatNumber(item.vol) }}</td><td>{{ formatNumber(item.amount) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <footer class="pagination">
          <span>第 {{ indexDataPager.page }} / {{ indexDataPager.totalPages }} 页，每页 20 条</span>
          <div><button :disabled="indexDataPager.page === 1" @click="loadIndexData(indexDataPager.page - 1)">‹</button><button v-for="page in indexDataPageRange" :key="page" :class="{ current: page === indexDataPager.page }" @click="loadIndexData(page)">{{ page }}</button><button :disabled="indexDataPager.page === indexDataPager.totalPages" @click="loadIndexData(indexDataPager.page + 1)">›</button></div>
        </footer>
      </section>
    </main>

    <div v-if="selected" class="overlay" @click.self="closeDetail">
      <aside class="drawer">
        <div class="drawer-head"><div><span>股票档案</span><h2>{{ selected.name || selected.ts_code || '加载中…' }}</h2></div><button @click="closeDetail">×</button></div>
        <div v-if="detailLoading" class="detail-loading">正在加载详情…</div>
        <div v-else class="detail-grid"><div v-for="(value, key) in selected" :key="key" class="detail-item"><label>{{ fieldLabel(key) }}</label><span>{{ value || '—' }}</span></div></div>
      </aside>
    </div>

    <div v-if="barsVisible" class="overlay modal-overlay" @click.self="closeBars">
      <section class="bars-modal">
        <div class="bars-head">
          <div><span>DAILY MARKET DATA</span><h2>{{ barStock.name }} <small>{{ barStock.tsCode }}</small></h2></div>
          <button class="close-button" @click="closeBars">×</button>
        </div>

        <div class="bars-toolbar">
          <div class="field"><label>开始日期</label><input v-model="barFilters.startDate" type="date" /></div>
          <div class="date-separator">—</div>
          <div class="field"><label>结束日期</label><input v-model="barFilters.endDate" type="date" /></div>
          <button class="primary" :disabled="barsLoading" @click="loadBars(1)">查询</button>
          <button class="sync-button bar-sync-button" :disabled="barSync.active" @click="syncBars">
            <span :class="{ spinning: barSync.active }">↻</span>{{ barSync.active ? '同步中…' : '同步行情数据' }}
          </button>
        </div>

        <div v-if="barSync.message" class="bar-message" :class="{ success: barSync.success }">{{ barSync.message }}</div>

        <div class="bars-table-wrap">
          <table class="bars-table">
            <thead><tr><th>交易日期</th><th>开盘</th><th>最高</th><th>最低</th><th>收盘</th><th>昨收</th><th>涨跌额</th><th>涨跌幅</th><th>成交量</th><th>成交额</th></tr></thead>
            <tbody>
              <tr v-if="barsLoading"><td colspan="10"><div class="loading-line">正在读取日行情…</div></td></tr>
              <tr v-else-if="!barRows.length"><td colspan="10"><div class="empty">该日期范围暂无行情数据</div></td></tr>
              <tr v-for="item in barRows" :key="item.trade_date">
                <td><span class="code">{{ item.trade_date }}</span></td>
                <td>{{ formatNumber(item.open) }}</td><td>{{ formatNumber(item.high) }}</td><td>{{ formatNumber(item.low) }}</td>
                <td class="price">{{ formatNumber(item.close) }}</td><td>{{ formatNumber(item.pre_close) }}</td>
                <td :class="item.change > 0 ? 'up' : item.change < 0 ? 'down' : ''">{{ formatNumber(item.change) }}</td>
                <td :class="item.pct_chg > 0 ? 'up' : item.pct_chg < 0 ? 'down' : ''">{{ formatNumber(item.pct_chg) }}%</td>
                <td>{{ formatNumber(item.vol) }}</td><td>{{ formatNumber(item.amount) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <footer class="pagination bars-pagination">
          <div class="page-size"><span>每页</span><select v-model.number="barPager.pageSize" @change="changeBarPageSize"><option :value="20">20</option><option :value="30">30</option><option :value="50">50</option><option :value="100">100</option></select><span>条，共 {{ barPager.total }} 条</span></div>
          <div><button :disabled="barPager.page === 1" @click="loadBars(barPager.page - 1)">‹</button><button v-for="page in barPageRange" :key="page" :class="{ current: page === barPager.page }" @click="loadBars(page)">{{ page }}</button><button :disabled="barPager.page === barPager.totalPages" @click="loadBars(barPager.page + 1)">›</button></div>
        </footer>
      </section>
    </div>

    <div v-if="metricsVisible" class="overlay modal-overlay" @click.self="closeMetrics">
      <section class="bars-modal metrics-modal">
        <div class="bars-head">
          <div><span>{{ metricContext === 'index' ? 'INDEX CONSTITUENT METRICS' : 'DAILY FUNDAMENTALS' }}</span><h2>{{ metricStock.name }} <small>{{ metricStock.tsCode }}</small></h2></div>
          <button class="close-button" @click="closeMetrics">×</button>
        </div>
        <div class="bars-toolbar">
          <div class="field"><label>开始日期</label><input v-model="metricFilters.startDate" type="date" /></div>
          <div class="date-separator">—</div>
          <div class="field"><label>结束日期</label><input v-model="metricFilters.endDate" type="date" /></div>
          <button class="primary" :disabled="metricsLoading" @click="loadMetrics(1)">查询</button>
          <button class="sync-button metric-calculate-button" :disabled="metricCalculation.active" @click="calculateMetrics">
            <span :class="{ spinning: metricCalculation.active }">⌁</span>{{ metricCalculation.active ? '计算中…' : (metricContext === 'index' ? '计算指数指标' : '计算指标') }}
          </button>
        </div>
        <div v-if="metricCalculation.message" class="bar-message" :class="{ success: metricCalculation.success }">{{ metricCalculation.message }}</div>
        <div class="bars-table-wrap">
          <table class="bars-table metrics-table">
            <thead><tr><th>交易日期</th><th>收盘价</th><th>换手率</th><th>自由流通换手率</th><th>量比</th><th>市盈率</th><th>市盈率TTM</th><th>市净率</th><th>市销率</th><th>市销率TTM</th><th>股息率</th><th>股息率TTM</th><th>总股本</th><th>流通股本</th><th>自由流通股本</th><th>总市值</th><th>流通市值</th></tr></thead>
            <tbody>
              <tr v-if="metricsLoading"><td colspan="17"><div class="loading-line">正在读取每日指标…</div></td></tr>
              <tr v-else-if="!metricRows.length"><td colspan="17"><div class="empty">该日期范围暂无每日指标</div></td></tr>
              <tr v-for="item in metricRows" :key="item.trade_date">
                <td><span class="code">{{ item.trade_date }}</span></td><td class="price">{{ formatNumber(item.close) }}</td>
                <td>{{ formatNumber(item.turnover_rate) }}</td><td>{{ formatNumber(item.turnover_rate_f) }}</td><td>{{ formatNumber(item.volume_ratio) }}</td>
                <td>{{ formatNumber(item.pe) }}</td><td>{{ formatNumber(item.pe_ttm) }}</td><td>{{ formatNumber(item.pb) }}</td>
                <td>{{ formatNumber(item.ps) }}</td><td>{{ formatNumber(item.ps_ttm) }}</td><td>{{ formatNumber(item.dv_ratio) }}</td><td>{{ formatNumber(item.dv_ttm) }}</td>
                <td>{{ formatNumber(item.total_share) }}</td><td>{{ formatNumber(item.float_share) }}</td><td>{{ formatNumber(item.free_share) }}</td>
                <td>{{ formatNumber(item.total_mv) }}</td><td>{{ formatNumber(item.circ_mv) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <footer class="pagination bars-pagination">
          <div class="page-size"><span>每页</span><select v-model.number="metricPager.pageSize" @change="changeMetricPageSize"><option :value="20">20</option><option :value="30">30</option><option :value="50">50</option><option :value="100">100</option></select><span>条，共 {{ metricPager.total }} 条</span></div>
          <div><button :disabled="metricPager.page === 1" @click="loadMetrics(metricPager.page - 1)">‹</button><button v-for="page in metricPageRange" :key="page" :class="{ current: page === metricPager.page }" @click="loadMetrics(page)">{{ page }}</button><button :disabled="metricPager.page === metricPager.totalPages" @click="loadMetrics(metricPager.page + 1)">›</button></div>
        </footer>
      </section>
    </div>
  </div>
</template>
