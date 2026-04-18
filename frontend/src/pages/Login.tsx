import { useState } from "react";
import { useNavigate } from "react-router-dom";

const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8080";

export default function Login() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      if (mode === "register") {
        const r = await fetch(`${BASE}/auth/register`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, email, password }),
        });
        if (!r.ok) {
          const err = await r.json();
          throw new Error(err.detail ?? "Registration failed");
        }
        setMode("login");
        setError("Registered! Please log in.");
        return;
      }

      // login — must send as form data (OAuth2PasswordRequestForm)
      const form = new URLSearchParams();
      form.append("username", username);
      form.append("password", password);

      const r = await fetch(`${BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: form.toString(),
      });

      if (!r.ok) {
        const err = await r.json();
        throw new Error(err.detail ?? "Login failed");
      }

      const { access_token, refresh_token } = await r.json();
      localStorage.setItem("access_token", access_token);
      localStorage.setItem("refresh_token", refresh_token);
      navigate("/");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: "var(--bg)" }}>
      <div style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: "2rem", width: "100%", maxWidth: "380px", boxShadow: "var(--shadow)" }}>
        <h1 style={{ fontSize: "1.25rem", fontWeight: 700, marginBottom: "1.5rem" }}>
          {mode === "login" ? "Sign in" : "Create account"}
        </h1>

        {error && (
          <p style={{ color: mode === "login" ? "var(--danger)" : "green", marginBottom: "1rem", fontSize: "0.875rem" }}>
            {error}
          </p>
        )}

        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
          <input
            type="text"
            placeholder="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
          {mode === "register" && (
            <input
              type="email"
              placeholder="Email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              style={{ border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: "0.45rem 0.75rem", fontSize: "0.875rem", outline: "none" }}
            />
          )}
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            style={{ border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: "0.45rem 0.75rem", fontSize: "0.875rem", outline: "none" }}
          />
          <button className="primary" type="submit" disabled={loading}>
            {loading ? "Please wait…" : mode === "login" ? "Sign in" : "Register"}
          </button>
        </form>

        <p style={{ marginTop: "1rem", fontSize: "0.8rem", color: "var(--muted)", textAlign: "center" }}>
          {mode === "login" ? "No account? " : "Already have one? "}
          <button
            style={{ background: "none", border: "none", color: "var(--primary)", cursor: "pointer", padding: 0, fontSize: "0.8rem" }}
            onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}
          >
            {mode === "login" ? "Register" : "Sign in"}
          </button>
        </p>
      </div>
    </div>
  );
}
