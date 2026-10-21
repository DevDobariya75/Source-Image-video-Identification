// Display helpers shared by the pages.

const dateTime = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

/** Unix seconds -> local date and time. */
export function formatUnix(ts) {
  return ts == null ? "–" : dateTime.format(new Date(ts * 1000));
}

/** ISO string (from the backend) -> local date and time. */
export function formatIso(iso) {
  return iso ? dateTime.format(new Date(iso)) : "–";
}

/** "7f8e21c030950b03" -> "7f8e…0b03" */
export function shortId(id) {
  return id && id.length > 10 ? `${id.slice(0, 4)}…${id.slice(-4)}` : id || "–";
}

/**
 * Text under a device's name: the IMEI, unless the name already is the IMEI
 * (no label set), in which case the short device ID.
 */
export function secondLine(label, imei, deviceId) {
  if (imei && label !== imei) return `IMEI ${imei}`;
  return `ID ${shortId(deviceId)}`;
}

/** Human-readable gap between two Unix timestamps, e.g. "+2 min". */
export function gap(from, to) {
  if (from == null || to == null) return "";
  const s = to - from;
  if (s < 0) return "earlier!";
  if (s < 60) return `+${s} s`;
  if (s < 3600) return `+${Math.round(s / 60)} min`;
  if (s < 86400) return `+${Math.round(s / 3600)} h`;
  return `+${Math.round(s / 86400)} d`;
}

export function formatBytes(n) {
  if (n == null) return "–";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 ** 2).toFixed(1)} MB`;
}
