// Thin wrapper around the FastAPI backend (see backend/main.py).
// In development Vite proxies /api to the backend; for a separate deployment
// set VITE_API_URL (e.g. http://192.168.1.10:8000) at build time.
const BASE = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "");

async function request(path, options) {
  let res;
  try {
    res = await fetch(BASE + path, options);
  } catch {
    throw new Error("Cannot reach the backend. Is `uvicorn main:app` running?");
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    /* empty or non-JSON body */
  }
  if (!res.ok) {
    const detail = body?.detail;
    const msg = typeof detail === "string" ? detail : `${res.status} ${res.statusText}`;
    throw new Error(msg);
  }
  return body;
}

export const api = {
  health: () => request("/api/health"),

  upload(file) {
    const form = new FormData();
    form.append("file", file);
    return request("/api/upload", { method: "POST", body: form });
  },

  images: () => request("/api/images"),
  tree: (imageId) => request(`/api/images/${encodeURIComponent(imageId)}/tree`),

  uploads: (imageId) =>
    request(imageId ? `/api/uploads?image_id=${encodeURIComponent(imageId)}` : "/api/uploads"),
  deleteUpload: (id) => request(`/api/uploads/${id}`, { method: "DELETE" }),
  fileUrl: (id) => `${BASE}/api/uploads/${id}/file`,

  devices: () => request("/api/devices"),
  device: (id) => request(`/api/devices/${id}`),
  updateDevice: (id, data) =>
    request(`/api/devices/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),
};
