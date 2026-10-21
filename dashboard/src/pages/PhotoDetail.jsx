import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import ForwardTree from "../components/ForwardTree.jsx";
import { RoleBadge } from "../components/ChainPath.jsx";
import { formatIso, formatUnix, shortId } from "../format.js";
import MediaThumb from "../components/MediaThumb.jsx";

/** The forwarding tree of one photo or video, with details of the clicked device. */
export default function PhotoDetail({ imageId }) {
  const [tree, setTree] = useState(null);
  const [uploads, setUploads] = useState([]);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);

  const load = useCallback(() => {
    Promise.all([api.tree(imageId), api.uploads(imageId)]).then(
      ([t, u]) => {
        setTree(t);
        setUploads(u);
        setError(null);
      },
      (e) => setError(e.message),
    );
  }, [imageId]);

  useEffect(load, [load]);

  async function remove(id) {
    if (!window.confirm("Delete this uploaded copy from the dashboard?")) return;
    try {
      await api.deleteUpload(id);
      setSelected(null);
      if (uploads.length <= 1) window.location.hash = "#/photos";
      else load();
    } catch (e) {
      setError(e.message);
    }
  }

  if (error)
    return (
      <div className="stack">
        <a href="#/photos">← All photos &amp; videos</a>
        <p className="error-text">{error}</p>
      </div>
    );
  if (!tree) return <p className="muted">Loading…</p>;

  const thumb = uploads[0] ? api.fileUrl(uploads[0].id) : null;
  const noun = tree.media === "video" ? "Video" : "Photo";
  const what = noun.toLowerCase();
  const s = tree.stats;

  return (
    <div className="stack">
      <div>
        <a href="#/photos" className="small">
          ← All photos &amp; videos
        </a>
        <h1>
          {noun} <span className="mono">{shortId(tree.image_id)}</span>
        </h1>
        <p className="muted mono small">{tree.image_id}</p>
      </div>

      <div className="stats">
        <Stat label="Devices" value={s.devices} />
        <Stat label="Copies uploaded" value={s.copies} />
        <Stat label="Longest chain" value={`${s.max_depth} hops`} />
        <Stat label="Origin" value={tree.root.children.map((c) => c.label).join(", ")} />
      </div>

      {tree.anomalies.length > 0 && (
        <div className="alert">
          <strong>Check these hops:</strong>
          <ul>
            {tree.anomalies.map((a, i) => (
              <li key={i}>
                Copy #{a.upload_id}: {a.issue}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="detail-layout">
        <ForwardTree
          tree={tree}
          thumb={thumb}
          selectedId={selected?.id}
          onSelect={setSelected}
          height={560}
        />
        <aside className="card side">
          {selected ? (
            <NodeDetails node={selected} uploads={uploads} onDelete={remove} />
          ) : (
            <>
              {thumb && (
                <MediaThumb src={thumb} media={tree.media} controls className="preview" />
              )}
              <h3>How to read this</h3>
              <ul className="legend">
                <li>
                  <span className="dot dot-origin" /> Origin: the first phone to stamp the {what}
                </li>
                <li>
                  <span className="dot dot-send" /> Forwarded the {what} on
                </li>
                <li>
                  <span className="dot dot-claim" /> Received it (end of a chain)
                </li>
              </ul>
              <p className="muted small">
                Each branch is a different path the {what} took. Click a device for details.
              </p>
              <h3>Uploaded copies</h3>
              <CopyList copies={uploads} onDelete={remove} />
            </>
          )}
        </aside>
      </div>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="stat card">
      <div className="muted small">{label}</div>
      <div className="stat-value">{value}</div>
    </div>
  );
}

function NodeDetails({ node, uploads, onDelete }) {
  const ids = new Set(node.copies.map((c) => c.upload_id));
  const copies = uploads.filter((u) => ids.has(u.id));
  return (
    <div className="stack-sm">
      <div className="row-between">
        <h3>{node.label}</h3>
        <RoleBadge role={node.role} origin={node.depth === 1} />
      </div>
      <dl className="kv">
        <dt>IMEI</dt>
        <dd className="mono">{node.imei || "unknown"}</dd>
        <dt>Device ID</dt>
        <dd className="mono">{node.device_id}</dd>
        <dt>Position</dt>
        <dd>Hop {node.depth}</dd>
        <dt>Stamped</dt>
        <dd>
          {formatUnix(node.first_seen)}
          {node.last_seen !== node.first_seen && ` – ${formatUnix(node.last_seen)}`}
        </dd>
        <dt>Forwarded to</dt>
        <dd>{node.children.length ? node.children.map((c) => c.label).join(", ") : "nobody (yet)"}</dd>
      </dl>
      <a className="small" href="#/devices">
        Rename this device →
      </a>
      {copies.length > 0 && (
        <>
          <h3>Copies that ended here</h3>
          <CopyList copies={copies} onDelete={onDelete} />
        </>
      )}
    </div>
  );
}

function CopyList({ copies, onDelete }) {
  if (!copies.length) return <p className="muted small">None.</p>;
  return (
    <ul className="copies">
      {copies.map((u) => (
        <li key={u.id}>
          <MediaThumb src={api.fileUrl(u.id)} media={u.media} className="copy-thumb" />
          <div className="copies-text">
            <span title={u.filename}>{u.filename}</span>
            <span className="muted small">
              {u.hop_count} hops · {formatIso(u.uploaded_at)}
            </span>
          </div>
          <a className="icon-btn" href={api.fileUrl(u.id)} download title="Download">
            ↓
          </a>
          <button className="icon-btn" onClick={() => onDelete(u.id)} title="Delete">
            ✕
          </button>
        </li>
      ))}
    </ul>
  );
}
