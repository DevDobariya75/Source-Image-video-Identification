"""
tree.py - Merge the chains of several copies of one photo into a tree.

Each uploaded copy only knows its own path, e.g.
    copy 1: A -> B -> C
    copy 2: A -> D
Copies that share a prefix share those nodes, so together they form:

    (photo)
      └─ A
          ├─ B
          │   └─ C      <- copy 1 ended here
          └─ D          <- copy 2 ended here

A node is identified by its path from the root (the same device can appear in
two different branches, e.g. A -> B -> A, and those are different nodes).
"""

from typing import Dict, List


def _label(device_id: str, imei, labels: Dict[str, str]) -> str:
    return labels.get(device_id) or imei or device_id[:8]


def build_tree(image_id: str, uploads: List[dict], labels: Dict[str, str]) -> dict:
    """
    uploads: [{"id", "filename", "uploaded_at", "hops": [hop dicts in order]}]
    labels : device_id -> label set on the dashboard
    Returns {"image_id", "root", "nodes", "edges", "stats"}. "root" is nested;
    "nodes" / "edges" are the same tree flattened (handy for React Flow).
    """
    root = {"id": "root", "kind": "image", "label": "Photo", "depth": 0,
            "children": [], "copies": []}
    nodes = {"root": root}
    anomalies = []

    for up in uploads:
        node = root
        prev_ts = None
        for hop in up["hops"]:
            key = f'{node["id"]}/{hop["device_id"]}'
            child = nodes.get(key)
            if child is None:
                child = {"id": key, "kind": "device", "parent": node["id"],
                         "depth": node["depth"] + 1,
                         "device_id": hop["device_id"], "imei": hop["imei"],
                         "label": _label(hop["device_id"], hop["imei"], labels),
                         "role": hop["role"],
                         "first_seen": hop["timestamp"], "last_seen": hop["timestamp"],
                         "children": [], "copies": []}
                nodes[key] = child
                node["children"].append(child)
            else:
                child["first_seen"] = min(child["first_seen"], hop["timestamp"])
                child["last_seen"] = max(child["last_seen"], hop["timestamp"])
                child["imei"] = child["imei"] or hop["imei"]
                if hop["role"] == "claim":
                    child["role"] = "claim"
            if prev_ts is not None and hop["timestamp"] < prev_ts:
                anomalies.append({"upload_id": up["id"], "node": key,
                                  "issue": "timestamp earlier than previous hop "
                                           "(phone clock wrong, or chain edited)"})
            prev_ts = hop["timestamp"]
            node = child
        node["copies"].append({"upload_id": up["id"], "filename": up["filename"],
                               "uploaded_at": up["uploaded_at"]})

    for n in nodes.values():
        n["children"].sort(key=lambda c: c.get("first_seen", 0))

    flat = [{k: v for k, v in n.items() if k != "children"} for n in nodes.values()]
    edges = [{"id": f'{n["parent"]}->{n["id"]}', "source": n["parent"], "target": n["id"]}
             for n in flat if "parent" in n]
    devices = {n["device_id"] for n in flat if n["kind"] == "device"}
    return {
        "image_id": image_id,
        "root": root,
        "nodes": flat,
        "edges": edges,
        "stats": {"copies": len(uploads), "devices": len(devices),
                  "max_depth": max((n["depth"] for n in flat), default=0)},
        "anomalies": anomalies,
    }
