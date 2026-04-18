import { useEffect, useState } from "react";
import { documentsApi, type VersionSummary } from "../api/documents";

interface Props {
  docId: string;
  onClose: () => void;
  onRestored: () => void;
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function VersionHistory({ docId, onClose, onRestored }: Props) {
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [restoring, setRestoring] = useState<string | null>(null);

  useEffect(() => {
    documentsApi
      .listVersions(docId)
      .then(setVersions)
      .finally(() => setLoading(false));
  }, [docId]);

  async function handleRestore(versionId: string) {
    if (!confirm("Restore this version? The current state will be saved as a snapshot first.")) return;
    setRestoring(versionId);
    try {
      await documentsApi.restoreVersion(docId, versionId);
      onRestored();
      onClose();
    } catch {
      alert("Failed to restore version.");
    } finally {
      setRestoring(null);
    }
  }

  return (
    <div className="version-panel">
      <div className="version-panel-header">
        <span>Version History</span>
        <button className="ghost" onClick={onClose} style={{ padding: "0.2rem 0.6rem" }}>✕</button>
      </div>

      <div className="version-list">
        {loading && <p className="spinner">Loading…</p>}

        {!loading && versions.length === 0 && (
          <p className="empty-state">No versions saved yet.</p>
        )}

        {versions.map((v) => (
          <div key={v.id} className="version-item">
            <span className="v-num">Version {v.version_number}</span>
            <span className="v-meta">{v.title || "Untitled"}</span>
            <span className="v-meta">{formatDate(v.saved_at)}</span>
            <button
              className="ghost"
              disabled={restoring === v.id}
              onClick={() => handleRestore(v.id)}
            >
              {restoring === v.id ? "Restoring…" : "Restore"}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
