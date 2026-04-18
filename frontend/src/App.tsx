import { Routes, Route, Navigate } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import Editor from "./pages/Editor";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Dashboard />} />
      <Route path="/documents/:id" element={<Editor />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
