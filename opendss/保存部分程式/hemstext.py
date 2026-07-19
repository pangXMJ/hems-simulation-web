async function initDetail(route){
  const devices = await loadCSV('devices','./data/devices.csv');
  const d = devices.find(x=>x.id===route) || { label: route, status: 'ON', power_w: '--', voltage_v: '--', current_a: '--', icon_key: 'meter' };
  
  // 1. 設定畫面上方的標題與狀態徽章
  document.getElementById('detailEyebrow').textContent = 'DEVICE / ' + route.toUpperCase();
  document.getElementById('detailTitle').textContent = d.label;
  const badge = document.getElementById('detailBadge');
  badge.textContent = d.status;
  badge.className = 'badge ' + (d.status==='ON' ? 'badge-on':'badge-off');
  document.getElementById('detailIcon').innerHTML = ICONS[d.icon_key] || '';

  // 抓取準備用來放卡片的容器
  const gridContainer = document.querySelector('#detail-view .grid-2');

  // ==========================================
  // ⚡ 專屬於「電錶總覽」的動態畫面
  // ==========================================
  if (route === 'meter') {
      gridContainer.innerHTML = `
        <div class="card">
          <div class="spec-label" style="margin-bottom:20px;">各分路當前累積功率 (kWh)</div>
          <div class="spec-sheet">
            <!-- 總電錶與 PV 電錶 -->
            <div class="spec-sheet-row"><span class="k" style="color:var(--accent);">總電錶 T</span><span class="v" id="valMeterMain">--</span></div>
            <div class="spec-sheet-row"><span class="k" style="color:var(--accent);">PV 電錶</span><span class="v" id="valMeterPV">--</span></div>
            
            <!-- 各分路電錶 -->
            <div class="spec-sheet-row"><span class="k">A 電錶</span><span class="v" id="valMeterA">--</span></div>
            <div class="spec-sheet-row"><span class="k">B 電錶</span><span class="v" id="valMeterB">--</span></div>
            <div class="spec-sheet-row"><span class="k">C 電錶</span><span class="v" id="valMeterC">--</span></div>
            <div class="spec-sheet-row"><span class="k">D 電錶</span><span class="v" id="valMeterD">--</span></div>
          </div>
        </div>
        <div class="card" style="display:flex; flex-direction:column;">
          <p class="chart-label">當下分路耗電佔比</p>
          <div style="flex:1; position:relative; min-height:200px;">
            <!-- 準備用來畫長條圖的畫布 -->
            <canvas id="liveBarChart"></canvas>
          </div>
        </div>
      `;
  } 
  // ==========================================
  // 🏠 其他樓層 (1F, 2F, 3F) 的預設畫面
  // ==========================================
  else {
      gridContainer.innerHTML = `
        <div class="card">
          <div class="spec-sheet">
            <div class="spec-sheet-row"><span class="k">功率 POWER</span><span class="v" id="detailPower">${d.power_w}W</span></div>
            <div class="spec-sheet-row"><span class="k">電壓 VOLTAGE</span><span class="v" id="detailVoltage">${d.voltage_v}V</span></div>
            <div class="spec-sheet-row"><span class="k">電流 CURRENT</span><span class="v" id="detailCurrent">${d.current_a}A</span></div>
          </div>
        </div>
        <div class="card">
          <p class="chart-label">24H POWER TREND (kW)</p>
          <canvas id="detailChart" height="190"></canvas>
        </div>
      `;
  }
}