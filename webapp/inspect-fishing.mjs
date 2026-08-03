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
      rodsLocked: Array.from(
        document.querySelectorAll("[data-rod-option]"),
      ).every((button) => button.disabled),
    }));
    if (
      transition.state !== "waiting"
      || transition.casting
      || transition.backgroundImage === "none"
      || !transition.rodsLocked
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
    if (await page.locator("[data-rod-option]:disabled").count() !== 0) {
      throw new Error(`Выбор удочки не разблокирован после отмены (${rod})`);
    }
  }
}

await page.reload();
await page.evaluate(() => { Math.random = () => 0; });
await page.locator("#cast-button").click();
await page.clock.runFor(1000 + 1800);
if (
  await page.locator("#lake-scene").getAttribute("data-state") !== "playing"
  || await page.locator("[data-rod-option]:disabled").count() !== 3
) {
  throw new Error("Выбор удочки не заблокирован во время ловли");
}

await page.setViewportSize({ width: 390, height: 760 });
await page.reload();
await page.locator('[data-rod-option="classic"]').click();
await page.screenshot({
  path: join(outputPath, "mobile-idle-classic.png"),
  fullPage: true,
});

for (const rod of ["classic", "bamboo", "professional"]) {
  await page.reload();
  await page.locator(`[data-rod-option="${rod}"]`).click();
  await page.evaluate(() => { Math.random = () => 0; });
  await page.locator("#cast-button").click();
  await page.clock.runFor(1000 + 2100);
  const fight = await page.locator("#fisher").evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      animationName: style.animationName,
      backgroundImage: style.backgroundImage,
      state: document.querySelector("#lake-scene")?.getAttribute("data-state"),
    };
  });
  if (
    fight.state !== "playing"
    || fight.animationName !== "fisher-fight"
    || !fight.backgroundImage.includes(`fisher-fight-${rod}.png`)
  ) {
    throw new Error(`Двухкадровая анимация не запустилась (${rod})`);
  }
  await page.locator("#fisher").evaluate((element) => {
    element.style.animation = "none";
    element.style.backgroundPositionX = "0";
  });
  await page.screenshot({
    path: join(outputPath, `mobile-${rod}-fight-medium.png`),
    fullPage: true,
  });
  await page.locator("#fisher").evaluate((element) => {
    element.style.backgroundPositionX = "100%";
  });
  await page.screenshot({
    path: join(outputPath, `mobile-${rod}-fight-strong.png`),
    fullPage: true,
  });
}

await page.reload();
await page.locator('[data-rod-option="professional"]').click();
await page.screenshot({
  path: join(outputPath, "mobile-idle.png"),
  fullPage: true,
});

const mobileLayout = await page.evaluate(() => {
  const scene = document.querySelector("#lake-scene").getBoundingClientRect();
  const fisherElement = document.querySelector("#fisher");
  const seatElement = document.querySelector(".fisher-seat");
  const postElements = [...document.querySelectorAll(".pier-post-foreground")];
  const fisher = fisherElement.getBoundingClientRect();
  const fisherStyle = getComputedStyle(fisherElement);
  const seatStyle = getComputedStyle(seatElement);
  const postStyles = postElements.map((element) => getComputedStyle(element));
  return {
    fisherInside:
      fisher.left >= scene.left - fisher.width * .25
      && fisher.right <= scene.right,
    sharedAnchor:
      fisherStyle.left === seatStyle.left
      && fisherStyle.bottom === seatStyle.bottom
      && fisherStyle.width === seatStyle.width,
    postAboveFisher:
      postStyles.length === 2
      && postStyles.every((style) =>
        style.display !== "none"
        && style.clipPath !== "none"
        && Number(style.zIndex) > Number(fisherStyle.zIndex)
      ),
  };
});
if (
  !mobileLayout.fisherInside
  || !mobileLayout.sharedAnchor
  || !mobileLayout.postAboveFisher
) {
  throw new Error("Рыбак или табурет смещены в мобильной компоновке");
}

await page.evaluate(() => { Math.random = () => 0; });
await page.locator("#cast-button").click();
await page.clock.runFor(1000);
await page.screenshot({
  path: join(outputPath, "mobile-waiting.png"),
  fullPage: true,
});

await page.clock.runFor(2100);
if (await page.locator("#lake-scene").getAttribute("data-state") !== "playing") {
  throw new Error("Мобильная мини-игра не запустилась");
}
await page.screenshot({
  path: join(outputPath, "mobile-playing.png"),
  fullPage: true,
});
const compactMinigame = await page.evaluate(() => {
  const scene = document.querySelector("#lake-scene").getBoundingClientRect();
  const panel = document.querySelector("#minigame").getBoundingClientRect();
  const bobber = document.querySelector("#lake-bobber").getBoundingClientRect();
  const fishIcon = getComputedStyle(document.querySelector(".pixel-target-icon"));
  const hookIcon = getComputedStyle(document.querySelector(".pixel-hook-icon"));
  const overlapsBobber = !(
    panel.right <= bobber.left
    || panel.left >= bobber.right
    || panel.bottom <= bobber.top
    || panel.top >= bobber.bottom
  );
  return {
    inTopRight: panel.top - scene.top <= 16 && scene.right - panel.right <= 16,
    compact: panel.width <= scene.width * .3,
    iconsVisible: fishIcon.display !== "none" && hookIcon.display !== "none",
    overlapsBobber,
  };
});
if (
  !compactMinigame.inTopRight
  || !compactMinigame.compact
  || !compactMinigame.iconsVisible
  || compactMinigame.overlapsBobber
) {
  throw new Error("Мобильная шкала закрывает сцену или потеряла пиксельные маркеры");
}

await page.setViewportSize({ width: 480, height: 760 });
await page.screenshot({
  path: join(outputPath, "mobile-resized.png"),
  fullPage: true,
});

await page.setViewportSize({ width: 320, height: 650 });
await page.reload();
await page.screenshot({
  path: join(outputPath, "mobile-320.png"),
  fullPage: true,
});
const narrowLayout = await page.evaluate(() => {
  const scene = document.querySelector("#lake-scene").getBoundingClientRect();
  const fisher = document.querySelector("#fisher").getBoundingClientRect();
  return {
    noHorizontalOverflow: document.documentElement.scrollWidth <= innerWidth,
    fisherInside:
      fisher.left >= scene.left - fisher.width * .25
      && fisher.right <= scene.right,
  };
});
if (!narrowLayout.noHorizontalOverflow || !narrowLayout.fisherInside) {
  throw new Error("Компоновка не помещается на ширине 320px");
}

await page.evaluate(() => { Math.random = () => 0; });
await page.locator("#cast-button").click();
await page.clock.runFor(1000 + 2100);
await page.screenshot({
  path: join(outputPath, "mobile-playing-320.png"),
  fullPage: true,
});

await browser.close();
await server.close();
