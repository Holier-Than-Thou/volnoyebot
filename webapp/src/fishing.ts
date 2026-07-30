import "./fishing.css";

type GameState = "idle" | "casting" | "waiting" | "playing" | "caught" | "lost";
type RodKind = "classic" | "bamboo";

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
        <span class="fisher-rod" aria-hidden="true"></span>
        <span class="visually-hidden">Рыбак сидит с удочкой</span>
      </div>
      <svg class="fishing-line-overlay" id="fishing-line-overlay" aria-hidden="true">
        <path id="fishing-line-path"></path>
      </svg>

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
            <div class="safe-zone" id="safe-zone"></div>
            <div class="player-float" id="player-float"><i></i></div>
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

let state: GameState = "idle";
let waitTimer: number | undefined;
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

function setState(nextState: GameState): void {
  state = nextState;
  scene.dataset.state = nextState;
  fisher.classList.toggle("casting", nextState === "casting");
  lakeBobber.classList.toggle("visible", ["waiting", "playing"].includes(nextState));
  biteRings.classList.toggle("visible", nextState === "playing");
  minigame.classList.toggle("visible", nextState === "playing");
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
    castButton.disabled = true;
    castButton.innerHTML = "<span>Удочка в воде</span><small>рыба уже где-то рядом</small>";
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
  const startX = fisherRect.left - sceneRect.left + fisherRect.width * 0.94;
  const startY = fisherRect.top - sceneRect.top + fisherRect.height * 0.28;
  const endX = bobberRect.left - sceneRect.left + bobberRect.width / 2;
  const endY = bobberRect.top - sceneRect.top + 4;
  const controlX = (startX + endX) / 2;
  const midpointY = (startY + endY) / 2;
  const sagDepth = Math.min(
    55,
    Math.max(30, Math.abs(endX - startX) * 0.1),
  );
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

function cast(): void {
  if (["casting", "waiting", "playing"].includes(state)) return;
  window.clearTimeout(waitTimer);
  window.cancelAnimationFrame(animationFrame ?? 0);
  setState("casting");

  waitTimer = window.setTimeout(() => {
    setState("waiting");
    const biteDelay = 1800 + Math.random() * 3200;
    waitTimer = window.setTimeout(startMinigame, biteDelay);
  }, 950);
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

castButton.addEventListener("click", cast);
rodButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const rod = button.dataset.rodOption;
    if (rod === "classic" || rod === "bamboo") selectRod(rod);
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
window.addEventListener("resize", updateFishingLine);

const savedRod = window.localStorage.getItem("fishing-test-rod");
if (savedRod === "classic" || savedRod === "bamboo") {
  selectedRod = savedRod;
}
selectRod(selectedRod);
setState("idle");
