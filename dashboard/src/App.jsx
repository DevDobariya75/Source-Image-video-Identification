import { useEffect, useState } from "react";
import { api } from "./api.js";
import UploadPage from "./pages/UploadPage.jsx";
import PhotosPage from "./pages/PhotosPage.jsx";
import PhotoDetail from "./pages/PhotoDetail.jsx";
import DevicesPage from "./pages/DevicesPage.jsx";

// Minimal hash router: #/ , #/photos , #/photos/<imageId> , #/devices
function useHashRoute() {
  const read = () => window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const [parts, setParts] = useState(read);
  useEffect(() => {
    const onChange = () => setParts(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return parts;
}

const NAV = [
  { href: "#/", label: "Analyze", match: (p) => p.length === 0 },
  { href: "#/photos", label: "Media", match: (p) => p[0] === "photos" },
  { href: "#/devices", label: "Devices", match: (p) => p[0] === "devices" },
];

function BackendStatus() {
  const [ok, setOk] = useState(null);
  useEffect(() => {
    let alive = true;
    const check = () =>
      api.health().then(
        () => alive && setOk(true),
        () => alive && setOk(false),
      );
    check();
    const t = setInterval(check, 15000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);
  const label = ok == null ? "Checking…" : ok ? "Backend online" : "Backend offline";
  return (
    <span className={`status ${ok == null ? "" : ok ? "status-ok" : "status-bad"}`} title={label}>
      <span className="status-dot" aria-hidden="true" />
      <span className="status-text">{label}</span>
    </span>
  );
}

export default function App() {
  const parts = useHashRoute();

  let page;
  if (parts[0] === "photos" && parts[1]) page = <PhotoDetail imageId={parts[1]} />;
  else if (parts[0] === "photos") page = <PhotosPage />;
  else if (parts[0] === "devices") page = <DevicesPage />;
  else page = <UploadPage />;

  return (
    <>
      <header className="topbar">
        <a href="#/" className="brand">
          <img src="/favicon.svg" alt="" width="24" height="24" />
          <span>Forwarding Chain</span>
        </a>
        <nav>
          {NAV.map((n) => (
            <a key={n.href} href={n.href} className={n.match(parts) ? "active" : ""}>
              {n.label}
            </a>
          ))}
        </nav>
        <BackendStatus />
      </header>
      <main className="container">{page}</main>
    </>
  );
}
