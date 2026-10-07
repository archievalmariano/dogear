// The publication registry (publication.json), read-only: a strict port of
// dogear/publish/registry.py `parse` and `manifest`. Anything the Python schema
// rejects is rejected here (the shared vectors include each case), so the Worker
// never serves from a registry the publisher would call corrupt.

export const REGISTRY_KEY = "publication.json";
const SCHEMA_VERSION = 1;
const MAX_ISSUE_BYTES = 32 * 1024; // DogearLimits.h kMaxIssueBytes
const HEX64 = /^[0-9a-f]{64}$/;
const TXN = /^[0-9]{8}T[0-9]{6}-[a-z]+-[0-9a-f]{12}$/;
const DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const OPS_WITH_ARTIFACTS = ["launch", "promote", "correct"];
const REVISION_KEYS = ["rev", "op", "txn", "issueId", "issueKey", "issueSha256", "issueBytes", "webKey",
  "webSha256", "provenanceSha256", "datasetSha256", "generator", "generatedAt", "publishedAt", "reason"];

export class CorruptRegistry extends Error {}

const isObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const isInt = (v) => typeof v === "number" && Number.isInteger(v);
const sameKeys = (obj, keys) => {
  const have = Object.keys(obj);
  return have.length === keys.length && keys.every((k) => Object.hasOwn(obj, k));
};

// A calendar date as UTC midnight, or null. Mirrors date.fromisoformat + isoformat equality.
export function parseDate(value) {
  const m = typeof value === "string" ? DATE.exec(value) : null;
  if (!m) return null;
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  if (d.getUTCFullYear() !== +m[1] || d.getUTCMonth() !== +m[2] - 1 || d.getUTCDate() !== +m[3]) return null;
  if (+m[1] < 1) return null;
  return d;
}

export const isoDate = (d) => d.toISOString().slice(0, 10);
export const isMonday = (d) => d.getUTCDay() === 1;

export const issueKey = (issueId, sha) => `issues/${issueId}.${sha.slice(0, 16)}.json`;
export const webKey = (week, sha) => `web/${week}/${sha.slice(0, 16)}.html`;

// JSON numbers outside strings that are not plain integers (4000.0, 0e0). Python's
// json reads them as floats, which the schema rejects; JSON.parse cannot tell.
function hasNonIntegerNumber(text) {
  let inString = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inString) {
      if (c === "\\") i++;
      else if (c === '"') inString = false;
    } else if (c === '"') {
      inString = true;
    } else if ((c >= "0" && c <= "9") || c === "-") {
      let j = i;
      while (j < text.length && /[0-9eE.+-]/.test(text[j])) j++;
      if (/[.eE]/.test(text.slice(i, j))) return true;
      i = j - 1;
    }
  }
  return false;
}

function checkRevision(week, i, rev) {
  const where = `weeks.${week}.revisions[${i}]`;
  const fail = (msg) => { throw new CorruptRegistry(`${where}: ${msg}`); };
  if (!isObject(rev)) fail("not an object");
  const keys = Object.keys(rev);
  const required = REVISION_KEYS.filter((k) => k !== "reason");
  if (!required.every((k) => Object.hasOwn(rev, k)) || keys.some((k) => !REVISION_KEYS.includes(k))) fail("wrong fields");
  if (rev.rev !== i) fail(`rev must be ${i}`);
  if (!OPS_WITH_ARTIFACTS.includes(rev.op) || typeof rev.txn !== "string" || !TXN.test(rev.txn)) fail("bad op or txn");
  if (rev.issueId !== `dogear-${week}`) fail("issueId does not match the week");
  for (const f of ["issueSha256", "webSha256", "provenanceSha256", "datasetSha256"]) {
    if (typeof rev[f] !== "string" || !HEX64.test(rev[f])) fail(`${f} is not a SHA-256`);
  }
  if (rev.issueKey !== issueKey(rev.issueId, rev.issueSha256)) fail("issueKey does not match its hash");
  if (rev.webKey !== webKey(week, rev.webSha256)) fail("webKey does not match its hash");
  if (!isInt(rev.issueBytes) || !(rev.issueBytes > 0 && rev.issueBytes <= MAX_ISSUE_BYTES)) fail("issueBytes out of range");
  for (const f of ["generator", "generatedAt", "publishedAt"]) {
    if (typeof rev[f] !== "string" || !rev[f]) fail(`${f} missing`);
  }
  if (rev.op === "correct" && !(typeof rev.reason === "string" && rev.reason.trim())) fail("a correction needs a reason");
}

export function validate(reg) {
  if (!isObject(reg) || !sameKeys(reg, ["schemaVersion", "txn", "current", "hold", "weeks"])) {
    throw new CorruptRegistry("wrong top-level fields");
  }
  if (reg.schemaVersion !== SCHEMA_VERSION) throw new CorruptRegistry(`schemaVersion must be ${SCHEMA_VERSION}`);
  if (typeof reg.txn !== "string" || !TXN.test(reg.txn)) throw new CorruptRegistry("bad txn");
  const weeks = reg.weeks;
  if (!isObject(weeks) || Object.keys(weeks).length === 0) throw new CorruptRegistry("weeks must be a non-empty object");
  for (const [key, entry] of Object.entries(weeks)) {
    const day = parseDate(key);
    if (!day || !isMonday(day)) throw new CorruptRegistry(`weeks.${key}: not a Monday`);
    if (!isObject(entry) || !sameKeys(entry, ["active", "revisions"])) throw new CorruptRegistry(`weeks.${key}: wrong fields`);
    const revs = entry.revisions;
    if (!Array.isArray(revs) || revs.length === 0) throw new CorruptRegistry(`weeks.${key}: no revisions`);
    revs.forEach((rev, i) => checkRevision(key, i, rev));
    if (!isInt(entry.active) || entry.active < 0 || entry.active >= revs.length) {
      throw new CorruptRegistry(`weeks.${key}: active out of range`);
    }
  }
  if (typeof reg.current !== "string" || !Object.hasOwn(weeks, reg.current)) {
    throw new CorruptRegistry("current names no published week");
  }
  const hold = reg.hold;
  if (hold !== null && (!isObject(hold) || !sameKeys(hold, ["reason", "since", "txn"]) ||
      !Object.values(hold).every((v) => typeof v === "string" && v))) {
    throw new CorruptRegistry("bad hold");
  }
  return reg;
}

export function parse(text) {
  let reg;
  try {
    reg = JSON.parse(text);
  } catch (err) {
    throw new CorruptRegistry(`not JSON: ${err.message}`);
  }
  if (hasNonIntegerNumber(text)) throw new CorruptRegistry("a number is not an integer");
  return validate(reg);
}

export function activeRevision(reg, week) {
  const entry = reg.weeks[week];
  return entry.revisions[entry.active];
}

// The device's /current.json, byte-identical to registry.manifest_bytes (Python's
// json.dumps(indent=2) in insertion order; every value is ASCII).
export function manifestText(reg) {
  const week = reg.current;
  const rev = activeRevision(reg, week);
  const end = new Date(parseDate(week).getTime() + 6 * 86400000);
  const manifest = {
    issueId: rev.issueId,
    issuePath: rev.issueKey,
    issueSha256: rev.issueSha256,
    revision: rev.rev,
    webPath: `${week}/`,
    week: { start: week, end: isoDate(end) },
  };
  return JSON.stringify(manifest, null, 2) + "\n";
}
