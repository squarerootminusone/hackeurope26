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
let selectedPipelineKey = null;

// Carbon intensity lookup (must mirror backend ZONE_INTENSITY)
const ZONE_INTENSITY = {
  DE: 350, FR: 85, GB: 225, IE: 310, SE: 45, NO: 30, PL: 680,
  'US-CAL-CISO': 250, 'US-MISO': 530, 'US-NY-NYISO': 280,
  IN_SO: 760, CN: 620, 'AU-NSW': 750, WORLD: 475
};

// GPU embodied carbon lookup
const GPU_TE = {
  'NVIDIA A100 80GB': 150000,
  'NVIDIA L4': 100000,
  'NVIDIA L40S': 120000,
  'NVIDIA RTX 4090': 85000,
  'NVIDIA V100': 100000,
  'NVIDIA T4': 70000
};

// -----------------------------------------------------------------------
// Initialise
// -----------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
  loadBenchmarkResults();

  document.getElementById('zone').addEventListener('change', function () {
    const z = this.value;
    const i = ZONE_INTENSITY[z] ?? 475;
    document.getElementById('zone-hint').textContent = `${z}: ${i} gCO₂/kWh`;
  });

  document.getElementById('gpu_type').addEventListener('change', function () {
    const te = GPU_TE[this.value] ?? 150000;
    document.getElementById('gpu-hint').textContent = `TE: ${te.toLocaleString()} gCO₂`;
  });
});

// -----------------------------------------------------------------------
// Start pipeline
// -----------------------------------------------------------------------

async function startPipeline() {
  const repoUrl = document.getElementById('repo_url').value.trim();
  if (!repoUrl) {
    alert('Please enter a GitHub repository URL.');
    return;
  }

  const payload = {
    repo_url:         repoUrl,
    branch:           document.getElementById('branch').value.trim() || 'main',
    zone:             document.getElementById('zone').value,
    gpu_type:         document.getElementById('gpu_type').value,
    lifespan_years:   parseFloat(document.getElementById('lifespan_years').value),
    functional_units: parseInt(document.getElementById('functional_units').value),
    api_key:          document.getElementById('api_key').value.trim(),
  };

  setStatusBadge('running', 'Running');
  document.getElementById('btn-run').disabled = true;
  document.getElementById('btn-cancel').disabled = false;

  startTime = Date.now();
  elapsedTimer = setInterval(updateElapsed, 1000);

  try {
    const res = await fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Failed to start job');

    currentJobId = data.job_id;
    pollTimer = setInterval(pollJob, 1500);
  } catch (err) {
    alert('Error: ' + err.message);
    resetControls();
  }
}

// -----------------------------------------------------------------------
// Cancel pipeline
// -----------------------------------------------------------------------

async function cancelPipeline() {
  if (!currentJobId) return;
  await fetch(`/api/jobs/${currentJobId}/cancel`, { method: 'POST' });
  stopPolling();
  setStatusBadge('idle', 'Cancelled');
  resetControls();
}

// -----------------------------------------------------------------------
// Poll job status
// -----------------------------------------------------------------------

async function pollJob() {
  if (!currentJobId) return;
  try {
    const res = await fetch(`/api/jobs/${currentJobId}`);
    const job = await res.json();

    if (job.status === 'completed') {
      stopPolling();
      setStatusBadge('done', 'Completed');
      resetControls();
      if (job.sci_results) renderDashboard(job.sci_results);
      loadBenchmarkResults();
      refreshHistory();
    } else if (job.status === 'cancelled') {
      stopPolling();
      resetControls();
    }
  } catch (e) {
    console.error('Poll error:', e);
  }
}

// -----------------------------------------------------------------------
// Render SCI dashboard
// -----------------------------------------------------------------------

function renderDashboard(r) {
  document.getElementById('sci-dashboard').style.display = 'block';

  const b = r.baseline;
  const o = r.optimized;
  const red = r.reduction;

  // Badges
  document.getElementById('val-sci-pct').textContent    = red.sci_pct.toFixed(1) + '%';
  document.getElementById('val-energy-pct').textContent = red.energy_pct.toFixed(1) + '%';
  document.getElementById('val-co2-saved').textContent  = red.co2_saved_gco2.toFixed(3) + ' gCO₂';

  // Baseline card
  document.getElementById('val-baseline-sci').textContent = formatSci(b.sci);
  document.getElementById('val-baseline-e').textContent   = b.energy_kwh.toFixed(5) + ' kWh';
  document.getElementById('val-baseline-ei').textContent  = b.ei_gco2.toFixed(3) + ' gCO₂';
  document.getElementById('val-baseline-m').textContent   = b.embodied_gco2.toFixed(3) + ' gCO₂';
  document.getElementById('val-baseline-dur').textContent = (b.duration_h * 60).toFixed(1) + ' min';

  // Optimised card
  document.getElementById('val-opt-sci').textContent = formatSci(o.sci);
  document.getElementById('val-opt-e').textContent   = o.energy_kwh.toFixed(5) + ' kWh';
  document.getElementById('val-opt-ei').textContent  = o.ei_gco2.toFixed(3) + ' gCO₂';
  document.getElementById('val-opt-m').textContent   = o.embodied_gco2.toFixed(3) + ' gCO₂';
  document.getElementById('val-opt-dur').textContent = (o.duration_h * 60).toFixed(1) + ' min';

  // SCI params table
  const paramRows = [
    ['Zone (I source)', r.zone],
    ['Carbon Intensity (I)', `${r.intensity} gCO₂/kWh`],
    ['GPU Type', r.gpu_type],
    ['Embodied Carbon (TE)', `${r.te_gco2.toLocaleString()} gCO₂`],
    ['Hardware Lifespan', `${r.lifespan_years} years`],
    ['Functional Units (R)', r.functional_units.toLocaleString()],
  ];
  document.getElementById('params-table').innerHTML = paramRows.map(([k, v]) => `
    <tr>
      <td class="py-2 pr-4 text-gray-500 font-medium">${k}</td>
      <td class="py-2 text-gray-200 font-mono">${v}</td>
    </tr>
  `).join('');

  // Optimisations list
  document.getElementById('opts-list').innerHTML = r.optimisations_applied.map(opt => `
    <li class="flex items-start gap-2">
      <span class="text-brand-400 mt-0.5">✓</span>
      <span>${opt}</span>
    </li>
  `).join('');

  // Charts
  renderBreakdownChart(b, o);
  renderSciChart(b.sci, o.sci);
}

function formatSci(val) {
  if (val < 0.001) return val.toExponential(3);
  return val.toFixed(6);
}

// -----------------------------------------------------------------------
// Chart: SCI breakdown (grouped bar — E*I and M)
// -----------------------------------------------------------------------

function renderBreakdownChart(b, o) {
  const ctx = document.getElementById('chart-breakdown');
  if (breakdownChart) { breakdownChart.destroy(); breakdownChart = null; }

  breakdownChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: ['Operational (E×I)', 'Embodied (M)', 'Total gCO₂'],
      datasets: [
        {
          label: 'Baseline',
          data: [b.ei_gco2, b.embodied_gco2, b.total_gco2],
          backgroundColor: 'rgba(96,165,250,0.7)',
          borderColor: 'rgba(96,165,250,1)',
          borderWidth: 1,
          borderRadius: 4,
        },
        {
          label: 'Optimised',
          data: [o.ei_gco2, o.embodied_gco2, o.total_gco2],
          backgroundColor: 'rgba(74,222,128,0.7)',
          borderColor: 'rgba(74,222,128,1)',
          borderWidth: 1,
          borderRadius: 4,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: '#9ca3af', font: { size: 11 } } },
        tooltip: {
          callbacks: {
            label: ctx => ` ${ctx.dataset.label}: ${ctx.raw.toFixed(4)} gCO₂`
          }
        }
      },
      scales: {
        x: { ticks: { color: '#6b7280', font: { size: 10 } }, grid: { color: '#1e2d21' } },
        y: {
          ticks: { color: '#6b7280', font: { size: 10 } },
          grid: { color: '#1e2d21' },
          title: { display: true, text: 'gCO₂', color: '#6b7280', font: { size: 10 } }
        },
      },
    },
  });
}

// -----------------------------------------------------------------------
// Chart: SCI score comparison (horizontal bar)
// -----------------------------------------------------------------------

function renderSciChart(bSci, oSci) {
  const ctx = document.getElementById('chart-sci');
  if (sciChart) { sciChart.destroy(); sciChart = null; }

  sciChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: ['Baseline', 'Optimised'],
      datasets: [{
        label: 'SCI (gCO₂eq / inference)',
        data: [bSci, oSci],
        backgroundColor: ['rgba(96,165,250,0.7)', 'rgba(74,222,128,0.7)'],
        borderColor:     ['rgba(96,165,250,1)',   'rgba(74,222,128,1)'],
        borderWidth: 1,
        borderRadius: 6,
      }],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => ` SCI: ${ctx.raw.toExponential(4)} gCO₂/inf`
          }
        }
      },
      scales: {
        x: {
          ticks: { color: '#6b7280', font: { size: 10 } },
          grid: { color: '#1e2d21' },
          title: { display: true, text: 'gCO₂eq / inference', color: '#6b7280', font: { size: 10 } },
        },
        y: { ticks: { color: '#9ca3af', font: { size: 11 } }, grid: { color: '#1e2d21' } },
      },
    },
  });
}

// -----------------------------------------------------------------------
// Benchmark Results
// -----------------------------------------------------------------------

async function loadBenchmarkResults() {
  try {
    const res = await fetch('/api/benchmark_results');
    cachedBenchRows = await res.json();
    renderBenchTable(cachedBenchRows);
  } catch (e) {
    console.error('Failed to load benchmark results:', e);
  }
}

// -----------------------------------------------------------------------
// Group rows into pipeline runs (baseline+optimized pair by module+gpu+time)
// -----------------------------------------------------------------------

function groupIntoPipelines(rows) {
  // Each job_name like "raft-baseline-l4" or "raft-optimized-l4" has multiple
  // metric rows (one per dataset). Group by job_name first.
  const byJob = {};
  for (const r of rows) {
    if (!byJob[r.job_name]) byJob[r.job_name] = [];
    byJob[r.job_name].push(r);
  }

  // Now pair baseline+optimized jobs by module+gpu_accelerator+close timestamps.
  // Sort jobs by created_at ascending to pair them in order.
  const jobList = Object.entries(byJob).map(([name, recs]) => ({
    name,
    variant: recs[0].variant,
    module: recs[0].module,
    gpu: recs[0].gpu,
    gpu_accelerator: recs[0].gpu_accelerator,
    created_at: recs[0].created_at,
    rows: recs,
  }));
  jobList.sort((a, b) => a.created_at.localeCompare(b.created_at));

  const pipelines = [];
  const used = new Set();

  for (const job of jobList) {
    if (used.has(job.name)) continue;
    if (job.variant !== 'baseline') continue;

    // Find the closest optimized job with same module+gpu_accelerator
    let bestMatch = null;
    for (const other of jobList) {
      if (used.has(other.name)) continue;
      if (other.variant !== 'optimized') continue;
      if (other.module !== job.module || other.gpu_accelerator !== job.gpu_accelerator) continue;
      if (other.created_at >= job.created_at) {
        bestMatch = other;
        break;
      }
    }

    if (bestMatch) {
      const key = `${job.module}|${job.gpu_accelerator}|${job.created_at}`;
      pipelines.push({
        key,
        module: job.module,
        gpu: job.gpu,
        baseline: job,
        optimized: bestMatch,
        created_at: job.created_at,
      });
      used.add(job.name);
      used.add(bestMatch.name);
    }
  }

  return pipelines;
}

// -----------------------------------------------------------------------
// Select a pipeline and compute SCI
// -----------------------------------------------------------------------

function selectPipeline(key) {
  selectedPipelineKey = key;
  // Re-render table to update highlight
  renderBenchTable(cachedBenchRows);

  const pipelines = groupIntoPipelines(cachedBenchRows);
  const pipeline = pipelines.find(p => p.key === key);
  if (!pipeline) return;

  // Use first row from each side for eval_time_sec (same across datasets)
  const bRow = pipeline.baseline.rows[0];
  const oRow = pipeline.optimized.rows[0];

  // Read sidebar params
  const zone = document.getElementById('zone').value;
  const intensity = ZONE_INTENSITY[zone] ?? 475;
  const gpuType = document.getElementById('gpu_type').value;
  const te_gco2 = GPU_TE[gpuType] ?? 150000;
  const lifespan = parseFloat(document.getElementById('lifespan_years').value) || 4;
  const R = parseInt(document.getElementById('functional_units').value) || 10000;

  const GPU_POWER_KW = {
    'NVIDIA A100 80GB': 0.300,
    'NVIDIA L4': 0.072,
    'NVIDIA L40S': 0.350,
    'NVIDIA RTX 4090': 0.450,
    'NVIDIA V100': 0.300,
    'NVIDIA T4': 0.070,
  };
  const powerKw = GPU_POWER_KW[gpuType] ?? 0.300;

  const bEnergyKwh = powerKw * (bRow.eval_time_sec / 3600);
  const oEnergyKwh = powerKw * (oRow.eval_time_sec / 3600);
  const bDurationH = bRow.eval_time_sec / 3600;
  const oDurationH = oRow.eval_time_sec / 3600;
  const bEmbodied = te_gco2 * (bDurationH / (lifespan * 8760));
  const oEmbodied = te_gco2 * (oDurationH / (lifespan * 8760));
  const bSci = (bEnergyKwh * intensity + bEmbodied) / R;
  const oSci = (oEnergyKwh * intensity + oEmbodied) / R;
  const bTotal = bEnergyKwh * intensity + bEmbodied;
  const oTotal = oEnergyKwh * intensity + oEmbodied;

  renderDashboard({
    zone,
    intensity,
    gpu_type: pipeline.gpu,
    te_gco2,
    lifespan_years: lifespan,
    functional_units: R,
    optimisations_applied: [
      'torch.compile() on model forward pass (reduce-overhead mode)',
      'AMP bfloat16 autocast during inference',
    ],
    baseline: {
      energy_kwh: bEnergyKwh,
      duration_h: bDurationH,
      embodied_gco2: bEmbodied,
      ei_gco2: bEnergyKwh * intensity,
      total_gco2: bTotal,
      sci: bSci,
    },
    optimized: {
      energy_kwh: oEnergyKwh,
      duration_h: oDurationH,
      embodied_gco2: oEmbodied,
      ei_gco2: oEnergyKwh * intensity,
      total_gco2: oTotal,
      sci: oSci,
    },
    reduction: {
      energy_pct: (bEnergyKwh - oEnergyKwh) / bEnergyKwh * 100,
      sci_pct: (bSci - oSci) / bSci * 100,
      co2_saved_gco2: bTotal - oTotal,
    },
  });
}

// -----------------------------------------------------------------------
// Render benchmark table with pipeline grouping + row selection
// -----------------------------------------------------------------------

function renderBenchTable(rows) {
  const tbody = document.getElementById('bench-table-body');
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="12" class="px-4 py-4 text-center text-gray-600 italic">No benchmark results found.</td></tr>`;
    return;
  }

  const pipelines = groupIntoPipelines(rows);

  // Build a set of job_names belonging to the selected pipeline
  const selectedJobs = new Set();
  if (selectedPipelineKey) {
    const sel = pipelines.find(p => p.key === selectedPipelineKey);
    if (sel) {
      selectedJobs.add(sel.baseline.name);
      selectedJobs.add(sel.optimized.name);
    }
  }

  // Map each row to its pipeline key
  const rowToPipeline = {};
  for (const p of pipelines) {
    for (const r of p.baseline.rows) rowToPipeline[r.id] = p.key;
    for (const r of p.optimized.rows) rowToPipeline[r.id] = p.key;
  }

  tbody.innerHTML = rows.map(r => {
    const variantClass = r.variant === 'optimized'
      ? 'text-brand-400 bg-brand-900/30 border border-brand-800'
      : 'text-blue-400 bg-blue-900/30 border border-blue-900';

    const pKey = rowToPipeline[r.id];
    const isSelected = selectedJobs.has(r.job_name);
    const selectedClass = isSelected ? 'bg-brand-900/20 border-l-2 border-l-brand-500' : '';
    const cursor = pKey ? 'cursor-pointer' : '';

    return `<tr class="hover:bg-surface-600/30 transition-colors ${selectedClass} ${cursor}"
                onclick="${pKey ? `selectPipeline('${pKey}')` : ''}">
      <td class="px-4 py-2.5 text-gray-400">#${r.id}</td>
      <td class="px-4 py-2.5 text-gray-200 font-mono text-xs">${r.job_name ?? '—'}</td>
      <td class="px-4 py-2.5 text-gray-300">${r.module}</td>
      <td class="px-4 py-2.5">
        <span class="px-2 py-0.5 rounded-full text-xs font-medium ${variantClass}">${r.variant}</span>
      </td>
      <td class="px-4 py-2.5 text-gray-400 text-xs">${r.gpu}</td>
      <td class="px-4 py-2.5 text-gray-300 font-mono text-xs">${r.dataset}</td>
      <td class="px-4 py-2.5 text-gray-300 font-mono text-xs">${r.metric_name}</td>
      <td class="px-4 py-2.5 text-gray-200 font-mono">${Number(r.metric_value).toFixed(4)}</td>
      <td class="px-4 py-2.5 text-gray-300 font-mono">${r.throughput ? Number(r.throughput).toFixed(2) + ' img/s' : '—'}</td>
      <td class="px-4 py-2.5 text-gray-300 font-mono">${r.avg_latency_ms ? Number(r.avg_latency_ms).toFixed(1) + ' ms' : '—'}</td>
      <td class="px-4 py-2.5 text-gray-300 font-mono">${r.eval_time_sec ? Number(r.eval_time_sec).toFixed(1) + ' s' : '—'}</td>
      <td class="px-4 py-2.5 text-gray-500">${fmtDate(r.created_at)}</td>
    </tr>`;
  }).join('');
}

function fmtDate(d) {
  if (!d) return '—';
  const dt = new Date(d.replace(' ', 'T') + 'Z');
  return dt.toLocaleString('en-GB', { dateStyle: 'short', timeStyle: 'short' });
}

function capitalise(s) {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : '—';
}

// -----------------------------------------------------------------------
// Job history panel
// -----------------------------------------------------------------------

async function refreshHistory() {
  try {
    const res = await fetch('/api/jobs');
    const jobs = await res.json();
    const el = document.getElementById('history-list');
    if (!jobs.length) {
      el.innerHTML = '<p class="italic">No jobs yet.</p>';
      return;
    }
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

// -----------------------------------------------------------------------
// Elapsed time
// -----------------------------------------------------------------------

function updateElapsed() {
  if (!startTime) return;
  const s = Math.floor((Date.now() - startTime) / 1000);
  const m = Math.floor(s / 60);
  const ss = s % 60;
  document.getElementById('elapsed-time').textContent =
    `Elapsed: ${m}:${ss.toString().padStart(2, '0')}`;
}

// -----------------------------------------------------------------------
// Status badge
// -----------------------------------------------------------------------

function setStatusBadge(state, label) {
  const el = document.getElementById('status-badge');
  el.textContent = label;
  el.className = 'px-3 py-1 rounded-full text-xs font-medium border ';
  if (state === 'running') {
    el.className += 'bg-yellow-900/30 text-yellow-400 border-yellow-800 animate-pulse';
  } else if (state === 'done') {
    el.className += 'bg-brand-900/30 text-brand-400 border-brand-800';
  } else {
    el.className += 'bg-surface-600 text-gray-400 border-surface-500';
  }
}

// -----------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------

function stopPolling() {
  if (pollTimer)   { clearInterval(pollTimer);   pollTimer   = null; }
  if (elapsedTimer){ clearInterval(elapsedTimer); elapsedTimer = null; }
}

function resetControls() {
  document.getElementById('btn-run').disabled    = false;
  document.getElementById('btn-cancel').disabled = true;
}
