
window.ind3Lamps = function(bars, I3, isChop, prevClose){
  const out = new Map(), N = YH.lampHold || 2; let day = null, n = 0, cur = null, cand = null, cnt = 0, dHi = 0, dLo = 0;
  for (let i = 0; i < bars.length; i++){
    const b = bars[i];
    const d = dayKey(b.time); if (d !== day){ day = d; n = 0; cur = null; cand = null; cnt = 0; } n++;
    let raw = ind3Raw(I3.get(b.time), b.time, n < SIG.skipMin);
    if (n === 1){ dHi = b.high; dLo = b.low; } else { dHi = Math.max(dHi, b.high); dLo = Math.min(dLo, b.low); }
    // 2026-10-21 04:20 開(YH.lampExtPct 0.3;0 = 關):價已經貼著今天最高(做多)/ 最低(做空)lampExtPct% 以內 → 不亮順勢燈,當監控(不追在轉折點)
    const ex = YH.lampExtPct || 0;
    // 2026-10-21 04:40 使用者「改」(友達一路破底,做空被切成好幾段):只擋「剛要亮」的;燈已經是同方向(做空 / 漸空 → 做空;做多 / 漸多 → 做多)就不擋,讓趨勢連成一段
    const dirOf = x => (x === '做多' || x === '漸多') ? 1 : (x === '做空' || x === '漸空') ? -1 : 0;
    const lit = cur === '做多' ? 1 : cur === '做空' ? -1 : 0;   // 只有已經亮「做多 / 做空」才算已亮(漸多 / 漸空不算,富喬 13:01 漸空 → 做空在底部照擋)
    // 2026-10-21 05:30 開盤到 YH.lampExtFrom(09:45)不套用(今天的高低點才剛形成;第一個燈號改要連 3 根後,今國光開盤直衝漲停整段被當「貼當日高點」擋成監控)
    if (ex > 0 && hmKey(b.time) >= (YH.lampExtFrom || '09:45') && dirOf(raw) !== 0 && dirOf(raw) !== lit && ((dirOf(raw) > 0 && b.close >= dHi * (1 - ex / 100)) || (dirOf(raw) < 0 && b.close <= dLo * (1 + ex / 100)))) raw = '監控';
    if (raw !== '等待' && raw !== '監控' && isChop && isChop(i)) raw = '監控';
    if (window.__hook && (raw === '做多' || raw === '做空') && cur !== raw) raw = window.__hook(raw, i, bars, n, prevClose, cur);
    // 2026-10-21 05:50 使用者「改」(榮科 / 建榮 / 中釉 當天大漲還亮做空,7 錯 3 對):當天漲幅 ≥ YH.lampNoShortUpPct(2.5)% 不亮做空(同判一「漲超過 3% 不做空」的想法);漸空不擋(擋漸空會讓富喬等其他股票的燈號跟著變,YH.lampNoShortPre=true 才擋)
    if ((raw === '做空' || (YH.lampNoShortPre && raw === '漸空')) && prevClose > 0 && (b.close - prevClose) / prevClose * 100 >= (YH.lampNoShortUpPct || 99)) raw = '監控';
    // 2026-10-21 05:00 使用者「改」(台塑化 10/20 下午卡在 107~108,燈號做多做空來回換十幾次):今天最近 YH.lampFlatBars(30)根高低差 ≤ YH.lampFlatTicks(2)檔
    //   = 價格沒在動,只顯示監控。用「檔」不用 %:台塑化 107~108 是 2 檔(0.9%),友達 36.75~36.9 慢慢跌是 3 檔(0.4%),用 % 分不開
    if (raw !== '等待' && raw !== '監控' && (YH.lampFlatTicks || 0) > 0){
      let fh = -Infinity, fl = Infinity; const fn = Math.min(YH.lampFlatBars || 30, n);
      for (let k = i; k > i - fn && k >= 0; k--){ fh = Math.max(fh, bars[k].high); fl = Math.min(fl, bars[k].low); }
      const tk = fl < 10 ? 0.01 : fl < 50 ? 0.05 : fl < 100 ? 0.1 : fl < 500 ? 0.5 : fl < 1000 ? 1 : 5;   // 台股升降單位
      if (fl > 0 && (fh - fl) <= tk * YH.lampFlatTicks + 1e-9) raw = '監控';
    }
    if (raw === '等待' || hmKey(b.time) >= '13:25'){ cur = raw; cand = null; cnt = 0; }
    else {
    if (cur === null || cur === '等待'){ cur = '監控'; cand = null; cnt = 0; }   // 2026-10-21 05:20 使用者「改」(中揚光 09:14 等待一結束就直接亮做空,接著拉高):等待結束先從監控開始,第一個燈號也要連 3 根
    if (raw === cur){ cand = null; cnt = 0; }
    else { if (raw === cand) cnt++; else { cand = raw; cnt = 1; } if (cnt >= N){ cur = raw; cand = null; cnt = 0; } }
    }
    out.set(b.time, cur);
  }
  return out;
}

window.__hook = function(raw, i, bars, n, prevClose, cur){
  const c = window.__cfg || {}; const sd = raw === '做多' ? 1 : -1; const b = bars[i];
  const down = c.down || (raw === '做多' ? '漸多' : '漸空');
  const s0 = i - n + 1;   // 今天第一根
  if (c.vol){ let a = 0, d = 0; for (let k = Math.max(s0, i - 9); k <= i; k++) a += bars[k].volume; a /= Math.min(10, n); for (let k = s0; k <= i; k++) d += bars[k].volume; d /= n; if (a < d * c.vol) return down; }
  if (c.mom){ const k = Math.max(s0, i - (c.momBars || 10)); if ((b.close - bars[k].close) / bars[k].close * 100 * sd < c.mom) return down; }
  if (c.ap){ let pv = 0, vv = 0; for (let k = s0; k <= i; k++){ pv += (bars[k].high + bars[k].low + bars[k].close) / 3 * bars[k].volume; vv += bars[k].volume; } const ap = vv > 0 ? pv / vv : b.close; if ((b.close - ap) * sd <= 0) return down; }
  if (c.kaSlope){ const ka = k => { let t = 0, m = 0; for (let q = Math.max(s0, k - 9); q <= k; q++){ t += bars[q].close; m++; } return t / m; }; if ((ka(i) - ka(i - 3)) / ka(i - 3) * 100 * sd < c.kaSlope) return down; }
  if (c.obv){ let ob = 0; const O = []; for (let k = s0; k <= i; k++){ if (k > s0) ob += Math.sign(bars[k].close - bars[k-1].close) * bars[k].volume; O.push(ob); } const m = O.slice(-10).reduce((a, x) => a + x, 0) / Math.min(10, O.length); if ((O[O.length - 1] - m) * sd <= 0) return down; }
  if (c.rangePos != null){ let hi = -1e18, lo = 1e18; for (let k = s0; k <= i; k++){ hi = Math.max(hi, bars[k].high); lo = Math.min(lo, bars[k].low); } const pos = hi > lo ? (b.close - lo) / (hi - lo) : 0.5; if (sd < 0 ? pos < c.rangePos : pos > 1 - c.rangePos) return down; }
  return raw;
};
0;
