import { Fragment, useEffect, useState } from "react";
import { api } from "../api.js";
import { formatUnix, shortId } from "../format.js";

/** Every device seen in any chain; give each one a readable name. */
export default function DevicesPage() {
  const [devices, setDevices] = useState(null);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);

  useEffect(() => {
    api.devices().then(setDevices, (e) => setError(e.message));
  }, []);

  const saved = (d) =>
    setDevices((ds) => ds.map((x) => (x.device_id === d.device_id ? { ...x, label: d.label } : x)));

  return (
    <div className="stack">
      <section>
        <h1>Devices</h1>
        <p className="muted">
          Every phone that stamped a photo or video. Give a phone a name and it will show up in all trees
          with that name.
        </p>
      </section>

      {error && <p className="error-text">{error}</p>}
      {!devices && !error && <p className="muted">Loading…</p>}
      {devices?.length === 0 && <p className="muted">No devices seen yet.</p>}

      {devices?.length > 0 && (
        <div className="table-wrap card">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>IMEI</th>
                <th>Device ID</th>
                <th className="num">Files</th>
                <th>Last seen</th>
                <th aria-label="Expand" />
              </tr>
            </thead>
            <tbody>
              {devices.map((d) => (
                <Fragment key={d.device_id}>
                  <tr>
                    <td>
                      <LabelEditor device={d} onSaved={saved} />
                    </td>
                    <td className="mono">{d.imei || "–"}</td>
                    <td className="mono" title={d.device_id}>
                      {shortId(d.device_id)}
                    </td>
                    <td className="num">{d.images}</td>
                    <td className="nowrap">{formatUnix(d.last_seen)}</td>
                    <td>
                      <button
                        className="btn btn-ghost btn-sm"
                        aria-expanded={open === d.device_id}
                        onClick={() => setOpen(open === d.device_id ? null : d.device_id)}
                      >
                        {open === d.device_id ? "Hide" : "Files"}
                      </button>
                    </td>
                  </tr>
                  {open === d.device_id && (
                    <tr className="subrow">
                      <td colSpan={6}>
                        <DevicePhotos deviceId={d.device_id} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function LabelEditor({ device, onSaved }) {
  const [value, setValue] = useState(device.label || "");
  const [state, setState] = useState("idle"); // idle | saving | saved | error
  const dirty = value.trim() !== (device.label || "");

  async function save(e) {
    e.preventDefault();
    if (!dirty) return;
    setState("saving");
    try {
      const d = await api.updateDevice(device.device_id, {
        label: value.trim() || null,
        notes: device.notes,
      });
      onSaved(d);
      setState("saved");
    } catch {
      setState("error");
    }
  }

  return (
    <form className="label-form" onSubmit={save}>
      <input
        value={value}
        maxLength={80}
        placeholder="Add a name"
        aria-label={`Name for device ${device.device_id}`}
        onChange={(e) => {
          setValue(e.target.value);
          setState("idle");
        }}
      />
      {dirty && (
        <button className="btn btn-sm" disabled={state === "saving"}>
          {state === "saving" ? "…" : "Save"}
        </button>
      )}
      {!dirty && state === "saved" && <span className="small ok-text">Saved</span>}
      {state === "error" && <span className="small error-text">Failed</span>}
    </form>
  );
}

function DevicePhotos({ deviceId }) {
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    api.device(deviceId).then(setInfo, (e) => setError(e.message));
  }, [deviceId]);

  if (error) return <span className="error-text">{error}</span>;
  if (!info) return <span className="muted">Loading…</span>;
  return (
    <ul className="device-photos">
      {info.images.map((im) => (
        <li key={im.image_id}>
          <a href={`#/photos/${im.image_id}`} className="mono">
            {shortId(im.image_id)}
          </a>
          <span className="muted small">
            {im.position === 1 ? "origin" : `hop ${im.position}`} · {formatUnix(im.first_seen)}
          </span>
        </li>
      ))}
    </ul>
  );
}
