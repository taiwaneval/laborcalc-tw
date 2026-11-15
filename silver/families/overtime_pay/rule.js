/* overtime_pay — JavaScript 實作（規則卡頁面試算用）。
 * 與 rule.py 為獨立實作；test_agreement.py 檢查兩者在 golden cases 與隨機輸入上一致。
 * 以 1/720 月薪為最小單位做整數運算，避免浮點誤差：
 *   時薪 = 3/720 月薪、時薪×4/3 = 4/720、時薪×5/3 = 5/720、時薪×8/3 = 8/720、一日工資 = 24/720。
 *   最終金額無條件進位到元：⌊(N + 719) / 720⌋（N 為 720 分之 N 元的整數）。
 */
const OvertimeRule = (function () {
  const MAX_HOURS = { weekday: 4, rest_day: 12, holiday: 12 }; // LSA-32-2
  const LABEL = { weekday: '平日延長工時', rest_day: '休息日出勤', holiday: '國定假日出勤' };

  function validate(monthly, dayType, hours, minimumWage) {
    const errs = [];
    if (!Number.isInteger(monthly) || monthly <= 0) errs.push('月薪須為正整數');
    else if (minimumWage && monthly < minimumWage) errs.push('月薪低於最低工資 ' + minimumWage);
    if (!(dayType in MAX_HOURS)) errs.push('日別不合法');
    if (!Number.isInteger(hours) || hours < 0) errs.push('時數須為非負整數');
    else if (dayType in MAX_HOURS && hours > MAX_HOURS[dayType])
      errs.push(LABEL[dayType] + '時數上限 ' + MAX_HOURS[dayType] + ' 小時');
    return errs;
  }

  function compute(monthly, dayType, hours) {
    const errs = validate(monthly, dayType, hours);
    if (errs.length) throw new Error(errs.join('; '));
    const items = [];
    if (dayType === 'weekday') {
      const h1 = Math.min(hours, 2), h2 = Math.max(hours - 2, 0);
      if (h1) items.push({ label: '前 2 小時 ×4/3', hours: h1, units: 4 * h1 });
      if (h2) items.push({ label: '第 3 小時起 ×5/3', hours: h2, units: 5 * h2 });
    } else if (dayType === 'rest_day') { // LSA-24-2；第 9 小時起 ×8/3 見 MOL-RESTDAY-9H
      const h1 = Math.min(hours, 2), h2 = Math.min(Math.max(hours - 2, 0), 6), h3 = Math.max(hours - 8, 0);
      if (h1) items.push({ label: '前 2 小時 ×4/3', hours: h1, units: 4 * h1 });
      if (h2) items.push({ label: '第 3–8 小時 ×5/3', hours: h2, units: 5 * h2 });
      if (h3) items.push({ label: '第 9 小時起 ×8/3', hours: h3, units: 8 * h3 });
    } else { // holiday — LSA-39
      if (hours > 0) items.push({ label: '8 小時內加發一日工資', hours: Math.min(hours, 8), units: 24 });
      const over = Math.max(hours - 8, 0), h1 = Math.min(over, 2), h2 = Math.max(over - 2, 0);
      if (h1) items.push({ label: '第 9–10 小時 ×4/3', hours: h1, units: 4 * h1 });
      if (h2) items.push({ label: '第 11–12 小時 ×5/3', hours: h2, units: 5 * h2 });
    }
    const N = monthly * items.reduce((s, i) => s + i.units, 0); // 720 分之 N
    const total = Math.floor((N + 719) / 720);                 // 無條件進位到元
    return {
      hourly: monthly / 240,
      items: items.map(i => ({ label: i.label, hours: i.hours, amount: monthly * i.units / 720 })),
      totalExact: N / 720,
      total,
    };
  }

  return { validate, compute, MAX_HOURS, LABEL };
})();

if (typeof module !== 'undefined') module.exports = OvertimeRule;
