import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, type TestInfo } from "@playwright/test";

export async function auditAccessibility(page: Page, testInfo: TestInfo, name: string) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  await testInfo.attach(`${name}-accessibility.json`, { body: JSON.stringify(results, null, 2), contentType: "application/json" });
  expect(results.violations.map(item => ({ id: item.id, nodes: item.nodes.map(node => ({ target: node.target, problem: node.failureSummary })) })), name).toEqual([]);
}
