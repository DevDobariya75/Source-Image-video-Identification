import { formatUnix, gap, secondLine } from "../format.js";

/** One copy's chain as a row of steps: A -> B -> C. */
export default function ChainPath({ hops }) {
  if (!hops?.length) return null;
  return (
    <ol className="chain">
      {hops.map((h, i) => (
        <li key={h.position ?? i} className="chain-step">
          {i > 0 && (
            <span className="chain-arrow" aria-hidden="true">
              <span className="chain-gap">{gap(hops[i - 1].timestamp, h.timestamp)}</span>→
            </span>
          )}
          <div className={`chain-card ${i === 0 ? "origin" : ""}`}>
            <div className="chain-head">
              <span className="chain-num">{i + 1}</span>
              <strong className="chain-label">{h.label}</strong>
              <RoleBadge role={h.role} origin={i === 0} />
            </div>
            <div className="mono small">{secondLine(h.label, h.imei, h.device_id)}</div>
            <div className="muted small">{formatUnix(h.timestamp)}</div>
          </div>
        </li>
      ))}
    </ol>
  );
}

export function RoleBadge({ role, origin }) {
  if (origin) return <span className="badge badge-origin">origin</span>;
  if (role === "claim") return <span className="badge badge-claim">received</span>;
  return <span className="badge badge-send">forwarded</span>;
}
