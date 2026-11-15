/* annual_leave_days — JavaScript 實作（規則卡頁面試算用）。
 * 與 rule.py 為獨立實作；test_agreement.py 檢查兩者在 golden cases 與隨機輸入上一致。
 * 日期一律以 {y, mo, d} 整數處理，比較時轉成 Date.UTC 毫秒，避免時區與夏令時間問題。
 * 滿期日依民法 §121：到職日加 m 個月之相當日當天起算已滿；無相當日者以該月末日為期間末日，翌日起算已滿。
 */
const AnnualLeaveRule = (function () {
  const DAY_MS = 86400000;

  function parse(s) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
    if (!m) throw new Error('日期格式須為 YYYY-MM-DD: ' + s);
    const o = { y: +m[1], mo: +m[2], d: +m[3] };
    // Date.UTC 會把 2023-02-30 默默滾成 3 月 2 日；不存在的日期要報錯，與 Python 的 date.fromisoformat 一致
    if (o.mo < 1 || o.mo > 12 || o.d < 1 || o.d > daysInMonth(o.y, o.mo)) throw new Error('不存在的日期: ' + s);
    return o;
  }
  function daysInMonth(y, mo) { return new Date(Date.UTC(y, mo, 0)).getUTCDate(); }   // mo 為 1–12
  function toUTC(o) { return Date.UTC(o.y, o.mo - 1, o.d); }
  function fromUTC(ms) { const t = new Date(ms); return { y: t.getUTCFullYear(), mo: t.getUTCMonth() + 1, d: t.getUTCDate() }; }

  // 到職日加 months 個月後「已滿」的第一天
  function entitlementDate(hire, months) {
    const idx = hire.mo - 1 + months;
    const y = hire.y + Math.floor(idx / 12), mo = (idx % 12) + 1;
    const last = daysInMonth(y, mo);
    if (hire.d <= last) return { y, mo, d: hire.d };
    return fromUTC(Date.UTC(y, mo - 1, last) + DAY_MS);
  }

  function completedMonths(hire, query) {
    if (toUTC(query) < toUTC(hire)) throw new Error('查詢日早於到職日');
    let m = (query.y - hire.y) * 12 + (query.mo - hire.mo) + 1;
    while (m > 0 && toUTC(entitlementDate(hire, m)) > toUTC(query)) m -= 1;
    return m;
  }

  function daysForMonths(months) {
    if (months < 6) return 0;
    if (months < 12) return 3;
    const y = Math.floor(months / 12);
    if (y === 1) return 7;
    if (y === 2) return 10;
    if (y < 5) return 14;
    if (y < 10) return 15;
    return Math.min(15 + (y - 9), 30);
  }

  const LABEL = { 0: 'lt6m', 3: '6m', 7: '1y', 10: '2y', 14: '3y', 15: '5y', 30: 'cap' };

  function compute(hireDate, queryDate) {
    const hire = parse(hireDate), query = parse(queryDate);
    const months = completedMonths(hire, query), total = daysForMonths(months);
    return {
      completedMonths: months,
      completedYears: Math.floor(months / 12),
      tier: LABEL[total] || '10y+',
      total,
      totalExact: total,
    };
  }

  return { compute, entitlementDate, completedMonths, daysForMonths, parse };
})();

if (typeof module !== 'undefined') module.exports = AnnualLeaveRule;
