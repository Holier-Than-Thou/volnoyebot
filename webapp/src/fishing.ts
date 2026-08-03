import "./fishing.css";

type GameState = "idle" | "casting" | "waiting" | "playing" | "caught" | "lost";
type RodKind = "classic" | "bamboo" | "professional";

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) throw new Error("Не найден корневой элемент приложения");

app.innerHTML = `
  <main class="fishing-app">
    <header class="game-header">
      <div>
        <p class="eyebrow">Казино «Три топора»</p>
        <h1>Тихая заводь</h1>
      </div>
      <div class="weather">
        <span class="weather-icon">☀</span>
        <span><b>Утро</b><small>клёв хороший</small></span>
      </div>
    </header>

    <section class="lake-scene" id="lake-scene">
      <div class="sky">
        <span class="cloud cloud-one"></span>
        <span class="cloud cloud-two"></span>
        <div class="far-shore">
          <i></i><i></i><i></i><i></i><i></i><i></i><i></i>
        </div>
      </div>
      <div class="water">
        <span class="water-line line-one"></span>
        <span class="water-line line-two"></span>
        <span class="water-line line-three"></span>
        <div class="bobber" id="lake-bobber"><i></i></div>
        <div class="bite-rings" id="bite-rings"><i></i><i></i><i></i></div>
      </div>

      <div class="pier">
        <span></span><span></span><span></span><span></span><span></span>
      </div>
      <div class="fisher-seat" aria-hidden="true">
        <i></i><i></i>
      </div>
      <div class="fisher" id="fisher" aria-label="Рыбак сидит на табурете на пирсе">
        <span class="visually-hidden">Рыбак сидит с удочкой</span>
      </div>
      <svg class="fishing-line-overlay" id="fishing-line-overlay" aria-hidden="true">
        <path id="fishing-line-path"></path>
      </svg>
      <div class="pier-post-foreground pier-post-near" aria-hidden="true"></div>
      <div class="pier-post-foreground pier-post-far" aria-hidden="true"></div>

      <div class="status-card" id="status-card">
        <span class="status-icon" id="status-icon">≈</span>
        <div>
          <strong id="status-title">Вода спокойна</strong>
          <p id="status-text">Закиньте удочку и дождитесь поклёвки.</p>
        </div>
      </div>

      <aside class="minigame" id="minigame" aria-label="Шкала ловли">
        <div class="catch-column">
          <span class="scale-label">Рыба</span>
          <div class="catch-track" id="catch-track">
            <div class="safe-zone" id="safe-zone">
              <svg class="pixel-target-icon" viewBox="0 0 18 12" aria-hidden="true">
                <path class="pixel-fish-outline" d="M2 3h2V1h8v2h2V2h4v8h-4V9h-2v2H4V9H2V7H0V5h2z" />
                <path class="pixel-fish-body" d="M4 3h7v2h3V4h2v4h-2V7h-3v2H4V7H2V5h2z" />
                <path class="pixel-fish-eye" d="M5 4h2v2H5z" />
              </svg>
            </div>
            <div class="player-float" id="player-float">
              <i></i>
              <svg class="pixel-hook-icon" viewBox="0 0 12 16" aria-hidden="true">
                <path class="pixel-hook-outline" d="M4 0h4v3H7v7h2V8h3v4h-1v2H9v2H5v-1H3v-2H2V9h3v3h2V2H4z" />
                <path class="pixel-hook-body" d="M5 1h2v1H6v9h3V9h2v2h-1v2H8v1H5v-1H4v-2h1z" />
              </svg>
            </div>
          </div>
          <small>Удерживайте маркер в зелёной зоне</small>
        </div>
        <div class="progress-column">
          <span class="scale-label">Улов</span>
          <div class="progress-track">
            <div class="progress-fill" id="progress-fill"></div>
            <span class="fish-mark">🐟</span>
          </div>
        </div>
      </aside>
    </section>

    <section class="controls">
      <button id="cast-button" type="button">
        <span>Закинуть удочку</span>
        <small>и ждать поклёвки</small>
      </button>
      <div class="rod-picker" role="group" aria-label="Выбор удочки">
        <span>Удочка</span>
        <div>
          <button type="button" data-rod-option="classic">Обычная</button>
          <button type="button" data-rod-option="bamboo">Бамбук</button>
          <button type="button" data-rod-option="professional">Профи</button>
        </div>
      </div>
      <div class="control-hint">
        <span class="mouse-icon">↟</span>
        <p><b>Удерживайте ЛКМ, палец или пробел</b><br>Нажатие тянет маркер вверх, отпускание — вниз.</p>
      </div>
      <div class="record">
        <span>Лучший улов</span>
        <b id="record-value">—</b>
      </div>
    </section>

    <footer>
      <span>Прототип</span>
      Результаты не сохраняются и не влияют на баланс бота.
    </footer>
  </main>
`;

const get = <T extends Element>(id: string): T => {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Не найден элемент #${id}`);
  return element as unknown as T;
};

const scene = get<HTMLElement>("lake-scene");
const fisher = get<HTMLElement>("fisher");
const lakeBobber = get<HTMLElement>("lake-bobber");
const fishingLineOverlay = get<SVGElement>("fishing-line-overlay");
const fishingLinePath = get<SVGPathElement>("fishing-line-path");
const biteRings = get<HTMLElement>("bite-rings");
const minigame = get<HTMLElement>("minigame");
const safeZoneElement = get<HTMLElement>("safe-zone");
const playerFloatElement = get<HTMLElement>("player-float");
const progressFill = get<HTMLElement>("progress-fill");
const castButton = get<HTMLButtonElement>("cast-button");
const statusIcon = get<HTMLElement>("status-icon");
const statusTitle = get<HTMLElement>("status-title");
const statusText = get<HTMLElement>("status-text");
const recordValue = get<HTMLElement>("record-value");
const rodButtons = Array.from(
  document.querySelectorAll<HTMLButtonElement>("[data-rod-option]"),
);

const assetUrl = (path: string): string => (
  `${import.meta.env.BASE_URL}${path.replace(/^\//, "")}`
);

castButton.disabled = true;
fisher.style.animationPlayState = "paused";
rodButtons.forEach((button) => { button.disabled = true; });

let state: GameState = "idle";
let waitTimer: number | undefined;
let castFallbackTimer: number | undefined;
let animationFrame: number | undefined;
let lastFrame = 0;
let holding = false;
let floatPosition = 0.22;
let floatVelocity = 0;
let safePosition = 0.55;
let safeVelocity = 0.1;
let safeTarget = 0.55;
let targetChangeIn = 0;
let catchProgress = 0.42;
let roundStartedAt = 0;
let bestTime: number | null = null;
let selectedRod: RodKind = "classic";
let castPending = false;
let rodPending = false;

const spriteSources: Record<RodKind, string[]> = {
  classic: [
    assetUrl("assets/fishing/fisher-idle-rod.png"),
    assetUrl("assets/fishing/fisher-idle-body.png"),
    assetUrl("assets/fishing/fisher-cast-rod.png"),
    assetUrl("assets/fishing/fisher-cast-body.png"),
  ],
  bamboo: [
    assetUrl("assets/fishing/fisher-idle-bamboo.png"),
    assetUrl("assets/fishing/fisher-cast-bamboo.png"),
  ],
  professional: [
    assetUrl("assets/fishing/fisher-idle-professional.png"),
    assetUrl("assets/fishing/fisher-cast-professional.png"),
  ],
};

const spriteLoads = new Map<string, Promise<void>>();

function preloadSprite(source: string): Promise<void> {
  const pending = spriteLoads.get(source);
  if (pending) return pending;

  const loading = new Promise<void>((resolve) => {
    const image = new Image();
    image.onload = () => {
      if (typeof image.decode === "function") {
        void image.decode().catch(() => undefined).finally(resolve);
      } else {
        resolve();
      }
    };
    image.onerror = () => resolve();
    image.src = source;
  });
  spriteLoads.set(source, loading);
  return loading;
}

function preloadRod(rod: RodKind): Promise<void> {
  return Promise.all(spriteSources[rod].map(preloadSprite)).then(() => undefined);
}

function updateRodControls(): void {
  const lockedByState = ["casting", "waiting", "playing"].includes(state);
  rodButtons.forEach((button) => {
    button.disabled = lockedByState || castPending || rodPending;
  });
}

async function changeRod(rod: RodKind): Promise<void> {
  if (
    ["casting", "waiting", "playing"].includes(state)
    || rodPending
    || castPending
  ) return;
  rodPending = true;
  castButton.disabled = true;
  updateRodControls();
  fisher.style.animationPlayState = "paused";
  await preloadRod(rod);
  selectRod(rod);
  fisher.style.animationPlayState = "";
  rodPending = false;
  castButton.disabled = false;
  updateRodControls();
}

function selectRod(rod: RodKind): void {
  selectedRod = rod;
  fisher.dataset.rod = rod;
  rodButtons.forEach((button) => {
    const active = button.dataset.rodOption === rod;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  window.localStorage.setItem("fishing-test-rod", rod);
}

function setStatus(icon: string, title: string, text: string): void {
  statusIcon.textContent = icon;
  statusTitle.textContent = title;
  statusText.textContent = text;
}

const sceneArtwork = {
  width: 1536,
  height: 1024,
  fisherAnchorX: 261,
  fisherAnchorY: 856.5,
  fisherWidth: 414,
  compactBackgroundFocusX: 437,
  compactFisherAnchorX: 515,
  compactFisherAnchorY: 755,
  compactFisherWidth: 340,
  bobberX: 890,
  compactBobberX: 1040,
  bobberY: 572,
  ringsY: 615,
  foregroundPosts: {
    near: [
      [552, 668], [588, 668], [596, 676], [596, 724],
      [607, 731], [607, 749], [592, 755], [592, 815],
      [558, 815], [558, 755], [543, 749], [543, 731], [551, 724],
    ],
    far: [
      [663, 589], [699, 589], [706, 596], [706, 642],
      [718, 650], [718, 669], [701, 675], [701, 750],
      [668, 750], [668, 675], [653, 669], [653, 650], [661, 642],
    ],
  },
};

function updateSceneLayout(): void {
  const width = scene.clientWidth;
  const height = scene.clientHeight;
  if (!width || !height) return;

  const scale = Math.max(
    width / sceneArtwork.width,
    height / sceneArtwork.height,
  );
  const renderedHeight = sceneArtwork.height * scale;
  const offsetY = (height - renderedHeight) / 2;
  const visibleArtworkWidth = width / scale;
  const compact = width / height < 1;
  const availableHorizontalCrop = Math.max(
    0,
    sceneArtwork.width - visibleArtworkWidth,
  );
  const fisherAnchorX = compact
    ? sceneArtwork.compactFisherAnchorX
    : sceneArtwork.fisherAnchorX;
  const fisherAnchorY = compact
    ? sceneArtwork.compactFisherAnchorY
    : sceneArtwork.fisherAnchorY;
  const fisherWidth = compact
    ? sceneArtwork.compactFisherWidth
    : sceneArtwork.fisherWidth;
  const compactCropSourceX = sceneArtwork.compactBackgroundFocusX
    - visibleArtworkWidth * .02;
  const cropSourceX = compact
    ? Math.min(Math.max(0, compactCropSourceX), availableHorizontalCrop)
    : 0;
  const offsetX = -cropSourceX * scale;
  const bobberX = compact
    ? Math.min(
        sceneArtwork.compactBobberX,
        cropSourceX + visibleArtworkWidth * .88,
      )
    : sceneArtwork.bobberX;

  scene.style.setProperty("--background-left", `${offsetX}px`);
  scene.style.setProperty(
    "--fisher-left",
    `${offsetX + fisherAnchorX * scale}px`,
  );
  scene.style.setProperty(
    "--fisher-bottom",
    `${height - (offsetY + fisherAnchorY * scale)}px`,
  );
  scene.style.setProperty(
    "--fisher-width",
    `${fisherWidth * scale}px`,
  );
  scene.style.setProperty(
    "--bobber-left",
    `${offsetX + bobberX * scale}px`,
  );
  scene.style.setProperty(
    "--bobber-top",
    `${offsetY + sceneArtwork.bobberY * scale}px`,
  );
  scene.style.setProperty(
    "--rings-top",
    `${offsetY + sceneArtwork.ringsY * scale}px`,
  );

  Object.entries(sceneArtwork.foregroundPosts).forEach(([name, points]) => {
    const clipPath = points
      .map(([x, y]) => `${offsetX + x * scale}px ${offsetY + y * scale}px`)
      .join(", ");
    scene.style.setProperty(`--${name}-post-clip`, clipPath);
  });
}

function setState(nextState: GameState): void {
  state = nextState;
  scene.dataset.state = nextState;
  fisher.classList.toggle("casting", nextState === "casting");
  lakeBobber.classList.toggle("visible", ["waiting", "playing"].includes(nextState));
  biteRings.classList.toggle("visible", nextState === "playing");
  minigame.classList.toggle("visible", nextState === "playing");
  updateRodControls();
  window.requestAnimationFrame(updateFishingLine);

  if (nextState === "idle") {
    setStatus("≈", "Вода спокойна", "Закиньте удочку и дождитесь поклёвки.");
    castButton.disabled = false;
    castButton.innerHTML = "<span>Закинуть удочку</span><small>и ждать поклёвки</small>";
  } else if (nextState === "casting") {
    setStatus("↗", "Заброс!", "Поплавок летит к середине заводи.");
    castButton.disabled = true;
    castButton.innerHTML = "<span>Забрасываем…</span><small>держите удочку крепче</small>";
  } else if (nextState === "waiting") {
    setStatus("…", "Ждём поклёвку", "Следите за поплавком.");
    castButton.disabled = false;
    castButton.innerHTML = "<span>Смотать удочку</span><small>прервать ожидание поклёвки</small>";
  } else if (nextState === "playing") {
    setStatus("!", "Клюёт!", "Удерживайте маркер внутри зелёной зоны.");
    castButton.disabled = true;
    castButton.innerHTML = "<span>Рыба на крючке</span><small>не дайте ей сорваться</small>";
  } else if (nextState === "caught") {
    const seconds = (performance.now() - roundStartedAt) / 1000;
    if (bestTime === null || seconds < bestTime) {
      bestTime = seconds;
      recordValue.textContent = `${seconds.toFixed(1)} сек.`;
    }
    setStatus("🐟", "Карась пойман!", `Вы справились за ${seconds.toFixed(1)} сек.`);
    castButton.disabled = false;
    castButton.innerHTML = "<span>Закинуть ещё раз</span><small>попробовать улучшить результат</small>";
  } else {
    setStatus("↘", "Рыба сорвалась", "Попробуйте удерживать более ровный ритм.");
    castButton.disabled = false;
    castButton.innerHTML = "<span>Попробовать снова</span><small>в этот раз повезёт</small>";
  }
}

function updateFishingLine(): void {
  const sceneRect = scene.getBoundingClientRect();
  const fisherRect = fisher.getBoundingClientRect();
  const bobberRect = lakeBobber.getBoundingClientRect();
  const fightingClassic = state === "playing" && selectedRod === "classic";
  const startX = fisherRect.left - sceneRect.left
    + fisherRect.width * (fightingClassic ? 0.895 : 0.94);
  const startY = fisherRect.top - sceneRect.top
    + fisherRect.height * (fightingClassic ? 0.319 : 0.28);
  const endX = bobberRect.left - sceneRect.left + bobberRect.width / 2;
  const endY = bobberRect.top - sceneRect.top + 4;
  const controlX = (startX + endX) / 2;
  const midpointY = (startY + endY) / 2;
  const sagDepth = fightingClassic
    ? Math.min(24, Math.max(14, Math.abs(endX - startX) * 0.045))
    : Math.min(55, Math.max(30, Math.abs(endX - startX) * 0.1));
  const controlY = midpointY + sagDepth * 2;

  fishingLineOverlay.setAttribute(
    "viewBox",
    `0 0 ${sceneRect.width} ${sceneRect.height}`,
  );
  fishingLinePath.setAttribute(
    "d",
    `M ${startX} ${startY} Q ${controlX} ${controlY} ${endX} ${endY}`,
  );
}

function finishCast(): void {
  if (state !== "casting") return;
  window.clearTimeout(castFallbackTimer);
  castFallbackTimer = undefined;
  setState("waiting");
  const biteDelay = 1800 + Math.random() * 3200;
  waitTimer = window.setTimeout(startMinigame, biteDelay);
}

async function cast(): Promise<void> {
  if (state === "waiting") {
    window.clearTimeout(waitTimer);
    waitTimer = undefined;
    setState("idle");
    return;
  }
  if (["casting", "playing"].includes(state) || castPending || rodPending) return;
  castPending = true;
  castButton.disabled = true;
  updateRodControls();
  await preloadRod(selectedRod);
  castPending = false;
  window.clearTimeout(waitTimer);
  window.cancelAnimationFrame(animationFrame ?? 0);
  setState("casting");

  castFallbackTimer = window.setTimeout(finishCast, 980);
}

function startMinigame(): void {
  floatPosition = 0.22;
  floatVelocity = 0;
  safePosition = 0.56;
  safeVelocity = 0;
  safeTarget = 0.56;
  targetChangeIn = 0.4;
  catchProgress = 0.42;
  roundStartedAt = performance.now();
  lastFrame = roundStartedAt;
  setState("playing");
  updateVisuals();
  animationFrame = window.requestAnimationFrame(gameLoop);
}

function gameLoop(now: number): void {
  if (state !== "playing") return;
  const dt = Math.min((now - lastFrame) / 1000, 0.034);
  lastFrame = now;

  const acceleration = holding ? 2.15 : -1.52;
  floatVelocity += acceleration * dt;
  floatVelocity *= Math.pow(0.985, dt * 60);
  floatVelocity = Math.max(-1.05, Math.min(1.08, floatVelocity));
  floatPosition += floatVelocity * dt;

  if (floatPosition < 0) {
    floatPosition = 0;
    floatVelocity = Math.abs(floatVelocity) * 0.28;
  } else if (floatPosition > 1) {
    floatPosition = 1;
    floatVelocity = -Math.abs(floatVelocity) * 0.28;
  }

  targetChangeIn -= dt;
  if (targetChangeIn <= 0) {
    safeTarget = 0.12 + Math.random() * 0.76;
    targetChangeIn = 0.7 + Math.random() * 1.25;
  }
  const targetForce = (safeTarget - safePosition) * 3.1;
  safeVelocity = (safeVelocity + targetForce * dt) * Math.pow(0.92, dt * 60);
  safePosition = Math.max(0.1, Math.min(0.9, safePosition + safeVelocity * dt));

  const distance = Math.abs(floatPosition - safePosition);
  const isInside = distance <= 0.145;
  catchProgress += (isInside ? 0.105 : -0.075) * dt;
  catchProgress = Math.max(0, Math.min(1, catchProgress));
  scene.classList.toggle("in-zone", isInside);
  updateVisuals();

  if (catchProgress >= 1) {
    setState("caught");
    return;
  }
  if (catchProgress <= 0) {
    setState("lost");
    return;
  }
  animationFrame = window.requestAnimationFrame(gameLoop);
}

function updateVisuals(): void {
  playerFloatElement.style.bottom = `${floatPosition * 88 + 2}%`;
  safeZoneElement.style.bottom = `${safePosition * 76 + 2}%`;
  progressFill.style.height = `${catchProgress * 100}%`;
}

function setHolding(value: boolean): void {
  holding = value;
  scene.classList.toggle("holding", value);
}

castButton.addEventListener("click", () => void cast());
fisher.addEventListener("animationend", (event) => {
  if (event.animationName === "fisher-cast") finishCast();
});
rodButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const rod = button.dataset.rodOption;
    if (
      rod === "classic"
      || rod === "bamboo"
      || rod === "professional"
    ) void changeRod(rod);
  });
});
scene.addEventListener("pointerdown", (event) => {
  if (state !== "playing") return;
  event.preventDefault();
  scene.setPointerCapture(event.pointerId);
  setHolding(true);
});
scene.addEventListener("pointerup", () => setHolding(false));
scene.addEventListener("pointercancel", () => setHolding(false));
scene.addEventListener("contextmenu", (event) => event.preventDefault());

window.addEventListener("keydown", (event) => {
  if (event.code !== "Space" || event.repeat || state !== "playing") return;
  event.preventDefault();
  setHolding(true);
});
window.addEventListener("keyup", (event) => {
  if (event.code !== "Space") return;
  event.preventDefault();
  setHolding(false);
});
window.addEventListener("blur", () => setHolding(false));
const sceneResizeObserver = new ResizeObserver(() => {
  updateSceneLayout();
  updateFishingLine();
});
sceneResizeObserver.observe(scene);
window.addEventListener("resize", () => {
  updateSceneLayout();
  updateFishingLine();
});

const savedRod = window.localStorage.getItem("fishing-test-rod");
if (
  savedRod === "classic"
  || savedRod === "bamboo"
  || savedRod === "professional"
) {
  selectedRod = savedRod;
}
selectRod(selectedRod);
await preloadRod(selectedRod);
fisher.style.animationPlayState = "";
updateSceneLayout();
setState("idle");
void Promise.all(
  (["classic", "bamboo", "professional"] as RodKind[]).map(preloadRod),
);
