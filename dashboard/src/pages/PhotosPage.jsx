import { useEffect, useState } from "react";
import { api } from "../api.js";
import { formatIso, formatUnix, shortId } from "../format.js";
import MediaThumb from "../components/MediaThumb.jsx";

/** Every distinct original photo or video (Image ID) seen so far. */
export default function PhotosPage() {
  const [images, setImages] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.images().then(setImages, (e) => setError(e.message));
  }, []);

  return (
    <div className="stack">
      <section>
        <h1>Photos &amp; videos</h1>
        <p className="muted">
          Each card is one original photo or video. All uploaded copies of it are merged into one
          tree.
        </p>
      </section>

      {error && <p className="error-text">{error}</p>}
      {!images && !error && <p className="muted">Loading…</p>}
      {images?.length === 0 && (
        <div className="empty">
          <p>No stamped photos or videos uploaded yet.</p>
          <a className="btn" href="#/">
            Analyze a file
          </a>
        </div>
      )}

      {images?.length > 0 && (
        <div className="grid">
          {images.map((im) => (
            <a key={im.image_id} className="card photo-card" href={`#/photos/${im.image_id}`}>
              <div className="photo-thumb">
                <MediaThumb src={api.fileUrl(im.latest_upload_id)} media={im.media} />
              </div>
              <div className="photo-body">
                <div className="row-between">
                  <strong className="mono">{shortId(im.image_id)}</strong>
                  <span className="row-gap">
                    {im.media === "video" && <span className="badge">video</span>}
                    <span className="badge">{im.devices} devices</span>
                  </span>
                </div>
                <div className="small">
                  Origin: <strong>{im.origin.label}</strong>
                </div>
                <div className="muted small">First sent {formatUnix(im.first_hop_at)}</div>
                <div className="muted small">
                  {im.copies} {im.copies === 1 ? "copy" : "copies"} · last upload{" "}
                  {formatIso(im.last_upload_at)}
                </div>
              </div>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
