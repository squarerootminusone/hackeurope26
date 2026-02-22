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

// Carbon intensity lookup (must mirror backend ZONE_INTENSITY)
const ZONE_INTENSITY = {
  DE: 350, FR: 85, GB: 225, IE: 310, SE: 45, NO: 30, PL: 680,
  'US-CAL-CISO': 250, 'US-MISO': 530, 'US-NY-NYISO': 280,
  IN_SO: 760, CN: 620, 'AU-NSW': 750, WORLD: 475
};

// GPU embodied carbon lookup
const GPU_TE = {
  'NVIDIA A100 80GB': 150000,
  'NVIDIA L40S': 120000,
  'NVIDIA RTX 4090': 85000,
  'NVIDIA V100': 100000,
  'NVIDIA T4': 70000
};

// Step keys (matches backend PIPELINE_STEPS)
const STEP_META = [
  { key: 'clone',           icon: '⬇', label: 'Clone' },
  { key: 'analyze',         icon: '🔍', label: 'Analyse' },
  { key: 'optimize',        icon: '⚡', label: 'Optimise' },
  { key: 'build_baseline',  icon: '🐳', label: 'Build Base' },
  { key: 'build_optimized', icon: '🐳', label: 'Build Opt' },
  { key: 'eval_baseline',   icon: '📊', label: 'Eval Base' },
  { key: 'eval_optimized',  icon: '📊', label: 'Eval Opt' },
  { key: 'sci_calc',        icon: '🧮', label: 'SCI Calc' },
  { key: 'report',          icon: '📄', label: 'Report' },
];

// -----------------------------------------------------------------------
// Initialise steps in the DOM
// -----------------------------------------------------------------------

function initSteps() {
  const container = document.getElementById('steps-container');
  container.innerHTML = STEP_META.map((s, i) => `
    <div id="step-${i}" class="flex flex-col items-center gap-1 p-2 rounded-lg bg-surface-800 border border-surface-600 transition-all">
      <div id="step-icon-${i}"
           class="w-8 h-8 rounded-full bg-surface-700 border border-surface-500 flex items-center justify-center text-base transition-all">
        <span class="text-gray-600">○</span>
      </div>
      <span class="text-center text-gray-600 leading-tight" style="font-size:10px">${s.label}</span>
    </div>
  `).join('');
}

function updateStep(idx, status) {
  const iconEl = document.getElementById(`step-icon-${idx}`);
  const cardEl = document.getElementById(`step-${idx}`);
  if (!iconEl || !cardEl) return;

  iconEl.classList.remove('step-running');

  if (status === 'done') {
    iconEl.className = 'w-8 h-8 rounded-full bg-brand-800 border border-brand-500 flex items-center justify-center text-base transition-all';
    iconEl.innerHTML = '<span class="text-brand-400">✓</span>';
    cardEl.className = 'flex flex-col items-center gap-1 p-2 rounded-lg bg-surface-800 border border-brand-800/50 transition-all';
  } else if (status === 'running') {
    iconEl.className = 'w-8 h-8 rounded-full bg-surface-700 border border-brand-500 flex items-center justify-center text-base transition-all step-running';
    iconEl.innerHTML = `<span class="text-brand-400">${STEP_META[idx].icon}</span>`;
    cardEl.className = 'flex flex-col items-center gap-1 p-2 rounded-lg bg-surface-700 border border-brand-600 transition-all';
  } else {
    iconEl.className = 'w-8 h-8 rounded-full bg-surface-700 border border-surface-500 flex items-center justify-center text-base transition-all';
    iconEl.innerHTML = '<span class="text-gray-600">○</span>';
    cardEl.className = 'flex flex-col items-center gap-1 p-2 rounded-lg bg-surface-800 border border-surface-600 transition-all';
  }
}

// -----------------------------------------------------------------------
// Zone / GPU hint updates
// -----------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
  initSteps();
  loadEvaluations();

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

  // Reset UI
  resetDashboard();
  setStatusBadge('running', 'Running');
  document.getElementById('btn-run').disabled = true;
  document.getElementById('btn-cancel').disabled = false;
  document.getElementById('idle-placeholder').style.display = 'none';

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
  document.getElementById('progress-label').textContent = 'Cancelled';
}

// -----------------------------------------------------------------------
// Poll job status
// -----------------------------------------------------------------------

async function pollJob() {
  if (!currentJobId) return;
  try {
    const res = await fetch(`/api/jobs/${currentJobId}`);
    const job = await res.json();
    updatePipelineUI(job);

    if (job.status === 'completed') {
      stopPolling();
      setStatusBadge('done', 'Completed');
      resetControls();
      if (job.sci_results) renderDashboard(job.sci_results);
      loadEvaluations();
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
// Update pipeline step indicators + progress bar
// -----------------------------------------------------------------------

function updatePipelineUI(job) {
  const total = STEP_META.length;
  let done = 0;

  job.steps.forEach((step, idx) => {
    updateStep(idx, step.status);
    if (step.status === 'done') done++;
  });

  const pct = Math.round((done / total) * 100);
  document.getElementById('progress-bar').style.width = pct + '%';
  document.getElementById('progress-pct').textContent = pct + '%';

  const currentIdx = job.current_step;
  if (currentIdx >= 0 && currentIdx < STEP_META.length) {
    document.getElementById('progress-label').textContent =
      STEP_META[currentIdx].label + '…';
  }
  if (job.status === 'completed') {
    document.getElementById('progress-label').textContent = 'Pipeline complete';
  }
}

// -----------------------------------------------------------------------
// Render SCI dashboard
// -----------------------------------------------------------------------

function renderDashboard(r) {
  // Show dashboard, hide idle placeholder
  document.getElementById('sci-dashboard').style.display = 'block';
  document.getElementById('idle-placeholder').style.display = 'none';

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
// Evaluations DB
// -----------------------------------------------------------------------

async function loadEvaluations() {
  try {
    const res = await fetch('/api/evaluations');
    const evals = await res.json();
    renderEvalTable(evals);
  } catch (e) {
    console.error('Failed to load evaluations:', e);
  }
}

function renderEvalTable(evals) {
  const tbody = document.getElementById('eval-table-body');
  if (!evals.length) {
    tbody.innerHTML = `<tr><td colspan="10" class="px-4 py-4 text-center text-gray-600 italic">No evaluations found.</td></tr>`;
    return;
  }

  tbody.innerHTML = evals.map(e => {
    const optClass = e.is_optimized
      ? 'text-brand-400 bg-brand-900/30 border border-brand-800'
      : 'text-blue-400 bg-blue-900/30 border border-blue-900';
    const optLabel = e.is_optimized ? 'Yes' : 'No';

    return `<tr class="hover:bg-surface-600/30 transition-colors">
      <td class="px-4 py-2.5 text-gray-400">#${e.id}</td>
      <td class="px-4 py-2.5 text-gray-200 font-mono text-xs max-w-[180px] truncate" title="${e.evaluation_name}">${e.evaluation_name}</td>
      <td class="px-4 py-2.5 text-gray-300 font-mono text-xs">${e.model_name ?? '—'}</td>
      <td class="px-4 py-2.5">
        <span class="px-2 py-0.5 rounded-full text-xs font-medium ${optClass}">${optLabel}</span>
      </td>
      <td class="px-4 py-2.5 text-gray-400 font-mono text-xs">${e.vm_reference}</td>
      <td class="px-4 py-2.5 text-gray-400 text-xs">${e.instance_type ?? '—'}</td>
      <td class="px-4 py-2.5 text-gray-500">${fmtDate(e.create_date)}</td>
      <td class="px-4 py-2.5 text-gray-500">${fmtDate(e.start_runtime_date)}</td>
      <td class="px-4 py-2.5 text-gray-500">${fmtDate(e.end_runtime_date)}</td>
      <td class="px-4 py-2.5 text-gray-500">${fmtDate(e.update_date)}</td>
    </tr>`;
  }).join('');
}

function fmtDate(d) {
  if (!d) return '—';
  // Show only HH:MM if today, else date + time
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

function resetDashboard() {
  // Reset all step icons
  STEP_META.forEach((_, i) => updateStep(i, 'pending'));

  // Progress bar
  document.getElementById('progress-bar').style.width = '0%';
  document.getElementById('progress-pct').textContent = '0%';
  document.getElementById('progress-label').textContent = 'Starting…';
  document.getElementById('elapsed-time').textContent = '';

  // Hide results
  document.getElementById('sci-dashboard').style.display = 'none';

  // Destroy charts
  if (breakdownChart) { breakdownChart.destroy(); breakdownChart = null; }
  if (sciChart)       { sciChart.destroy();       sciChart = null; }
}
