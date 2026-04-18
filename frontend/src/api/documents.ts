import { api } from "./client";

export interface Document {
  id: string;
  title: string;
  content: unknown;
  owner_id: string;
  created_at: string;
  updated_at: string;
}

export interface DocumentSummary {
  id: string;
  title: string;
  owner_id: string;
  created_at: string;
  updated_at: string;
}

export interface VersionSummary {
  id: string;
  version_number: number;
  title: string;
  saved_by: string;
  saved_at: string;
}

export const documentsApi = {
  list: () => api.get<DocumentSummary[]>("/api/documents"),

  create: (title = "Untitled Document") =>
    api.post<Document>("/api/documents", { title }),

  get: (id: string) => api.get<Document>(`/api/documents/${id}`),

  update: (id: string, payload: { title?: string; content?: unknown }) =>
    api.put<Document>(`/api/documents/${id}`, payload),

  delete: (id: string) => api.delete<void>(`/api/documents/${id}`),

  listVersions: (id: string) =>
    api.get<VersionSummary[]>(`/api/documents/${id}/versions`),

  saveVersion: (id: string) =>
    api.post<VersionSummary>(`/api/documents/${id}/versions`),

  restoreVersion: (docId: string, versionId: string) =>
    api.post<Document>(`/api/documents/${docId}/versions/${versionId}/restore`),
};
