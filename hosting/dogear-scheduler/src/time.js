// The Manila calendar the watchdog judges by. Pure. Manila is UTC+8 with no DST,
// and the UTC day matters: Monday 06:00 Manila is Sunday 22:00 UTC.

const HOUR = 3600 * 1000;
const DAY = 24 * HOUR;
const OFFSET = 8 * HOUR;

// The Manila calendar date (YYYY-MM-DD) of an instant.
export function manilaDate(now) {
  return new Date(now.getTime() + OFFSET).toISOString().slice(0, 10);
}

// The instant of 00:00 Manila on a Manila calendar date, plus `hours`.
export function manilaTime(isoDate, hours = 0, minutes = 0) {
  return new Date(Date.parse(`${isoDate}T00:00:00Z`) - OFFSET + hours * HOUR + minutes * 60 * 1000);
}

export function addDays(isoDate, days) {
  return new Date(Date.parse(`${isoDate}T00:00:00Z`) + days * DAY).toISOString().slice(0, 10);
}

// 0 = Sunday .. 6 = Saturday, in Manila.
export function manilaWeekday(now) {
  return new Date(now.getTime() + OFFSET).getUTCDay();
}

// The Monday (Manila) of the Monday-to-Sunday week containing `now`.
export function currentMonday(now) {
  const back = (manilaWeekday(now) + 6) % 7;
  return addDays(manilaDate(now), -back);
}

export function nextMonday(now) {
  return addDays(currentMonday(now), 7);
}

// 00:00 Manila on the Friday of the current week (the Friday stage window opens).
export function fridayStart(now) {
  return manilaTime(addDays(currentMonday(now), 4));
}

// Monday 06:00 Manila of the current week (the publication rollover).
export function mondayRollover(now) {
  return manilaTime(currentMonday(now), 6);
}

// 00:00 Manila today.
export function dayStart(now) {
  return manilaTime(manilaDate(now));
}

export const DAY_MS = DAY;
