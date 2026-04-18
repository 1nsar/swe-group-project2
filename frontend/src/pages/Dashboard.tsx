import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { documentsApi, type DocumentSummary } from "../api/documents";

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export default function Dashboard() {
  const navigate = useNavigate();
  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    documentsApi
      .list()
      .then(setDocs)
      .catch(() => setError("Failed to load documents."))
      .finally(() => setLoading(false));
  }, []);

  async function handleCreate() {
    setCreating(true);
    try {
      const doc = await documentsApi.create("Untitled Document");
      navigate(`/documents/${doc.id}`);
    } catch {
      setError("Could not create document.");
      setCreating(false);
    }
  }

  async function handleDelete(e: React.MouseEvent, id: string) {
    e.stopPropagation();
    if (!confirm("Delete this document?")) return;
    await documentsApi.delete(id);
    setDocs((prev) => prev.filter((d) => d.id !== id));
  }

  return (
    <div className="page">
      <div className="topbar">
        <h1>My Documents</h1>
        <button className="ghost" onClick={() => {
          localStorage.removeItem("access_token");
          localStorage.removeItem("refresh_token");
          navigate("/login");
        }}>Sign out</button>
      </div>

      {error && <p style={{ color: "var(--danger)", marginBottom: "1rem" }}>{error}</p>}

      {loading ? (
        <p className="spinner">Loading…</p>
      ) : (
        <div className="doc-grid">
          {/* New document card */}
          <div
            className="new-doc-card"
            onClick={handleCreate}
            role="button"
            aria-label="Create new document"
          >
            <span style={{ fontSize: "2rem" }}>+</span>
            <span>{creating ? "Creating…" : "New Document"}</span>
          </div>

          {docs.map((doc) => (
            <div
              key={doc.id}
              className="doc-card"
              onClick={() => navigate(`/documents/${doc.id}`)}
            >
              <h3 title={doc.title}>{doc.title || "Untitled Document"}</h3>
              <p className="meta">Updated {formatDate(doc.updated_at)}</p>
              <p className="meta">Created {formatDate(doc.created_at)}</p>
              <div className="card-actions">
                <button
                  className="ghost"
                  onClick={(e) => {
                    e.stopPropagation();
                    navigate(`/documents/${doc.id}`);
                  }}
                >
                  Open
                </button>
                <button className="danger" onClick={(e) => handleDelete(e, doc.id)}>
                  Delete
                </button>
              </div>
            </div>
          ))}

          {docs.length === 0 && !loading && (
            <p className="empty-state" style={{ gridColumn: "1/-1" }}>
              No documents yet. Create one to get started.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
