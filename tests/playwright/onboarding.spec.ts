import fs from 'fs';
import path from 'path';
import { test, expect } from '@playwright/test';

type RestaurantProfile = {
  name: string;
  location: string;
  website?: string;
  phone?: string;
  description?: string;
  highlights: string[];
  serviceOptions: string[];
  offerings: string[];
  themes: string[];
  adjectives: string[];
};

type OnboardingFixtures = {
  profile: RestaurantProfile;
};

const onboardingDocPath = path.join(__dirname, '..', '..', 'app', 'onboarding.txt');

const fixtures: OnboardingFixtures = loadOnboardingFixtures(onboardingDocPath);

function loadOnboardingFixtures(filePath: string): OnboardingFixtures {
  const document = fs.readFileSync(filePath, 'utf-8');

  const outscraperStart = document.indexOf('{"query"');
  if (outscraperStart === -1) {
    throw new Error('Unable to locate outscraper payload in onboarding.txt');
  }
  const outscraperEnd = document.indexOf('\n', outscraperStart);
  const outscraperPayload =
    outscraperEnd === -1
      ? document.slice(outscraperStart)
      : document.slice(outscraperStart, outscraperEnd);

  const outscraperData = JSON.parse(outscraperPayload) as {
    name: string;
    full_address: string;
    site?: string;
    phone?: string;
    description?: string;
    about?: Record<string, Record<string, unknown>>;
  };

  const reviewInsightsMatch = document.match(/\{"themes":\[[^\]]+\],"adjectives":\[[^\]]+\]\}/);
  const reviewInsights = reviewInsightsMatch
    ? (JSON.parse(reviewInsightsMatch[0]) as { themes?: string[]; adjectives?: string[] })
    : { themes: [], adjectives: [] };

  const pickEnabled = (section?: Record<string, unknown>) =>
    Object.entries(section ?? {})
      .filter(([, value]) => Boolean(value))
      .map(([label]) => label);

  const about = outscraperData.about ?? {};

  return {
    profile: {
      name: outscraperData.name,
      location: outscraperData.full_address,
      website: outscraperData.site,
      phone: outscraperData.phone,
      description: outscraperData.description,
      highlights: pickEnabled(about['Highlights']),
      serviceOptions: pickEnabled(about['Service options']),
      offerings: pickEnabled(about['Offerings']),
      themes: reviewInsights.themes ?? [],
      adjectives: reviewInsights.adjectives ?? [],
    },
  };
}

test('onboarding fixtures parse the Train Wreck Bar & Grill profile from the document', () => {
  expect(fixtures.profile).toMatchObject({
    name: 'Train Wreck Bar & Grill',
    location: '427 E Fairhaven Ave, Burlington, WA 98233',
    website: 'http://www.trainwreckbar.com/',
  });
  expect(fixtures.profile.highlights).toEqual([
    'Fast service',
    'Great beer selection',
    'Great cocktails',
    'Sports',
  ]);
  expect(fixtures.profile.adjectives).toContain('classic');
  expect(fixtures.profile.themes).toContain('Atmosphere and ambiance');
});

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:8000';

test.describe('Customer onboarding flow', () => {
  test('signup form accepts restaurant details from onboarding fixtures', async ({ page }) => {
    await page.goto(`${baseURL}/signup/`);

    const timestamp = Date.now();
    await page.getByLabel('Email').fill(`playwright+${timestamp}@example.com`);
    await page.getByLabel('Password').fill('Testing123!');
    await page.getByLabel('Confirm Password').fill('Testing123!');
    await page.getByLabel('Restaurant Name').fill(fixtures.profile.name);
    await page.getByLabel('Location').fill(fixtures.profile.location);

    const websiteField = page.getByLabel(/website/i);
    if (await websiteField.count()) {
      await websiteField.fill(fixtures.profile.website ?? '');
      await expect(websiteField).toHaveValue(fixtures.profile.website ?? '');
    }

    await expect(page.getByLabel('Restaurant Name')).toHaveValue(fixtures.profile.name);
    await expect(page.getByLabel('Location')).toHaveValue(fixtures.profile.location);
  });

  test('setup confirmation nudges users toward the getting started hub', async ({ page }) => {
    const sessionId = 'cs_test_mock_session';
    await page.goto(`${baseURL}/setup/?session_id=${sessionId}`);

    await expect(page.getByRole('heading', { level: 1, name: /you're all set/i })).toBeVisible();
    await expect(page.getByText(/checkout reference/i)).toContainText(sessionId);
    await expect(page.getByRole('link', { name: /getting started checklist/i })).toHaveAttribute(
      'href',
      expect.stringContaining('/getting-started')
    );
  });
});
