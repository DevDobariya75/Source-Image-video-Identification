import { useMemo } from "react";
import { Background, Controls, Handle, Position, ReactFlow } from "@xyflow/react";
import { hierarchy, tree as d3tree } from "d3-hierarchy";
import { formatUnix, secondLine } from "../format.js";
import MediaThumb from "./MediaThumb.jsx";

const NODE_W = 190;
const NODE_H = 86;

function PhotoNode({ data }) {
  return (
    <div className="tnode tnode-photo">
      {data.thumb ? (
        <MediaThumb src={data.thumb} media={data.media} />
      ) : (
        <span>{data.media === "video" ? "Video" : "Photo"}</span>
      )}
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
}

function DeviceNode({ data, selected }) {
  const n = data.node;
  const kind = n.depth === 1 ? "origin" : n.role === "claim" ? "claim" : "send";
  return (
    <div className={`tnode tnode-device tnode-${kind} ${selected ? "selected" : ""}`}>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <div className="tnode-title">
        <span className="tnode-depth">{n.depth}</span>
        <strong>{n.label}</strong>
      </div>
      <div className="mono small">{secondLine(n.label, n.imei, n.device_id)}</div>
      <div className="muted small">{formatUnix(n.first_seen)}</div>
      {n.copies.length > 0 && (
        <span className="tnode-copies" title="Uploaded copies that ended at this device">
          {n.copies.length} {n.copies.length === 1 ? "copy" : "copies"}
        </span>
      )}
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
}

const nodeTypes = { photo: PhotoNode, device: DeviceNode };

/** Lay the nested tree out top-down with d3-hierarchy, then hand it to React Flow. */
function layout(root, thumb, media) {
  const h = hierarchy(root, (d) => d.children);
  d3tree().nodeSize([NODE_W + 30, NODE_H + 60])(h);

  const nodes = [];
  const edges = [];
  h.each((d) => {
    const isPhoto = d.data.kind === "image";
    nodes.push({
      id: d.data.id,
      type: isPhoto ? "photo" : "device",
      position: { x: d.x - (isPhoto ? 40 : NODE_W / 2), y: d.y },
      data: isPhoto ? { thumb, media } : { node: d.data },
      selectable: !isPhoto,
    });
    if (d.parent) {
      edges.push({
        id: `${d.parent.data.id}->${d.data.id}`,
        source: d.parent.data.id,
        target: d.data.id,
        type: "smoothstep",
        animated: d.data.role === "claim",
        className: d.data.role === "claim" ? "edge-claim" : "",
      });
    }
  });
  return { nodes, edges };
}

/** Zoomable forwarding tree. onSelect(node | null) reports clicks on devices. */
export default function ForwardTree({ tree, thumb, selectedId, onSelect, height = 520 }) {
  const { nodes, edges } = useMemo(
    () => layout(tree.root, thumb, tree.media),
    [tree, thumb],
  );
  const shown = useMemo(
    () => nodes.map((n) => ({ ...n, selected: n.id === selectedId })),
    [nodes, selectedId],
  );

  return (
    <div className="tree-wrap" style={{ height }}>
      <ReactFlow
        nodes={shown}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2, maxZoom: 1.1 }}
        minZoom={0.2}
        nodesDraggable={false}
        nodesConnectable={false}
        colorMode="system"
        proOptions={{ hideAttribution: true }}
        onNodeClick={(_, n) => onSelect?.(n.type === "device" ? n.data.node : null)}
        onPaneClick={() => onSelect?.(null)}
      >
        <Background gap={20} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
