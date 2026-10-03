import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  timeout: 60000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:5174",
    headless: true,
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev -- --port 5174",
    url: "http://127.0.0.1:5174",
    reuseExistingServer: !process.env.CI,
    env: {
      VITE_SUPABASE_URL: "http://127.0.0.1:8001",
      VITE_SUPABASE_PUBLISHABLE_KEY: "test-key",
      VITE_API_URL: "http://127.0.0.1:8001/api/v1",
    },
  },
});
