import { useState } from "react";
import { api } from "../api.js";
import DropZone from "../components/DropZone.jsx";
import ChainPath from "../components/ChainPath.jsx";
import ForwardTree from "../components/ForwardTree.jsx";
import { formatBytes, shortId } from "../format.js";

let nextKey = 1;

/** Upload one or more photos or videos and show the chain hidden in each. */
export default function UploadPage() {
  const [results, setResults] = useState([]);
  const [busy, setBusy] = useState(false);

  const patch = (key, fields) =>
    setResults((rs) => rs.map((r) => (r.key === key ? { ...r, ...fields } : r)));

  async function handleFiles(files) {
    const batch = files.map((f) => ({ key: nextKey++, file: f, status: "pending" }));
    setResults((rs) => [...[...batch].reverse(), ...rs]); // newest on top
    setBusy(true);
    // One at a time: decoding is CPU-heavy on the server.
    for (const item of batch) {
      try {
        const data = await api.upload(item.file);
        patch(item.key, { status: "done", data });
      } catch (e) {
        patch(item.key, { status: "error", error: e.message });
      }
    }
    setBusy(false);
  }

  const latestTree = results.find((r) => r.data?.tree);

  return (
    <div className="stack">
      <section>
        <h1>Analyze a photo or video</h1>
        <p className="muted">
          Upload a photo or video stamped by the app to see every device it passed through. Upload
          copies from different phones to see how it spread.
        </p>
      </section>

      <DropZone onFiles={handleFiles} busy={busy} />

      {results.length > 0 && (
        <section className="stack-sm">
          <div className="row-between">
            <h2>Results</h2>
            <button className="btn btn-ghost" onClick={() => setResults([])} disabled={busy}>
              Clear
            </button>
          </div>
          {results.map((r) => (
            <ResultCard key={r.key} item={r} />
          ))}
        </section>
      )}

      {latestTree && (
        <section className="stack-sm">
          <div className="row-between">
            <h2>Forwarding tree</h2>
            <a className="btn" href={`#/photos/${latestTree.data.tree.image_id}`}>
              Open full view
            </a>
          </div>
          <ForwardTree
            tree={latestTree.data.tree}
            thumb={api.fileUrl(latestTree.data.upload.id)}
            height={420}
          />
        </section>
      )}
    </div>
  );
}

function ResultCard({ item }) {
  const { file, status, data, error } = item;
  const up = data?.upload;

  let badge;
  if (status === "pending") badge = <span className="badge">analyzing…</span>;
  else if (status === "error") badge = <span className="badge badge-bad">error</span>;
  else if (!up.found) badge = <span className="badge badge-warn">no chain</span>;
  else badge = <span className="badge badge-good">{up.hop_count} devices</span>;

  return (
    <article className="card result">
      <div className="result-head">
        <div className="result-name">
          <strong title={file.name}>{file.name}</strong>
          <span className="muted small">{formatBytes(file.size)}</span>
        </div>
        <div className="result-tags">
          {data?.duplicate && <span className="badge">already uploaded</span>}
          {badge}
        </div>
      </div>

      {status === "error" && <p className="error-text">{error}</p>}

      {up && !up.found && (
        <p className="muted small">
          No forwarding chain in this file. It was never stamped by the app, or it was
          re-compressed on the way (sent as a WhatsApp photo or video, edited, or a screenshot).
        </p>
      )}

      {up?.found && (
        <>
          <ChainPath hops={up.hops} />
          <div className="result-foot muted small">
            <span>
              {up.media === "video" ? "Video" : "Photo"} ID <span className="mono">{shortId(up.image_id)}</span>
            </span>
            <span>Decode confidence {Math.round(up.confidence * 100)}%</span>
            <a href={`#/photos/${up.image_id}`}>View tree →</a>
          </div>
        </>
      )}
    </article>
  );
}
