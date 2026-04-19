import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { documentsApi } from "../api/documents";

export default function Join() {
  const { token } = useParams<{ token: string }>();
  const navigate = useNavigate();
  const [error, setError] = useState("");

  useEffect(() => {
    if (!token) return;
    documentsApi
      .joinViaLink(token)
      .then(({ document_id }) => navigate(`/documents/${document_id}`, { replace: true }))
      .catch((e: Error) => setError(e.message));
  }, [token, navigate]);

  if (error) {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: "1rem" }}>
        <p style={{ color: "var(--danger)" }}>{error}</p>
        <button className="primary" onClick={() => navigate("/")}>Go to dashboard</button>
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <p className="spinner">Joining document…</p>
    </div>
  );
}
