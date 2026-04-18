import { test, expect, type Page } from "@playwright/test";

// Unique suffix per test run so parallel runs don't collide.
const RUN_ID = Date.now();

async function register(page: Page, username: string, password: string) {
  await page.goto("/login");
  await page.getByRole("button", { name: /register/i }).click();
  await page.getByPlaceholder("Username").fill(username);
  await page.getByPlaceholder("Email").fill(`${username}@test.example`);
  await page.getByPlaceholder("Password").fill(password);
  await page.getByRole("button", { name: /register/i }).click();
  // Server redirects back to login mode after registration.
  await expect(page.getByText(/registered/i)).toBeVisible();
}

async function login(page: Page, username: string, password: string) {
  await page.goto("/login");
  await page.getByPlaceholder("Username").fill(username);
  await page.getByPlaceholder("Password").fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();
  // Successful login navigates to the dashboard.
  await expect(page).toHaveURL("/");
  await expect(page.getByText("My Documents")).toBeVisible();
}

// ── Test 1: Register & Login ──────────────────────────────────────────────────

test("register then login", async ({ page }) => {
  const username = `user_${RUN_ID}`;
  await register(page, username, "Password1!");

  // After registration we're on the login form — sign in.
  await page.getByPlaceholder("Username").fill(username);
  await page.getByPlaceholder("Password").fill("Password1!");
  await page.getByRole("button", { name: /sign in/i }).click();

  await expect(page).toHaveURL("/");
  await expect(page.getByText("My Documents")).toBeVisible();
});

// ── Test 2: Create document & auto-save ──────────────────────────────────────

test("create document and auto-save", async ({ page }) => {
  const username = `user_autosave_${RUN_ID}`;
  await register(page, username, "Password1!");
  await login(page, username, "Password1!");

  // Create a new document.
  await page.getByRole("button", { name: /new document/i }).click();
  await expect(page).toHaveURL(/\/documents\//);

  // Give the editor a moment to initialise.
  const editor = page.locator(".tiptap-wrap");
  await expect(editor).toBeVisible();

  // Type some text — this triggers auto-save after 1.5 s.
  await editor.click();
  await page.keyboard.type("Hello from E2E test");

  // Wait for the "Saved" status indicator.
  await expect(page.locator(".autosave-status.saved")).toBeVisible({ timeout: 10_000 });
});

// ── Test 3: Version history — save, edit, restore ────────────────────────────

test("save version then restore it", async ({ page }) => {
  const username = `user_versions_${RUN_ID}`;
  await register(page, username, "Password1!");
  await login(page, username, "Password1!");

  await page.getByRole("button", { name: /new document/i }).click();
  await expect(page).toHaveURL(/\/documents\//);

  const editor = page.locator(".tiptap-wrap");
  await expect(editor).toBeVisible();

  // Type v1 content and save a snapshot.
  await editor.click();
  await page.keyboard.type("Version one content");
  await page.getByRole("button", { name: /save version/i }).click();

  // Edit again to create v2.
  await editor.click();
  await page.keyboard.press("End");
  await page.keyboard.type(" — edited");

  // Open version history via toolbar.
  await page.getByTitle("Version history").click();
  const historyPanel = page.locator(".version-panel");
  await expect(historyPanel).toBeVisible();

  // There should be at least one saved version.
  const versionItems = historyPanel.locator(".version-item");
  await expect(versionItems.first()).toBeVisible({ timeout: 5_000 });

  // Register dialog handler BEFORE clicking restore so the confirm() is accepted.
  page.on("dialog", (d) => d.accept());
  await versionItems.first().getByRole("button", { name: /restore/i }).click();

  // After restore the component calls onClose() — panel disappears.
  await expect(historyPanel).not.toBeVisible({ timeout: 8_000 });
});

// ── Test 4: Editor title change persists ─────────────────────────────────────

test("title change triggers save", async ({ page }) => {
  const username = `user_title_${RUN_ID}`;
  await register(page, username, "Password1!");
  await login(page, username, "Password1!");

  await page.getByRole("button", { name: /new document/i }).click();
  await expect(page).toHaveURL(/\/documents\//);

  const titleInput = page.locator(".title-input");
  await expect(titleInput).toBeVisible();
  await titleInput.fill("My E2E Document");

  // Trigger auto-save by blurring.
  await page.keyboard.press("Tab");
  await expect(page.locator(".autosave-status.saved")).toBeVisible({ timeout: 10_000 });

  // Go back to dashboard and confirm the doc appears with new title.
  await page.getByRole("button", { name: /back/i }).click();
  await expect(page.getByText("My E2E Document")).toBeVisible({ timeout: 5_000 });
});

// ── Test 5: AI panel opens and shows compose UI ───────────────────────────────

test("AI panel opens", async ({ page }) => {
  const username = `user_ai_${RUN_ID}`;
  await register(page, username, "Password1!");
  await login(page, username, "Password1!");

  await page.getByRole("button", { name: /new document/i }).click();
  await expect(page).toHaveURL(/\/documents\//);

  // Open AI panel via the toolbar button.
  await page.getByRole("button", { name: /✨ AI/i }).click();
  const aiPanel = page.locator(".ai-panel");
  await expect(aiPanel).toBeVisible();

  // Compose tab should show the action selector.
  await expect(aiPanel.locator("select").first()).toBeVisible();

  // Close the panel.
  await aiPanel.getByRole("button", { name: /close ai panel/i }).click();
  await expect(aiPanel).not.toBeVisible();
});

// ── Test 6: Logout ────────────────────────────────────────────────────────────

test("logout redirects to login", async ({ page }) => {
  const username = `user_logout_${RUN_ID}`;
  await register(page, username, "Password1!");
  await login(page, username, "Password1!");

  await page.getByRole("button", { name: /sign out/i }).click();
  await expect(page).toHaveURL("/login");
});
