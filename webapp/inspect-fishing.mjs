import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { createServer } from "vite";

const outputDir = new URL("./inspection/", import.meta.url);
const outputPath = fileURLToPath(outputDir);
await mkdir(outputDir, { recursive: true });

const server = await createServer({
  configFile: false,
  server: {
    host: "127.0.0.1",
    port: 4173,
    watch: { ignored: ["**/inspection/**", "**/inspection-profile*/**"] },
  },
});
await server.listen();

const browser = await chromium.launch({
  executablePath: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  headless: true,
});
const page = await browser.newPage({ viewport: { width: 1180, height: 850 } });
await page.goto("http://127.0.0.1:4173/fishing.html");
await page.clock.install();

const capture = async (name) => {
  await page.screenshot({
    path: join(outputPath, `${name}.png`),
  });
};

await capture("00-idle");
await page.locator('[data-rod-option="bamboo"]').click();
await capture("01-bamboo-idle");
await page.locator("#cast-button").click();

for (let index = 0; index < 4; index += 1) {
  await page.clock.runFor(index === 0 ? 80 : 220);
  await capture(`cast-${index + 1}`);
}

await page.clock.runFor(350);
await capture("10-waiting-a");
await page.clock.runFor(500);
await capture("11-waiting-b");

await page.reload();
await page.locator('[data-rod-option="bamboo"]').click();
await page.locator("#fisher").evaluate((element) => {
  element.style.animation = "none";
});
for (const [index, position] of ["0", "33.333%", "66.667%", "100%"].entries()) {
  await page.locator("#fisher").evaluate(
    (element, backgroundPosition) => {
      element.style.backgroundPositionX = backgroundPosition;
    },
    position,
  );
  await capture(`bamboo-idle-frame-${index + 1}`);
}

await page.reload();
await page.locator('[data-rod-option="bamboo"]').click();
await page.locator("#fisher").evaluate((element) => {
  element.classList.add("casting");
  element.style.animation = "none";
});
for (const [index, position] of ["0", "33.333%", "66.667%", "100%"].entries()) {
  await page.locator("#fisher").evaluate(
    (element, backgroundPosition) => {
      element.style.backgroundPositionX = backgroundPosition;
    },
    position,
  );
  await capture(`bamboo-cast-frame-${index + 1}`);
}

await page.reload();
await page.locator('[data-rod-option="professional"]').click();
await page.locator("#fisher").evaluate((element) => {
  element.style.animation = "none";
});
for (const [index, position] of ["0", "33.333%", "66.667%", "100%"].entries()) {
  await page.locator("#fisher").evaluate(
    (element, backgroundPosition) => {
      element.style.backgroundPositionX = backgroundPosition;
    },
    position,
  );
  await capture(`professional-idle-frame-${index + 1}`);
}

await page.reload();
await page.locator('[data-rod-option="professional"]').click();
await page.locator("#fisher").evaluate((element) => {
  element.classList.add("casting");
  element.style.animation = "none";
});
for (const [index, position] of ["0", "33.333%", "66.667%", "100%"].entries()) {
  await page.locator("#fisher").evaluate(
    (element, backgroundPosition) => {
      element.style.backgroundPositionX = backgroundPosition;
    },
    position,
  );
  await capture(`professional-cast-frame-${index + 1}`);
}

await page.reload();
await page.locator('[data-rod-option="professional"]').click();
await page.locator("#cast-button").click();
await page.clock.runFor(1000);
await capture("professional-waiting");

await page.reload();
await page.locator('[data-rod-option="bamboo"]').click();
await page.locator("#cast-button").click();
await page.clock.runFor(1000);
if (await page.locator("#lake-scene").getAttribute("data-state") !== "waiting") {
  throw new Error("Игра не перешла в ожидание поклёвки");
}
await page.locator("#cast-button").click();
if (await page.locator("#lake-scene").getAttribute("data-state") !== "idle") {
  throw new Error("Повторное нажатие не прервало ожидание");
}
await capture("12-waiting-cancelled");

for (const rod of ["classic", "bamboo", "professional"]) {
  await page.reload();
  await page.locator(`[data-rod-option="${rod}"]`).click();
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await page.locator("#cast-button").click();
    await page.clock.runFor(1000);
    const transition = await page.locator("#fisher").evaluate((element) => ({
      backgroundImage: getComputedStyle(element).backgroundImage,
      casting: element.classList.contains("casting"),
      state: element.closest("#lake-scene")?.getAttribute("data-state"),
    }));
    if (
      transition.state !== "waiting"
      || transition.casting
      || transition.backgroundImage === "none"
    ) {
      throw new Error(
        `Нестабильный переход после заброса (${rod}, попытка ${attempt + 1})`,
      );
    }
    for (let sample = 0; sample < 50; sample += 1) {
      await page.clock.runFor(10);
      const positions = await page.locator("#fisher").evaluate((element) =>
        getComputedStyle(element).backgroundPositionX
          .split(",")
          .map((value) => Number.parseFloat(value)),
      );
      const stablePositions = [0, 33.333, 66.667, 100];
      if (positions.some((position) =>
        stablePositions.every((stable) => Math.abs(position - stable) > .01)
      )) {
        throw new Error(
          `Промежуточная позиция спрайта (${rod}): ${positions.join(", ")}`,
        );
      }
    }
    await page.locator("#cast-button").click();
  }
}

await browser.close();
await server.close();
