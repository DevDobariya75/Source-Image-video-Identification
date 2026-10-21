/**
 * Thumbnail of an uploaded photo or video.
 * Videos show their first frame (no controls) unless `controls` is set.
 */
export default function MediaThumb({ src, media, controls = false, className = "" }) {
  if (!src) return null;
  if (media === "video") {
    return (
      <span className={`media-thumb ${className}`}>
        <video
          // "#t=0.1" makes browsers render a first frame instead of a blank box
          src={controls ? src : `${src}#t=0.1`}
          muted
          playsInline
          preload="metadata"
          controls={controls}
        />
        {!controls && (
          <span className="media-play" aria-label="Video">
            <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
              <path d="M8 5v14l11-7z" fill="currentColor" />
            </svg>
          </span>
        )}
      </span>
    );
  }
  return (
    <span className={`media-thumb ${className}`}>
      <img src={src} alt="" loading="lazy" />
    </span>
  );
}
