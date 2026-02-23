// GreenML Pipeline — Frontend logic
// -----------------------------------------------------------------------
// State
// -----------------------------------------------------------------------

let currentJobId = null;
let pollTimer    = null;
let startTime    = null;
let elapsedTimer = null;
let breakdownChart = null;
let sciChart = null;
let cachedBenchRows = [];
let activeTab = 'evaluations';
let activeDetailPair = null; // { module, dataset }

// Carbon intensity lookup
const ZONE_INTENSITY = {
  DE: 350, FR: 85, GB: 225, IE: 310, SE: 45, NO: 30, PL: 680,
  'US-CAL-CISO': 250, 'US-MISO': 530, 'US-NY-NYISO': 280,
  IN_SO: 760, CN: 620, 'AU-NSW': 750, WORLD: 475
};

// GPU embodied carbon + power
const GPU_TE = {
  'NVIDIA A100 80GB': 150000, 'NVIDIA L4': 100000, 'NVIDIA L40S': 120000,
  'NVIDIA RTX 4090': 85000, 'NVIDIA V100': 100000, 'NVIDIA T4': 70000
};
const GPU_POWER_KW = {
  'NVIDIA A100 80GB': 0.300, 'NVIDIA L4': 0.072, 'NVIDIA L40S': 0.350,
  'NVIDIA RTX 4090': 0.450, 'NVIDIA V100': 0.300, 'NVIDIA T4': 0.070
};

// -----------------------------------------------------------------------
// Initialise
// -----------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
  loadBenchmarkResults();

  // SCI param changes re-render detail if open
  for (const id of ['zone', 'gpu_type', 'lifespan_years', 'functional_units']) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.addEventListener('change', () => {
      if (id === 'zone') {
        const z = el.value;
        const i = ZONE_INTENSITY[z] ?? 475;
        document.getElementById('zone-hint').textContent = `${z}: ${i} gCO₂/kWh`;
      }
      if (id === 'gpu_type') {
        const te = GPU_TE[el.value] ?? 150000;
        document.getElementById('gpu-hint').textContent = `TE: ${te.toLocaleString()} gCO₂`;
      }
      if (activeDetailPair) {
        renderComparisonDetail(activeDetailPair.module, activeDetailPair.dataset, cachedBenchRows);
      }
    });
    el.addEventListener('input', () => {
      if (activeDetailPair) {
        renderComparisonDetail(activeDetailPair.module, activeDetailPair.dataset, cachedBenchRows);
      }
    });
  }
});

// -----------------------------------------------------------------------
// Tab switching
// -----------------------------------------------------------------------

function showTab(tab) {
  activeTab = tab;
  // Update tab buttons
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });
  // Show/hide tab content
  document.getElementById('tab-evaluations').style.display = tab === 'evaluations' ? 'block' : 'none';
  document.getElementById('tab-comparison').style.display = tab === 'comparison' ? 'block' : 'none';
}

// -----------------------------------------------------------------------
// Load benchmark results
// -----------------------------------------------------------------------

async function loadBenchmarkResults() {
  try {
    const res = await fetch('/api/benchmark_results');
    cachedBenchRows = await res.json();
    renderEvaluationsTab(cachedBenchRows);
    renderComparisonTab(cachedBenchRows);
  } catch (e) {
    console.error('Failed to load benchmark results:', e);
  }
}

// -----------------------------------------------------------------------
// Tab 1: Evaluations — job summary table grouped by job_name
// -----------------------------------------------------------------------

function renderEvaluationsTab(rows) {
  const container = document.getElementById('evaluations-content');

  if (!rows.length) {
    container.innerHTML = '<div class="text-xs text-gray-600 italic py-8 text-center">No benchmark results yet.</div>';
    return;
  }

  // Group by job_name
  const jobMap = {};
  for (const r of rows) {
    const key = r.job_name || 'unknown';
    if (!jobMap[key]) jobMap[key] = [];
    jobMap[key].push(r);
  }

  // Sort jobs by most recent row (highest id) desc
  const jobEntries = Object.entries(jobMap).sort((a, b) => {
    const maxA = Math.max(...a[1].map(r => r.id));
    const maxB = Math.max(...b[1].map(r => r.id));
    return maxB - maxA;
  });

  const tableRows = jobEntries.map(([jobName, jobRows]) => {
    const first = jobRows[0];
    const modules = [...new Set(jobRows.map(r => r.module))].join(', ');
    const datasets = [...new Set(jobRows.map(r => r.dataset))].join(', ');
    const variants = [...new Set(jobRows.map(r => r.variant))].sort().join(', ');
    const gpu = first.gpu || first.gpu_accelerator || '—';
    const date = fmtDate(first.created_at);
    const rowCount = jobRows.length;

    return `
      <tr class="hover:bg-surface-700/50 transition-colors">
        <td class="px-4 py-3 text-gray-200 font-mono text-xs">${escHtml(jobName)}</td>
        <td class="px-4 py-3 text-gray-300 text-xs uppercase">${escHtml(modules)}</td>
        <td class="px-4 py-3 text-xs">
          ${variants.split(', ').map(v => {
            const cls = v === 'optimized' ? 'text-brand-400' : 'text-blue-400';
            return `<span class="${cls}">${v}</span>`;
          }).join(', ')}
        </td>
        <td class="px-4 py-3 text-gray-400 text-xs">${escHtml(gpu)}</td>
        <td class="px-4 py-3 text-gray-400 text-xs">${escHtml(datasets)}</td>
        <td class="px-4 py-3 text-gray-500 text-xs">${date}</td>
        <td class="px-4 py-3 text-gray-500 text-xs text-center">${rowCount}</td>
      </tr>
    `;
  }).join('');

  container.innerHTML = `
    <div class="bg-surface-700 rounded-xl border border-surface-600 overflow-hidden">
      <div class="overflow-x-auto">
        <table class="w-full text-xs">
          <thead>
            <tr class="text-gray-500 border-b border-surface-600 bg-surface-800">
              <th class="px-4 py-2.5 text-left font-medium">Job</th>
              <th class="px-4 py-2.5 text-left font-medium">Model</th>
              <th class="px-4 py-2.5 text-left font-medium">Variants</th>
              <th class="px-4 py-2.5 text-left font-medium">GPU</th>
              <th class="px-4 py-2.5 text-left font-medium">Datasets</th>
              <th class="px-4 py-2.5 text-left font-medium">Date</th>
              <th class="px-4 py-2.5 text-center font-medium">Rows</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-surface-600">
            ${tableRows}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

// -----------------------------------------------------------------------
// Tab 2: Comparison — grid of (module, dataset) cards
// -----------------------------------------------------------------------

function renderComparisonTab(rows) {
  const grid = document.getElementById('comparison-grid');

  if (!rows.length) {
    grid.innerHTML = '<div class="text-xs text-gray-600 italic py-8 text-center col-span-full">No benchmark results yet.</div>';
    return;
  }

  // Extract unique (module, dataset) pairs
  const pairMap = {};
  for (const r of rows) {
    const key = `${r.module}|||${r.dataset}`;
    if (!pairMap[key]) pairMap[key] = { module: r.module, dataset: r.dataset, rows: [] };
    pairMap[key].rows.push(r);
  }

  const pairs = Object.values(pairMap).sort((a, b) =>
    a.module.localeCompare(b.module) || a.dataset.localeCompare(b.dataset)
  );

  grid.innerHTML = pairs.map(pair => {
    const { module, dataset, rows: pairRows } = pair;
    const variants = [...new Set(pairRows.map(r => r.variant))].sort();
    const gpus = [...new Set(pairRows.map(r => r.gpu_accelerator || r.gpu).filter(Boolean))];
    const jobCount = new Set(pairRows.map(r => r.job_name)).size;

    // Quick summary: latest baseline vs optimized throughput
    const latestBaseline = pairRows.filter(r => r.variant === 'baseline').sort((a, b) => b.id - a.id)[0];
    const latestOptimized = pairRows.filter(r => r.variant === 'optimized').sort((a, b) => b.id - a.id)[0];

    let speedupHtml = '';
    if (latestBaseline?.throughput && latestOptimized?.throughput) {
      const pct = ((latestOptimized.throughput / latestBaseline.throughput - 1) * 100).toFixed(1);
      speedupHtml = `<span class="text-brand-400 text-xs font-medium">+${pct}% throughput</span>`;
    }

    return `
      <div class="comparison-card bg-surface-700 rounded-xl p-5 border border-surface-600"
           onclick="openComparisonDetail('${escAttr(module)}', '${escAttr(dataset)}')">
        <div class="flex items-center justify-between mb-3">
          <span class="text-sm font-bold text-gray-200 uppercase">${escHtml(module)}</span>
          ${speedupHtml}
        </div>
        <p class="text-xs text-gray-400 font-mono mb-3">${escHtml(dataset)}</p>
        <div class="flex flex-wrap gap-2 text-xs">
          ${variants.map(v => {
            const cls = v === 'optimized' ? 'bg-brand-900/30 text-brand-400 border-brand-800' : 'bg-blue-900/20 text-blue-400 border-blue-800';
            return `<span class="px-2 py-0.5 rounded border ${cls}">${v}</span>`;
          }).join('')}
        </div>
        <div class="mt-3 flex items-center gap-3 text-xs text-gray-500">
          <span>${gpus.length} GPU type${gpus.length !== 1 ? 's' : ''}</span>
          <span>·</span>
          <span>${jobCount} job${jobCount !== 1 ? 's' : ''}</span>
          <span>·</span>
          <span>${pairRows.length} rows</span>
        </div>
      </div>
    `;
  }).join('');
}

// -----------------------------------------------------------------------
// Comparison detail — historical runs + SCI
// -----------------------------------------------------------------------

function openComparisonDetail(module, dataset) {
  activeDetailPair = { module, dataset };
  document.getElementById('comparison-grid').style.display = 'none';
  document.getElementById('comparison-detail').style.display = 'block';
  document.getElementById('detail-title').textContent = `${module.toUpperCase()} — ${dataset}`;
  renderComparisonDetail(module, dataset, cachedBenchRows);
}

function closeComparisonDetail() {
  activeDetailPair = null;
  document.getElementById('comparison-grid').style.display = 'grid';
  document.getElementById('comparison-detail').style.display = 'none';
  document.getElementById('sci-dashboard').style.display = 'none';
}

function renderComparisonDetail(module, dataset, allRows) {
  const rows = allRows.filter(r => r.module === module && r.dataset === dataset);
  const container = document.getElementById('detail-table-container');

  if (!rows.length) {
    container.innerHTML = '<div class="text-xs text-gray-600 italic py-4">No data for this pair.</div>';
    document.getElementById('sci-dashboard').style.display = 'none';
    return;
  }

  // Group by (gpu_accelerator, variant) — aggregate averages
  const groupMap = {};
  for (const r of rows) {
    const gpu = r.gpu_accelerator || r.gpu || '—';
    const key = `${gpu}|||${r.variant}`;
    if (!groupMap[key]) groupMap[key] = { gpu, variant: r.variant, rows: [] };
    groupMap[key].rows.push(r);
  }

  const groups = Object.values(groupMap).sort((a, b) =>
    a.gpu.localeCompare(b.gpu) || a.variant.localeCompare(b.variant)
  );

  // Build aggregated table
  const tableRows = groups.map(g => {
    const n = g.rows.length;
    const avg = (arr, fn) => arr.reduce((s, r) => s + (fn(r) || 0), 0) / n;
    const throughput = avg(g.rows, r => r.throughput);
    const latency = avg(g.rows, r => r.avg_latency_ms);
    const evalTime = avg(g.rows, r => r.eval_time_sec);
    const metric = avg(g.rows, r => r.metric_value);
    const latest = g.rows.sort((a, b) => b.id - a.id)[0];
    const variantCls = g.variant === 'optimized' ? 'text-brand-400' : 'text-blue-400';

    return `
      <tr class="hover:bg-surface-700/50 transition-colors">
        <td class="px-4 py-2.5 text-gray-300 text-xs">${escHtml(g.gpu)}</td>
        <td class="px-4 py-2.5 text-xs ${variantCls} font-medium">${g.variant}</td>
        <td class="px-4 py-2.5 text-gray-200 font-mono text-xs">${throughput.toFixed(2)} img/s</td>
        <td class="px-4 py-2.5 text-gray-200 font-mono text-xs">${latency.toFixed(1)} ms</td>
        <td class="px-4 py-2.5 text-gray-200 font-mono text-xs">${fmtDuration(evalTime)}</td>
        <td class="px-4 py-2.5 text-gray-200 font-mono text-xs">${metric.toFixed(4)}</td>
        <td class="px-4 py-2.5 text-gray-500 text-xs">${fmtDate(latest.created_at)}</td>
        <td class="px-4 py-2.5 text-gray-500 text-xs text-center">${n}</td>
      </tr>
    `;
  }).join('');

  // Get metric name
  const metricName = rows[0]?.metric_name || 'metric';

  container.innerHTML = `
    <div class="bg-surface-700 rounded-xl border border-surface-600 overflow-hidden">
      <div class="px-5 py-3 border-b border-surface-600 flex items-center justify-between">
        <span class="text-xs font-semibold text-gray-400 uppercase tracking-wider">Historical Runs (avg per group)</span>
        <span class="text-xs text-gray-600">${metricName.toUpperCase()}</span>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full text-xs">
          <thead>
            <tr class="text-gray-500 border-b border-surface-600 bg-surface-800">
              <th class="px-4 py-2.5 text-left font-medium">Instance Type</th>
              <th class="px-4 py-2.5 text-left font-medium">Variant</th>
              <th class="px-4 py-2.5 text-left font-medium">Throughput</th>
              <th class="px-4 py-2.5 text-left font-medium">Latency</th>
              <th class="px-4 py-2.5 text-left font-medium">Eval Time</th>
              <th class="px-4 py-2.5 text-left font-medium">${escHtml(metricName)}</th>
              <th class="px-4 py-2.5 text-left font-medium">Latest</th>
              <th class="px-4 py-2.5 text-center font-medium">N</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-surface-600">
            ${tableRows}
          </tbody>
        </table>
      </div>
    </div>
  `;

  // Compute SCI for aggregated baseline vs optimized (latest rows)
  computeAndRenderSCI(rows);
}

// -----------------------------------------------------------------------
// SCI computation from comparison detail
// -----------------------------------------------------------------------

function computeAndRenderSCI(rows) {
  // Use latest baseline and latest optimized row
  const baselineRows = rows.filter(r => r.variant === 'baseline').sort((a, b) => b.id - a.id);
  const optimizedRows = rows.filter(r => r.variant === 'optimized').sort((a, b) => b.id - a.id);

  if (!baselineRows.length || !optimizedRows.length) {
    document.getElementById('sci-dashboard').style.display = 'none';
    return;
  }

  const b = baselineRows[0];
  const o = optimizedRows[0];

  // Read SCI params
  const zone = document.getElementById('zone').value;
  const intensity = ZONE_INTENSITY[zone] ?? 475;
  const gpuType = document.getElementById('gpu_type').value;
  const te_gco2 = GPU_TE[gpuType] ?? 150000;
  const lifespan = parseFloat(document.getElementById('lifespan_years').value) || 4;
  const R = parseInt(document.getElementById('functional_units').value) || 10000;
  const powerKw = GPU_POWER_KW[gpuType] ?? 0.300;

  const bEvalSec = b.eval_time_sec || 0;
  const oEvalSec = o.eval_time_sec || 0;
  const bEnergyKwh = powerKw * (bEvalSec / 3600);
  const oEnergyKwh = powerKw * (oEvalSec / 3600);
  const bDurationH = bEvalSec / 3600;
  const oDurationH = oEvalSec / 3600;
  const bEmbodied = te_gco2 * (bDurationH / (lifespan * 8760));
  const oEmbodied = te_gco2 * (oDurationH / (lifespan * 8760));
  const bSci = (bEnergyKwh * intensity + bEmbodied) / R;
  const oSci = (oEnergyKwh * intensity + oEmbodied) / R;
  const bTotal = bEnergyKwh * intensity + bEmbodied;
  const oTotal = oEnergyKwh * intensity + oEmbodied;

  renderSciDashboard({
    baseline: { energy_kwh: bEnergyKwh, duration_h: bDurationH, embodied_gco2: bEmbodied, ei_gco2: bEnergyKwh * intensity, total_gco2: bTotal, sci: bSci },
    optimized: { energy_kwh: oEnergyKwh, duration_h: oDurationH, embodied_gco2: oEmbodied, ei_gco2: oEnergyKwh * intensity, total_gco2: oTotal, sci: oSci },
    reduction: {
      energy_pct: bEnergyKwh > 0 ? (bEnergyKwh - oEnergyKwh) / bEnergyKwh * 100 : 0,
      sci_pct: bSci > 0 ? (bSci - oSci) / bSci * 100 : 0,
      co2_saved_gco2: bTotal - oTotal,
    },
  });
}

// -----------------------------------------------------------------------
// Render SCI dashboard
// -----------------------------------------------------------------------

function renderSciDashboard(r) {
  document.getElementById('sci-dashboard').style.display = 'block';

  const b = r.baseline;
  const o = r.optimized;
  const red = r.reduction;

  document.getElementById('val-sci-pct').textContent    = red.sci_pct.toFixed(1) + '%';
  document.getElementById('val-energy-pct').textContent = red.energy_pct.toFixed(1) + '%';
  document.getElementById('val-co2-saved').textContent  = red.co2_saved_gco2.toFixed(3) + ' gCO₂';

  document.getElementById('val-baseline-sci').textContent = formatSci(b.sci);
  document.getElementById('val-baseline-e').textContent   = b.energy_kwh.toFixed(5) + ' kWh';
  document.getElementById('val-baseline-ei').textContent  = b.ei_gco2.toFixed(3) + ' gCO₂';
  document.getElementById('val-baseline-m').textContent   = b.embodied_gco2.toFixed(3) + ' gCO₂';
  document.getElementById('val-baseline-dur').textContent = (b.duration_h * 60).toFixed(1) + ' min';

  document.getElementById('val-opt-sci').textContent = formatSci(o.sci);
  document.getElementById('val-opt-e').textContent   = o.energy_kwh.toFixed(5) + ' kWh';
  document.getElementById('val-opt-ei').textContent  = o.ei_gco2.toFixed(3) + ' gCO₂';
  document.getElementById('val-opt-m').textContent   = o.embodied_gco2.toFixed(3) + ' gCO₂';
  document.getElementById('val-opt-dur').textContent = (o.duration_h * 60).toFixed(1) + ' min';

  renderBreakdownChart(b, o);
  renderSciChart(b.sci, o.sci);
}

function formatSci(val) {
  if (val < 0.001) return val.toExponential(3);
  return val.toFixed(6);
}

// -----------------------------------------------------------------------
// Charts
// -----------------------------------------------------------------------

function renderBreakdownChart(b, o) {
  const ctx = document.getElementById('chart-breakdown');
  if (breakdownChart) { breakdownChart.destroy(); breakdownChart = null; }
  breakdownChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: ['Operational (E×I)', 'Embodied (M)', 'Total gCO₂'],
      datasets: [
        { label: 'Baseline', data: [b.ei_gco2, b.embodied_gco2, b.total_gco2], backgroundColor: 'rgba(96,165,250,0.7)', borderColor: 'rgba(96,165,250,1)', borderWidth: 1, borderRadius: 4 },
        { label: 'Optimised', data: [o.ei_gco2, o.embodied_gco2, o.total_gco2], backgroundColor: 'rgba(74,222,128,0.7)', borderColor: 'rgba(74,222,128,1)', borderWidth: 1, borderRadius: 4 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#9ca3af', font: { size: 11 } } }, tooltip: { callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.raw.toFixed(4)} gCO₂` } } },
      scales: { x: { ticks: { color: '#6b7280', font: { size: 10 } }, grid: { color: '#2a2f3a' } }, y: { ticks: { color: '#6b7280', font: { size: 10 } }, grid: { color: '#2a2f3a' }, title: { display: true, text: 'gCO₂', color: '#6b7280', font: { size: 10 } } } },
    },
  });
}

function renderSciChart(bSci, oSci) {
  const ctx = document.getElementById('chart-sci');
  if (sciChart) { sciChart.destroy(); sciChart = null; }
  sciChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: ['Baseline', 'Optimised'],
      datasets: [{ label: 'SCI (gCO₂eq / inference)', data: [bSci, oSci], backgroundColor: ['rgba(96,165,250,0.7)', 'rgba(74,222,128,0.7)'], borderColor: ['rgba(96,165,250,1)', 'rgba(74,222,128,1)'], borderWidth: 1, borderRadius: 6 }],
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => ` SCI: ${ctx.raw.toExponential(4)} gCO₂/inf` } } },
      scales: { x: { ticks: { color: '#6b7280', font: { size: 10 } }, grid: { color: '#2a2f3a' }, title: { display: true, text: 'gCO₂eq / inference', color: '#6b7280', font: { size: 10 } } }, y: { ticks: { color: '#9ca3af', font: { size: 11 } }, grid: { color: '#2a2f3a' } } },
    },
  });
}

// -----------------------------------------------------------------------
// Pipeline / polling (kept for Start Pipeline button)
// -----------------------------------------------------------------------

async function startPipeline() {
  const repoUrl = document.getElementById('repo_url').value.trim();
  if (!repoUrl) { alert('Please enter a GitHub repository URL.'); return; }

  const payload = {
    repo_url: repoUrl,
    branch: document.getElementById('branch').value.trim() || 'main',
    zone: document.getElementById('zone').value,
    gpu_type: document.getElementById('gpu_type').value,
    lifespan_years: parseFloat(document.getElementById('lifespan_years').value),
    functional_units: parseInt(document.getElementById('functional_units').value),
  };

  setStatusBadge('running', 'Running');
  document.getElementById('btn-run').disabled = true;
  document.getElementById('btn-cancel').disabled = false;
  startTime = Date.now();
  elapsedTimer = setInterval(updateElapsed, 1000);

  try {
    const res = await fetch('/api/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Failed to start job');
    currentJobId = data.job_id;
    pollTimer = setInterval(pollJob, 1500);
  } catch (err) { alert('Error: ' + err.message); resetControls(); }
}

async function cancelPipeline() {
  if (!currentJobId) return;
  await fetch(`/api/jobs/${currentJobId}/cancel`, { method: 'POST' });
  stopPolling(); setStatusBadge('idle', 'Cancelled'); resetControls();
}

async function pollJob() {
  if (!currentJobId) return;
  try {
    const res = await fetch(`/api/jobs/${currentJobId}`);
    const job = await res.json();
    if (job.status === 'completed') { stopPolling(); setStatusBadge('done', 'Completed'); resetControls(); loadBenchmarkResults(); refreshHistory(); }
    else if (job.status === 'cancelled') { stopPolling(); resetControls(); }
  } catch (e) { console.error('Poll error:', e); }
}

// -----------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------

function fmtDate(d) {
  if (!d) return '—';
  const dt = new Date(d.replace(' ', 'T') + 'Z');
  return dt.toLocaleString('en-GB', { dateStyle: 'short', timeStyle: 'short' });
}

function fmtDuration(sec) {
  if (!sec) return '—';
  if (sec < 60) return sec.toFixed(1) + 's';
  const m = Math.floor(sec / 60);
  const s = (sec % 60).toFixed(0);
  return `${m}m ${s}s`;
}

function capitalise(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : '—'; }

function escHtml(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function escAttr(s) {
  return escHtml(s).replace(/'/g, '&#39;');
}

function setStatusBadge(state, label) {
  const el = document.getElementById('status-badge');
  el.textContent = label;
  el.className = 'px-3 py-1 rounded-full text-xs font-medium border ';
  if (state === 'running') el.className += 'bg-yellow-900/30 text-yellow-400 border-yellow-800 animate-pulse';
  else if (state === 'done') el.className += 'bg-brand-900/30 text-brand-400 border-brand-800';
  else el.className += 'bg-surface-600 text-gray-400 border-surface-500';
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
}

function resetControls() {
  document.getElementById('btn-run').disabled = false;
  document.getElementById('btn-cancel').disabled = true;
}

function updateElapsed() {
  if (!startTime) return;
  const el = document.getElementById('elapsed-time');
  if (!el) return;
  const s = Math.floor((Date.now() - startTime) / 1000);
  el.textContent = `Elapsed: ${Math.floor(s/60)}:${(s%60).toString().padStart(2,'0')}`;
}

async function refreshHistory() {
  try {
    const res = await fetch('/api/jobs');
    const jobs = await res.json();
    const el = document.getElementById('history-list');
    if (!jobs.length) { el.innerHTML = '<p class="italic">No jobs yet.</p>'; return; }
    el.innerHTML = jobs.slice(0, 10).map(j => `
      <div class="flex items-center gap-3 py-1">
        <span class="w-2 h-2 rounded-full ${j.status === 'completed' ? 'bg-brand-400' : j.status === 'running' ? 'bg-yellow-400' : 'bg-gray-600'}"></span>
        <span class="text-gray-300 font-mono truncate max-w-[200px]">${j.repo_url.replace('https://github.com/', '')}</span>
        <span class="text-gray-600 ml-auto">${capitalise(j.status)}</span>
      </div>
    `).join('');
  } catch (e) {}
}

function toggleHistory() {
  const panel = document.getElementById('history-panel');
  panel.classList.toggle('hidden');
  if (!panel.classList.contains('hidden')) refreshHistory();
}
