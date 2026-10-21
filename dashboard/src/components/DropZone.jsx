import { useRef, useState } from "react";

/** Drag-and-drop or click to choose one or more photos (JPEG) or videos (MP4/MOV/3GP). */
export default function DropZone({ onFiles, busy }) {
  const input = useRef(null);
  const [over, setOver] = useState(false);

  const take = (list) => {
    const files = Array.from(list || []);
    if (files.length) onFiles(files);
  };

  return (
    <div
      className={`dropzone ${over ? "over" : ""} ${busy ? "busy" : ""}`}
      role="button"
      tabIndex={0}
      onClick={() => !busy && input.current?.click()}
      onKeyDown={(e) => {
        if ((e.key === "Enter" || e.key === " ") && !busy) {
          e.preventDefault();
          input.current?.click();
        }
      }}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (!busy) take(e.dataTransfer.files);
      }}
    >
      <input
        ref={input}
        type="file"
        accept="image/jpeg,video/mp4,video/quicktime,video/3gpp,.jpg,.jpeg,.mp4,.mov,.m4v,.3gp"
        multiple
        hidden
        onChange={(e) => {
          take(e.target.files);
          e.target.value = "";
        }}
      />
      <svg width="40" height="40" viewBox="0 0 24 24" aria-hidden="true" className="dropzone-icon">
        <path
          d="M12 16V4m0 0l-4 4m4-4l4 4M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <p className="dropzone-title">{busy ? "Analyzing…" : "Drop photos or videos here, or click to choose"}</p>
      <p className="muted small">
        JPEG photos or MP4 / MOV / 3GP videos received over Bluetooth. Several copies of the same
        file build its tree.
      </p>
    </div>
  );
}
