# -*- coding: utf-8 -*-
"""거시 모니터 대시보드 HTML 빌더 — chart_data.json + 템플릿 → macro_dashboard.html"""
import json
import os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config', 'data', 'chart_data_js.json')
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'macro_dashboard.html')

js_data = open(DATA, encoding='utf-8').read()

html = '''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>거시 모니터 대시보드</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"><\/script>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Malgun Gothic', sans-serif; background: #1a1a2e; color: #eee; padding: 16px; }
.header { text-align: center; margin-bottom: 20px; }
.header h1 { font-size: 1.4em; color: #64b5f6; }
.summary { background: #0d1b2a; border-radius: 8px; padding: 16px; margin-bottom: 16px; max-width: 1400px; margin-left: auto; margin-right: auto; border: 1px solid #2a2a4a; }
.summary h2 { font-size: 1.1em; color: #64b5f6; margin-bottom: 8px; }
.summary .row { display: flex; flex-wrap: wrap; gap: 12px; }
.summary .item { background: #16213e; padding: 8px 12px; border-radius: 6px; min-width: 120px; }
.summary .item .label { font-size: 0.7em; color: #90a4ae; }
.summary .item .val { font-size: 1.1em; font-weight: bold; }
.pos { color: #ef5350; }
.neg { color: #26a69a; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; max-width: 1400px; margin: 0 auto; }
.chart-box { background: #16213e; border-radius: 8px; padding: 12px; border: 1px solid #2a2a4a; }
.chart-box h3 { font-size: 0.9em; color: #90caf9; margin-bottom: 8px; }
.chart-wrap { position: relative; height: 260px; }
.values { margin-top: 8px; font-size: 0.8em; color: #b0bec5; line-height: 1.5; }
.full-width { grid-column: 1 / -1; }
@media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="header"><h1>📊 거시 모니터 대시보드</h1></div>
<div class="summary" id="summary"></div>
<div class="grid">
  <div class="chart-box full-width"><h3>📈 코스피 지수 vs 200일선</h3><div class="chart-wrap" style="height:300px"><canvas id="chKospi"></canvas></div><div class="values" id="vKospi"></div></div>
  <div class="chart-box"><h3>⚡ 단기: 5일 등락률 (반도체 vs 기타, %)</h3><div class="chart-wrap" style="height:200px"><canvas id="chShort"></canvas></div><div class="values" id="vShort"></div></div>
  <div class="chart-box"><h3>🔄 회전 스프레드 (%p)</h3><div class="chart-wrap" style="height:200px"><canvas id="chRot"></canvas></div><div class="values" id="vRot"></div></div>
  <div class="chart-box"><h3>📊 A/D 누적 라인</h3><div class="chart-wrap" style="height:200px"><canvas id="chAD"></canvas></div><div class="values" id="vAD"></div></div>
  <div class="chart-box"><h3>🌐 외국인 시장 순매수 (억원)</h3><div class="chart-wrap" style="height:200px"><canvas id="chFn"></canvas></div><div class="values" id="vFn"></div></div>
  <div class="chart-box"><h3>📊 변동성 ATR(14)% + ADX(14)</h3><div class="chart-wrap" style="height:200px"><canvas id="chVol"></canvas></div><div class="values" id="vVol"></div></div>
  <div class="chart-box full-width"><h3>📊 200일선 위 종목 비율 (%)</h3><div class="chart-wrap" style="height:160px"><canvas id="chPct"></canvas></div><div class="values" id="vPct"></div></div>
</div>
<script>
const D = ''' + js_data + ''';
const c = D.current;

document.getElementById('summary').innerHTML = `
<h2>📌 현재 상태</h2>
<div class="row">
  <div class="item"><div class="label">KOSPI</div><div class="val">${c.kospi.toLocaleString()}</div></div>
  <div class="item"><div class="label">200일선 대비</div><div class="val ${c.disparity>=0?'pos':'neg'}">${c.disparity>=0?'+':''}${c.disparity}%</div></div>
  <div class="item"><div class="label">고점대비</div><div class="val neg">${c.dd}%</div></div>
  <div class="item"><div class="label">200일선 기울기</div><div class="val ${c.slope>=0?'pos':'neg'}">${c.slope>=0?'+':''}${c.slope}%</div></div>
  <div class="item"><div class="label">ADX</div><div class="val">${c.adx}</div></div>
  <div class="item"><div class="label">ATR 변동성</div><div class="val">${c.atr_pct}%</div></div>
  <div class="item"><div class="label">200일선 위 종목</div><div class="val">${c.pct_above}%</div></div>
  <div class="item"><div class="label">A/D 당일</div><div class="val ${c.ad_change>=0?'pos':'neg'}">${c.ad_change>0?'+':''}${c.ad_change}</div></div>
</div>`;

Chart.defaults.color = '#90a4ae';
Chart.defaults.borderColor = '#2a2a4a';
const grid = { drawBorder: false, color: 'rgba(255,255,255,0.05)' };
const noLegend = { legend: { display: false } };

new Chart(document.getElementById('chKospi'), { type: 'line',
  data: { labels: D.dates, datasets: [
    { label: '코스피', data: D.kospi, borderColor: '#64b5f6', borderWidth: 2, pointRadius: 0, tension: 0.3 },
    { label: '200일선', data: D.ma200, borderColor: '#ffb74d', borderWidth: 1.5, borderDash: [5,5], pointRadius: 0, tension: 0.3 }
  ]},
  options: { responsive: true, maintainAspectRatio: false, scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 15 } }, y: { grid } } }
});

new Chart(document.getElementById('chShort'), { type: 'bar',
  data: { labels: D.dates.slice(-20), datasets: [
    { label: '반도체 블록', data: D.semi_ret.slice(-20), backgroundColor: 'rgba(239,83,80,0.7)' },
    { label: '기타 섹터', data: D.others_ret.slice(-20), backgroundColor: 'rgba(38,166,154,0.7)' }
  ]},
  options: { responsive: true, maintainAspectRatio: false, scales: { x: { grid: { display: false } }, y: { grid, title: { display: true, text: '%' } } } }
});

const rotLabels = D.rot_dates.map(d => d.slice(4));
new Chart(document.getElementById('chRot'), { type: 'bar',
  data: { labels: rotLabels, datasets: [{ data: D.rot_vals,
    backgroundColor: D.rot_vals.map(v => v >= 0 ? 'rgba(239,83,80,0.7)' : 'rgba(38,166,154,0.7)') }]},
  options: { responsive: true, maintainAspectRatio: false, plugins: noLegend,
    scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 15 } }, y: { grid, title: { display: true, text: '%p' } } } }
});

new Chart(document.getElementById('chAD'), { type: 'line',
  data: { labels: D.dates, datasets: [{ data: D.ad_cum, borderColor: '#ab47bc', borderWidth: 2, pointRadius: 0, tension: 0.3, fill: true, backgroundColor: 'rgba(171,71,188,0.1)' }]},
  options: { responsive: true, maintainAspectRatio: false, plugins: noLegend,
    scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 15 } }, y: { grid } } }
});

const fnLabels = D.foreign_dates.map(d => d.slice(4));
new Chart(document.getElementById('chFn'), { type: 'bar',
  data: { labels: fnLabels, datasets: [{ data: D.foreign_vals,
    backgroundColor: D.foreign_vals.map(v => v >= 0 ? 'rgba(239,83,80,0.7)' : 'rgba(38,166,154,0.7)') }]},
  options: { responsive: true, maintainAspectRatio: false, plugins: noLegend,
    scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 15 } }, y: { grid, title: { display: true, text: '억원' } } } }
});

new Chart(document.getElementById('chVol'), { type: 'line',
  data: { labels: D.dates, datasets: [
    { label: 'ATR(14) %', data: D.atr_pct, borderColor: '#ef5350', borderWidth: 2, pointRadius: 0, tension: 0.3, yAxisID: 'y' },
    { label: 'ADX(14)', data: D.adx, borderColor: '#42a5f5', borderWidth: 2, pointRadius: 0, tension: 0.3, yAxisID: 'y1' }
  ]},
  options: { responsive: true, maintainAspectRatio: false,
    scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 15 } },
      y: { grid, title: { display: true, text: 'ATR %' }, position: 'left' },
      y1: { grid: { display: false }, title: { display: true, text: 'ADX' }, position: 'right' } },
    plugins: { legend: { position: 'top' } } }
});

new Chart(document.getElementById('chPct'), { type: 'line',
  data: { labels: D.dates, datasets: [{ data: D.pct_above, borderColor: '#66bb6a', borderWidth: 2, pointRadius: 0, tension: 0.3, fill: true, backgroundColor: 'rgba(102,187,106,0.1)' }]},
  options: { responsive: true, maintainAspectRatio: false, plugins: noLegend,
    scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 15 } }, y: { grid, min: 0, max: 100 } } }
});
</script>
</body>
</html>'''

with open(OUT, 'w', encoding='utf-8') as f:
    f.write(html)
print(f'Dashboard HTML saved: {OUT} ({len(html)} bytes)')
