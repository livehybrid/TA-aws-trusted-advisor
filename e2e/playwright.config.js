// Playwright config for the browser checks against the docker Splunk that the
// integration-test job starts (docker/docker-compose.yml).
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
    testDir: '.',
    testMatch: '*.spec.js',
    timeout: 300000,
    retries: process.env.CI ? 1 : 0,
    workers: 1, // one Splunk, and the specs share its conf files
    forbidOnly: !!process.env.CI,
    outputDir: 'e2e-results',
    use: {
        baseURL: process.env.SPLUNK_WEB_URL || 'http://localhost:8000',
        ignoreHTTPSErrors: true,
        viewport: { width: 1600, height: 1000 },
        actionTimeout: 30000,
        screenshot: 'only-on-failure',
        trace: 'retain-on-failure',
    },
    reporter: [['list']],
});
