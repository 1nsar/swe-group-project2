/**
 * Lightweight word-level diff used by the AI panel to render
 * "original vs suggestion" side by side.
 *
 * Assignment 1 §2.5 ADR-004 + §2.2 AI Integration Design require the
 * suggestion to appear as a diff before any document change is applied.
 *
 * We implement a standard LCS (longest-common-subsequence) diff on tokens
 * rather than pulling in ``diff-match-patch``. This is:
 *   - ~100 lines, no deps, deterministic, easy to read in review.
 *   - Plenty fast for a few KB of suggested text (O(n*m) where n,m are
 *     word counts; typical selection is <2000 words).
 *
 * "Words" here means runs of non-whitespace separated by whitespace. We
 * keep whitespace tokens in the sequence so the rendered diff preserves
 * spacing and line breaks faithfully.
 */

export type DiffOp = "equal" | "insert" | "delete";

export interface DiffPart {
  op: DiffOp;
  text: string;
}

/** Split into words + whitespace runs, preserving both. */
export function tokenize(text: string): string[] {
  if (!text) return [];
  return text.match(/\s+|\S+/g) ?? [];
}

/**
 * Compute LCS length matrix for two token arrays.
 * Returns an (n+1) x (m+1) matrix.
 */
function buildLcsMatrix(a: string[], b: string[]): number[][] {
  const n = a.length;
  const m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      if (a[i - 1] === b[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }
  return dp;
}

/** Walk the LCS matrix backwards and emit a sequence of ops. */
function emitOps(a: string[], b: string[], dp: number[][]): DiffPart[] {
  const parts: DiffPart[] = [];
  let i = a.length;
  let j = b.length;
  while (i > 0 && j > 0) {
    if (a[i - 1] === b[j - 1]) {
      parts.push({ op: "equal", text: a[i - 1] });
      i--;
      j--;
    } else if (dp[i - 1][j] >= dp[i][j - 1]) {
      parts.push({ op: "delete", text: a[i - 1] });
      i--;
    } else {
      parts.push({ op: "insert", text: b[j - 1] });
      j--;
    }
  }
  while (i > 0) {
    parts.push({ op: "delete", text: a[i - 1] });
    i--;
  }
  while (j > 0) {
    parts.push({ op: "insert", text: b[j - 1] });
    j--;
  }
  parts.reverse();
  return coalesce(parts);
}

/** Merge adjacent parts of the same op into one — nicer to render. */
function coalesce(parts: DiffPart[]): DiffPart[] {
  const out: DiffPart[] = [];
  for (const p of parts) {
    const last = out[out.length - 1];
    if (last && last.op === p.op) {
      last.text += p.text;
    } else {
      out.push({ ...p });
    }
  }
  return out;
}

/**
 * Compute a word-level diff between ``original`` and ``suggestion``.
 *
 * Returns an array of parts in document order:
 *   { op: "equal",  text }   — same in both
 *   { op: "delete", text }   — present in original, missing in suggestion
 *   { op: "insert", text }   — present in suggestion, missing in original
 */
export function diffWords(original: string, suggestion: string): DiffPart[] {
  const a = tokenize(original);
  const b = tokenize(suggestion);
  if (a.length === 0 && b.length === 0) return [];
  if (a.length === 0) return [{ op: "insert", text: suggestion }];
  if (b.length === 0) return [{ op: "delete", text: original }];
  const dp = buildLcsMatrix(a, b);
  return emitOps(a, b, dp);
}
