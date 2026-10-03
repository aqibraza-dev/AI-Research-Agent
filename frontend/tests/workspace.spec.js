import { test, expect } from "@playwright/test";
const password = "Test-password-123";
async function signup(page, prefix = "researcher") {
  await page.goto("/signup");
  await page.getByLabel("Your name").fill("Test Researcher");
  const email = `${prefix}@${Date.now()}.example.test`;
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page
    .getByRole("button", { name: "Create account", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "What will you discover today?" }),
  ).toBeVisible();
  return email;
}
test("research, history, exports, persistent chat, analytics, models and red-team", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await signup(page);
  await page.screenshot({
    path: "/tmp/research-v2-desktop.png",
    fullPage: true,
  });
  await page
    .getByLabel("Your research question")
    .fill("Energy storage evidence");
  await page
    .getByRole("button", { name: "Start research", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Source provenance" }),
  ).toBeVisible({ timeout: 20000 });
  const reportUrl = page.url();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "↓ Markdown" }).click();
  expect((await download).suggestedFilename()).toMatch(/\.md$/);
  const pdf = page.waitForEvent("download");
  await page.getByRole("button", { name: "↓ PDF" }).click();
  expect((await pdf).suggestedFilename()).toMatch(/\.pdf$/);
  await page.getByRole("link", { name: "Chat about this report" }).click();
  await page
    .getByRole("textbox", { name: "Message", exact: true })
    .fill("Explain the findings");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(
    page.getByText("Here is a follow-up explanation based on your research."),
  ).toBeVisible({ timeout: 15000 });
  await page.reload();
  await expect(
    page.getByText("Here is a follow-up explanation based on your research."),
  ).toBeVisible();
  await page.getByRole("link", { name: "Analytics", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Every token, accounted for" }),
  ).toBeVisible();
  await expect(
    page.getByText("confirmed", { exact: true }).first(),
  ).toBeVisible();
  await page.getByRole("link", { name: "Models", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Good models. One simple workspace." }),
  ).toBeVisible();
  await expect(
    page.getByText("qwen/qwen3.8-27b:free", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("nvidia/nemotron-3.5-lightning:free", { exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Red teaming", exact: true }).click();
  await page.getByRole("button", { name: "Run selected tests" }).click();
  await expect(page.getByText("refusal", { exact: true }).first()).toBeVisible({
    timeout: 15000,
  });
  await page.goto(reportUrl);
  await expect(
    page.getByRole("heading", { name: "Source provenance" }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
test("schedules preview, edit, pause, run now and delete", async ({ page }) => {
  await signup(page);
  await page.getByRole("link", { name: "Scheduler", exact: true }).click();
  await page.getByLabel("Schedule name").fill("Morning evidence");
  await page
    .getByLabel("Research topic", { exact: true })
    .fill("Battery developments");
  await page.getByRole("button", { name: "Preview", exact: true }).click();
  await expect(page.getByText("Next run:", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Save schedule" }).click();
  await expect(
    page.getByRole("heading", { name: "Morning evidence", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByLabel("Schedule name").fill("Updated evidence");
  await page.getByRole("button", { name: "Save schedule" }).click();
  await expect(
    page.getByRole("heading", { name: "Updated evidence", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Resume", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Run now", exact: true }).click();
  await page.getByRole("button", { name: "Run history", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Execution history" }),
  ).toBeVisible();
  await expect(page.getByText("completed", { exact: true })).toBeVisible({
    timeout: 20000,
  });
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Updated evidence", exact: true }),
  ).toHaveCount(0);
});
test("admin settings, audit, password change and login persistence", async ({
  page,
}) => {
  const email = await signup(page, "admin");
  await page.getByRole("link", { name: "Admin panel", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "User management" }),
  ).toBeVisible();
  page.once("dialog", (d) => d.accept("60000"));
  await page.getByRole("button", { name: "Change default allowance" }).click();
  await expect(
    page.getByText("Default: 60,000", { exact: false }),
  ).toBeVisible();
  await expect(
    page.locator(".audit summary").filter({ hasText: "settings" }),
  ).toBeVisible();
  await page.goto("/account");
  await page.getByLabel("New password").fill("Changed-password-456");
  await page.getByRole("button", { name: "Update password" }).click();
  await expect(
    page.locator(".toast").filter({ hasText: "Password updated" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Welcome back" }),
  ).toBeVisible();
  await page.getByLabel("Email address").fill(email);
  await page
    .getByLabel("Password", { exact: true })
    .fill("Changed-password-456");
  await page.getByRole("button", { name: "Sign in →", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "What will you discover today?" }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "What will you discover today?" }),
  ).toBeVisible();
});
test("mobile navigation and layout", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signup(page);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "/tmp/research-v2-mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Open menu" }).click();
  await page
    .getByRole("link", { name: "Research history", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Research history", exact: true }),
  ).toBeVisible();
});

test('protected routes, confirmation message, and password reset request', async ({page}) => {
  await page.goto('/history');
  await expect(page.getByRole('heading',{name:'Welcome back'})).toBeVisible();
  await page.goto('/signup');
  await page.route('**/auth/v1/signup?**', route => route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({user:{id:'00000000-0000-0000-0000-000000000099',email:'verify@example.test'},session:null})}));
  await page.getByLabel('Your name').fill('Verify User');
  await page.getByLabel('Email address').fill('verify@example.test');
  await page.getByLabel('Password',{exact:true}).fill(password);
  await page.getByRole('button',{name:'Create account',exact:true}).click();
  await expect(page.getByText('Check your email to confirm your account, then sign in.')).toBeVisible();
  await page.goto('/reset');
  await page.getByLabel('Email address').fill('verify@example.test');
  await page.getByRole('button',{name:'Send reset link'}).click();
  await expect(page.getByText('If the account exists, a password reset link has been sent.')).toBeVisible();
});

test('public home explains features and gates the workspace', async ({ page }) => {
  const requests = [];
  page.on('request', request => { if (request.url().includes('/api/v1/')) requests.push(request.url()); });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Big questions. Better understanding.' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Sign in', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Create your workspace', exact: true })).toBeVisible();
  await page.screenshot({path:'/tmp/research-home-desktop.png', fullPage:true});
  expect(requests).toEqual([]);
  await page.getByRole('link', { name: 'Explore this feature: Keep the conversation going' }).click();
  await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible();
  await page.getByRole('link', {name: 'Create an account', exact:true}).click();
  await page.getByLabel('Your name').fill('Home Visitor');
  await page.getByLabel('Email address').fill(`home@${Date.now()}.example.test`);
  await page.getByLabel('Password', {exact:true}).fill(password);
  await page.getByRole('button', {name:'Create account', exact:true}).click();
  await expect(page).toHaveURL(/\/chat$/);
  await page.goto('/');
  await page.getByRole('link', {name:'Open workspace', exact:true}).click();
  await expect(page).toHaveURL(/\/research$/);
  await expect(page.getByRole('heading', {name:'What will you discover today?'})).toBeVisible();
});

test('public home fits mobile and every service requires login', async ({ page }) => {
  await page.setViewportSize({width:390,height:844});
  await page.goto('/');
  await expect(page.getByRole('heading', {name:'Big questions. Better understanding.'})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({path:'/tmp/research-home-mobile.png',fullPage:true});
  await page.getByRole('link', {name:'Get started',exact:true}).click();
  await expect(page.getByRole('heading', {name:'Create your workspace'})).toBeVisible();
  for (const path of ['/research','/history','/chat','/schedules','/analytics','/models','/redteam','/admin','/notifications','/account']) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole('heading', {name:'Welcome back'})).toBeVisible();
  }
  await page.getByRole('link', {name:'Back to home'}).click();
  await expect(page.getByRole('heading', {name:'Big questions. Better understanding.'})).toBeVisible();
});
