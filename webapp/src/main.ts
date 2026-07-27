import "./style.css";

type Player = {
  id: number;
  name: string;
  initials: string;
  color: string;
  score: number;
};

type Stroke = {
  color: string;
  width: number;
  points: Array<{ x: number; y: number }>;
};

type Guess = {
  playerId: number;
  text: string;
  correct: boolean;
};

type RoundStatus = "idle" | "playing" | "won" | "timeout";

const ROUND_SECONDS = 75;
const words = [
  "пингвин",
  "самолёт",
  "будильник",
  "кактус",
  "велосипед",
  "подводная лодка",
  "гитара",
  "снеговик",
  "телескоп",
  "чемодан",
];

const players: Player[] = [
  { id: 1, name: "Маша", initials: "МШ", color: "#e4543d", score: 4 },
  { id: 2, name: "Илья", initials: "ИЛ", color: "#2e6f95", score: 2 },
  { id: 3, name: "Лена", initials: "ЛН", color: "#6d8547", score: 1 },
  { id: 4, name: "Антон", initials: "АН", color: "#a0608b", score: 0 },
];

let viewerId = 1;
let drawerIndex = 0;
let roundNumber = 1;
let roundStatus: RoundStatus = "idle";
let currentWord = "";
let secondsLeft = ROUND_SECONDS;
let timerId: number | undefined;
let strokes: Stroke[] = [];
let guesses: Guess[] = [];
let activeStroke: Stroke | null = null;
let brushColor = "#20201e";
let brushWidth = 5;

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) throw new Error("Не найден корневой элемент приложения");

app.innerHTML = `
  <div class="app-shell">
    <header class="topbar">
      <a class="brand" href="#" aria-label="Крокодил — на главную">
        <span class="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 40 40" role="img">
            <path d="M7 19c3-8 10-12 19-10l7 3-5 4 6 4-8 2c-2 6-7 9-13 7-5-2-8-5-8-9l2-1Z"/>
            <circle cx="24" cy="13" r="1.5"/>
            <path class="brand-teeth" d="m27 22 2 4 2-5"/>
          </svg>
        </span>
        <span>
          <strong>Крокодил</strong>
          <small>комната «Три топора»</small>
        </span>
      </a>

      <div class="round-meta" aria-label="Информация о раунде">
        <span>Раунд <strong id="round-number">1</strong></span>
        <span class="meta-divider"></span>
        <span><strong id="online-count">4</strong> в игре</span>
      </div>

      <label class="viewer-switch">
        <span>Смотреть как</span>
        <select id="viewer-select" aria-label="Выберите участника"></select>
      </label>
    </header>

    <main>
      <section class="game-stage">
        <div class="stage-heading">
          <div>
            <p class="eyebrow" id="role-label">Подготовка к раунду</p>
            <h1 id="main-title">Нарисуйте слово — остальные попробуют угадать</h1>
          </div>
          <div class="timer" id="timer" aria-label="Оставшееся время">
            <svg viewBox="0 0 44 44" aria-hidden="true">
              <circle class="timer-track" cx="22" cy="22" r="18"></circle>
              <circle class="timer-progress" id="timer-progress" cx="22" cy="22" r="18"></circle>
            </svg>
            <span id="timer-value">1:15</span>
          </div>
        </div>

        <div class="workspace">
          <section class="canvas-panel">
            <div class="word-strip" id="word-strip">
              <span id="word-label">Ваше слово</span>
              <strong id="secret-word">•••••••</strong>
              <button class="text-button" id="copy-word" type="button" hidden>скопировать</button>
            </div>

            <div class="canvas-wrap" id="canvas-wrap">
              <canvas id="drawing-canvas"></canvas>
              <div class="canvas-placeholder" id="canvas-placeholder">
                <div class="placeholder-doodle" aria-hidden="true">
                  <svg viewBox="0 0 160 120">
                    <path d="M26 82c20-37 35-48 48-34 12 13 17 17 29-1 10-15 23-12 33 17"/>
                    <path d="M42 82c6-8 14-9 22 0m30 0c7-12 16-12 24 0"/>
                    <circle cx="79" cy="34" r="7"/>
                  </svg>
                </div>
                <strong>Холст ждёт первого штриха</strong>
                <span>Ведущий рисует мышью или пальцем</span>
              </div>
              <div class="round-overlay" id="round-overlay">
                <span class="overlay-icon">✦</span>
                <strong id="overlay-title">Все готовы?</strong>
                <p id="overlay-copy">Маша получит случайное слово и начнёт рисовать.</p>
                <button class="primary-button" id="start-round" type="button">Начать раунд</button>
              </div>
            </div>

            <div class="drawing-tools" id="drawing-tools">
              <div class="color-tools" aria-label="Цвет кисти">
                <button class="color-swatch active" data-color="#20201e" style="--swatch:#20201e" aria-label="Чёрный"></button>
                <button class="color-swatch" data-color="#e4543d" style="--swatch:#e4543d" aria-label="Красный"></button>
                <button class="color-swatch" data-color="#2e6f95" style="--swatch:#2e6f95" aria-label="Синий"></button>
                <button class="color-swatch" data-color="#6d8547" style="--swatch:#6d8547" aria-label="Зелёный"></button>
              </div>
              <span class="tool-divider"></span>
              <div class="size-tools" aria-label="Размер кисти">
                <button class="size-button" data-width="3" aria-label="Тонкая кисть"><i style="--size:3px"></i></button>
                <button class="size-button active" data-width="5" aria-label="Средняя кисть"><i style="--size:5px"></i></button>
                <button class="size-button" data-width="10" aria-label="Толстая кисть"><i style="--size:10px"></i></button>
              </div>
              <span class="tools-spacer"></span>
              <button class="icon-button" id="undo-stroke" type="button" aria-label="Отменить последний штрих" title="Отменить">
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 7-4 4 4 4M5 11h8a6 6 0 0 1 6 6"/></svg>
              </button>
              <button class="icon-button" id="clear-canvas" type="button" aria-label="Очистить холст" title="Очистить">
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 7 16 0M9 7V4h6v3m-9 0 1 14h10l1-14M10 11v6m4-6v6"/></svg>
              </button>
            </div>
          </section>

          <aside class="side-panel">
            <section class="players-section">
              <div class="section-heading">
                <h2>Игроки</h2>
                <span class="live-dot">онлайн</span>
              </div>
              <ol class="players-list" id="players-list"></ol>
            </section>

            <section class="guesses-section">
              <div class="section-heading">
                <h2>Ответы</h2>
                <span id="guess-count">0</span>
              </div>
              <div class="guesses-feed" id="guesses-feed">
                <p class="empty-feed">Здесь появятся попытки игроков.<br>Правильный ответ увидят все.</p>
              </div>
              <form class="guess-form" id="guess-form">
                <label for="guess-input">Ваш вариант</label>
                <div class="input-row">
                  <input
                    id="guess-input"
                    type="text"
                    autocomplete="off"
                    maxlength="60"
                    placeholder="Что изображено?"
                  />
                  <button type="submit" aria-label="Отправить ответ">
                    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 14-8-5 16-3-6-6-2Zm6 2 3-3"/></svg>
                  </button>
                </div>
                <p class="input-hint" id="input-hint">Нужно точное совпадение со словом</p>
              </form>
            </section>
          </aside>
        </div>
      </section>
    </main>

    <footer class="prototype-note">
      <span>Прототип</span>
      Переключайте участника сверху, чтобы проверить роли ведущего и угадывающего.
    </footer>
  </div>
`;

const canvas = getElement<HTMLCanvasElement>("drawing-canvas");
const canvasWrap = getElement<HTMLDivElement>("canvas-wrap");
const canvasContext = canvas.getContext("2d");
if (!canvasContext) throw new Error("Canvas API недоступен");
const context: CanvasRenderingContext2D = canvasContext;

function getElement<T extends Element = HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Не найден элемент #${id}`);
  return element as unknown as T;
}

function drawer(): Player {
  return players[drawerIndex];
}

function viewer(): Player {
  return players.find((player) => player.id === viewerId) ?? players[0];
}

function isViewerDrawer(): boolean {
  return viewerId === drawer().id;
}

function populateViewerSelect(): void {
  const select = getElement<HTMLSelectElement>("viewer-select");
  select.innerHTML = players
    .map((player) => `<option value="${player.id}">${player.name}</option>`)
    .join("");
  select.value = String(viewerId);
  select.addEventListener("change", () => {
    viewerId = Number(select.value);
    render();
  });
}

function startRound(): void {
  roundStatus = "playing";
  currentWord = words[Math.floor(Math.random() * words.length)];
  secondsLeft = ROUND_SECONDS;
  guesses = [];
  strokes = [];
  redrawCanvas();
  window.clearInterval(timerId);
  timerId = window.setInterval(() => {
    secondsLeft -= 1;
    if (secondsLeft <= 0) {
      secondsLeft = 0;
      finishRound("timeout");
    }
    renderTimer();
  }, 1000);
  render();
}

function finishRound(status: "won" | "timeout"): void {
  roundStatus = status;
  window.clearInterval(timerId);
  timerId = undefined;
  render();
}

function nextRound(): void {
  drawerIndex = (drawerIndex + 1) % players.length;
  roundNumber += 1;
  roundStatus = "idle";
  currentWord = "";
  secondsLeft = ROUND_SECONDS;
  guesses = [];
  strokes = [];
  redrawCanvas();
  render();
}

function submitGuess(text: string): void {
  const cleaned = text.trim();
  if (!cleaned || roundStatus !== "playing" || isViewerDrawer()) return;
  const correct = cleaned.toLocaleLowerCase("ru") === currentWord.toLocaleLowerCase("ru");
  guesses.push({ playerId: viewerId, text: cleaned, correct });
  if (correct) {
    viewer().score += 2;
    drawer().score += 1;
    finishRound("won");
  } else {
    render();
  }
}

function render(): void {
  const activeDrawer = drawer();
  const drawerView = isViewerDrawer();
  const roleLabel = getElement("role-label");
  const mainTitle = getElement("main-title");
  const wordLabel = getElement("word-label");
  const secretWord = getElement("secret-word");
  const overlay = getElement("round-overlay");
  const overlayTitle = getElement("overlay-title");
  const overlayCopy = getElement("overlay-copy");
  const startButton = getElement<HTMLButtonElement>("start-round");
  const guessForm = getElement<HTMLFormElement>("guess-form");
  const guessInput = getElement<HTMLInputElement>("guess-input");
  const inputHint = getElement("input-hint");

  getElement("round-number").textContent = String(roundNumber);
  getElement("online-count").textContent = String(players.length);

  if (roundStatus === "idle") {
    roleLabel.textContent = drawerView ? "В этом раунде рисуете вы" : `Рисует ${activeDrawer.name}`;
    mainTitle.textContent = "Нарисуйте слово — остальные попробуют угадать";
    wordLabel.textContent = "Слово появится после старта";
    secretWord.textContent = "•••••••";
    overlay.classList.add("visible");
    overlayTitle.textContent = "Все готовы?";
    overlayCopy.textContent = `${activeDrawer.name} получит случайное слово и начнёт рисовать.`;
    startButton.textContent = drawerView ? "Получить слово" : "Начать демо";
    startButton.onclick = startRound;
  } else if (roundStatus === "playing") {
    roleLabel.textContent = drawerView ? "Вы рисуете" : `Рисует ${activeDrawer.name}`;
    mainTitle.textContent = drawerView
      ? "Рисуйте, но не пишите буквы и цифры"
      : "Угадайте слово по рисунку";
    wordLabel.textContent = drawerView ? "Ваше слово" : "Слово ведущего";
    secretWord.textContent = drawerView ? currentWord : maskWord(currentWord);
    overlay.classList.remove("visible");
  } else {
    const winnerGuess = guesses.find((guess) => guess.correct);
    const winner = players.find((player) => player.id === winnerGuess?.playerId);
    roleLabel.textContent = roundStatus === "won" ? "Слово угадано" : "Время вышло";
    mainTitle.textContent =
      roundStatus === "won"
        ? `${winner?.name ?? "Игрок"} получает 2 очка`
        : `Никто не угадал: «${currentWord}»`;
    wordLabel.textContent = "Загаданное слово";
    secretWord.textContent = currentWord;
    overlay.classList.add("visible");
    overlayTitle.textContent = roundStatus === "won" ? "Точное попадание!" : "Раунд завершён";
    overlayCopy.textContent =
      roundStatus === "won"
        ? `${winner?.name ?? "Игрок"} угадал слово, а ${activeDrawer.name} получает очко за рисунок.`
        : `Правильный ответ — «${currentWord}». Следующим рисует ${players[(drawerIndex + 1) % players.length].name}.`;
    startButton.textContent = "Следующий раунд";
    startButton.onclick = nextRound;
  }

  canvas.classList.toggle("drawing-disabled", !drawerView || roundStatus !== "playing");
  getElement("drawing-tools").classList.toggle("disabled", !drawerView || roundStatus !== "playing");
  guessForm.classList.toggle("disabled", drawerView || roundStatus !== "playing");
  guessInput.disabled = drawerView || roundStatus !== "playing";
  guessInput.placeholder = drawerView ? "Вы сейчас рисуете" : "Что изображено?";
  inputHint.textContent = drawerView
    ? "Ведущий не может отправлять ответы"
    : "Нужно точное совпадение со словом";

  renderPlayers();
  renderGuesses();
  renderTimer();
  updateCanvasPlaceholder();
}

function renderPlayers(): void {
  const list = getElement<HTMLOListElement>("players-list");
  const sorted = [...players].sort((a, b) => b.score - a.score);
  list.innerHTML = sorted
    .map((player) => {
      const isDrawer = player.id === drawer().id;
      const isViewer = player.id === viewerId;
      return `
        <li class="${isViewer ? "is-viewer" : ""}">
          <span class="avatar" style="--avatar:${player.color}">${player.initials}</span>
          <span class="player-name">
            <strong>${player.name}${isViewer ? " · вы" : ""}</strong>
            <small>${isDrawer ? "рисует сейчас" : "угадывает"}</small>
          </span>
          <span class="score">${player.score}</span>
        </li>
      `;
    })
    .join("");
}

function renderGuesses(): void {
  const feed = getElement("guesses-feed");
  getElement("guess-count").textContent = String(guesses.length);
  if (guesses.length === 0) {
    feed.innerHTML = '<p class="empty-feed">Здесь появятся попытки игроков.<br>Правильный ответ увидят все.</p>';
    return;
  }
  feed.innerHTML = guesses
    .slice()
    .reverse()
    .map((guess) => {
      const player = players.find((item) => item.id === guess.playerId);
      return `
        <div class="guess ${guess.correct ? "correct" : ""}">
          <span class="avatar mini" style="--avatar:${player?.color ?? "#777"}">${player?.initials ?? "?"}</span>
          <p><strong>${player?.name ?? "Игрок"}</strong><span>${escapeHtml(guess.text)}</span></p>
          ${guess.correct ? '<b aria-label="Верно">✓</b>' : ""}
        </div>
      `;
    })
    .join("");
}

function renderTimer(): void {
  const minutes = Math.floor(secondsLeft / 60);
  const seconds = secondsLeft % 60;
  getElement("timer-value").textContent = `${minutes}:${seconds.toString().padStart(2, "0")}`;
  const progress = getElement<SVGCircleElement>("timer-progress");
  const circumference = 2 * Math.PI * 18;
  progress.style.strokeDasharray = String(circumference);
  progress.style.strokeDashoffset = String(circumference * (1 - secondsLeft / ROUND_SECONDS));
  getElement("timer").classList.toggle("urgent", secondsLeft <= 15 && roundStatus === "playing");
}

function maskWord(word: string): string {
  return word
    .split("")
    .map((character) => (character === " " ? "  " : "•"))
    .join("");
}

function escapeHtml(text: string): string {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function resizeCanvas(): void {
  const rect = canvasWrap.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.floor(rect.width * ratio);
  canvas.height = Math.floor(rect.height * ratio);
  canvas.style.width = `${rect.width}px`;
  canvas.style.height = `${rect.height}px`;
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.lineCap = "round";
  context.lineJoin = "round";
  redrawCanvas();
}

function canvasPoint(event: PointerEvent): { x: number; y: number } {
  const rect = canvas.getBoundingClientRect();
  return {
    x: (event.clientX - rect.left) / rect.width,
    y: (event.clientY - rect.top) / rect.height,
  };
}

function beginStroke(event: PointerEvent): void {
  if (!isViewerDrawer() || roundStatus !== "playing") return;
  canvas.setPointerCapture(event.pointerId);
  activeStroke = { color: brushColor, width: brushWidth, points: [canvasPoint(event)] };
  strokes.push(activeStroke);
  updateCanvasPlaceholder();
}

function continueStroke(event: PointerEvent): void {
  if (!activeStroke) return;
  activeStroke.points.push(canvasPoint(event));
  redrawCanvas();
}

function endStroke(event: PointerEvent): void {
  if (!activeStroke) return;
  activeStroke.points.push(canvasPoint(event));
  activeStroke = null;
  redrawCanvas();
}

function redrawCanvas(): void {
  const rect = canvas.getBoundingClientRect();
  context.clearRect(0, 0, rect.width, rect.height);
  for (const stroke of strokes) {
    context.beginPath();
    context.strokeStyle = stroke.color;
    context.lineWidth = stroke.width;
    stroke.points.forEach((point, index) => {
      const x = point.x * rect.width;
      const y = point.y * rect.height;
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    if (stroke.points.length === 1) {
      const point = stroke.points[0];
      context.lineTo(point.x * rect.width + 0.1, point.y * rect.height + 0.1);
    }
    context.stroke();
  }
}

function updateCanvasPlaceholder(): void {
  getElement("canvas-placeholder").classList.toggle("hidden", strokes.length > 0 || roundStatus !== "playing");
}

populateViewerSelect();
window.addEventListener("resize", resizeCanvas);
canvas.addEventListener("pointerdown", beginStroke);
canvas.addEventListener("pointermove", continueStroke);
canvas.addEventListener("pointerup", endStroke);
canvas.addEventListener("pointercancel", endStroke);

getElement<HTMLFormElement>("guess-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = getElement<HTMLInputElement>("guess-input");
  submitGuess(input.value);
  input.value = "";
});

document.querySelectorAll<HTMLButtonElement>(".color-swatch").forEach((button) => {
  button.addEventListener("click", () => {
    brushColor = button.dataset.color ?? "#20201e";
    document.querySelectorAll(".color-swatch").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
  });
});

document.querySelectorAll<HTMLButtonElement>(".size-button").forEach((button) => {
  button.addEventListener("click", () => {
    brushWidth = Number(button.dataset.width ?? 5);
    document.querySelectorAll(".size-button").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
  });
});

getElement("undo-stroke").addEventListener("click", () => {
  strokes.pop();
  redrawCanvas();
  updateCanvasPlaceholder();
});

getElement("clear-canvas").addEventListener("click", () => {
  strokes = [];
  redrawCanvas();
  updateCanvasPlaceholder();
});

requestAnimationFrame(() => {
  resizeCanvas();
  render();
});
