// Общие утилиты для страниц ведущего и команды.

const API_BASE = "";

const MODULE_META = {
  attack:  { label: "Атака",      cls: "attack" },
  defense: { label: "Защита",     cls: "defense" },
  scout:   { label: "Разведка",   cls: "scout" },
  stealth: { label: "Скрытность", cls: "stealth" },
};

const PHASE_LABEL = {
  lobby: "Ожидание команд",
  challenge: "Космический вызов",
  upgrade: "Техобслуживание",
  battle: "Звёздная битва",
  finished: "Игра завершена",
};

const ACTION_LABEL = {
  attack: "Атака", scout: "Разведка", repair: "Восстановление",
};

async function apiPost(path, body) {
  const res = await fetch(API_BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || "Ошибка запроса");
  }
  return data;
}

async function apiGet(path) {
  const res = await fetch(API_BASE + path);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Ошибка запроса");
  return data;
}

function wsUrl(roomCode, token, role) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws/${roomCode}?token=${encodeURIComponent(token)}&role=${role}`;
}

function connectWs(roomCode, token, role, onState, onClose) {
  let ws;
  let closedByUs = false;
  function open() {
    ws = new WebSocket(wsUrl(roomCode, token, role));
    ws.onmessage = (ev) => {
      try { onState(JSON.parse(ev.data)); } catch (e) { /* ignore */ }
    };
    ws.onclose = () => {
      if (!closedByUs) {
        if (onClose) onClose();
        setTimeout(open, 1500); // авто-переподключение
      }
    };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
    // ping для поддержания соединения
    const pingInt = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send("ping");
      else clearInterval(pingInt);
    }, 25000);
  }
  open();
  return { close: () => { closedByUs = true; if (ws) ws.close(); } };
}

function toast(msg, isError) {
  const el = document.createElement("div");
  el.className = "toast";
  el.style.borderColor = isError ? "var(--danger)" : "var(--card-border)";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

function moduleBar(label, cls, level) {
  const pct = (level / 5) * 100;
  return `
    <div class="module-row">
      <div class="module-label">${label}</div>
      <div class="bar-bg"><div class="bar-fill ${cls}" style="width:${pct}%"></div></div>
      <div class="module-lvl">${level}</div>
    </div>`;
}

function moduleBarHidden(label, cls) {
  return `
    <div class="module-row">
      <div class="module-label">${label}</div>
      <div class="bar-bg"><div class="bar-fill ${cls}" style="width:0%; opacity:.25"></div></div>
      <div class="module-lvl hidden-field">?</div>
    </div>`;
}

function upgradeCostText(level) {
  const table = { 1: 50, 2: 150, 3: 300, 4: 500 };
  if (level >= 5) return "макс.";
  return table[level] + " монет";
}

const RULES_HTML = `
  <h2>Правила игры «Зефирные космолёты» 🚀🍬</h2>
  <p>До 6 команд соревнуются за 4–5 раундов. Каждый раунд состоит из трёх фаз:</p>
  <ol>
    <li><b>Космический вызов</b> — ведущий даёт задание вживую, команды получают монеты по результату.</li>
    <li><b>Техобслуживание</b> — команды тратят монеты на прокачку модулей корабля.</li>
    <li><b>Звёздная битва</b> — команды выбирают одно действие: атака, разведка или восстановление.</li>
  </ol>
  <h3>Модули корабля (макс. уровень 5)</h3>
  <table>
    <tr><th>Модуль</th><th>Что даёт</th></tr>
    <tr><td>Атака</td><td>Урон = уровень атаки − уровень защиты цели</td></tr>
    <tr><td>Защита</td><td>Снижает получаемый урон</td></tr>
    <tr><td>Разведка</td><td>1 бесплатная разведка/раунд; 15 монет со 2 ур.; 2 раза/раунд с 4 ур.; безлимит с 5 ур.</td></tr>
    <tr><td>Скрытность</td><td>Скрывает от других данные о корабле: монеты (2 ур.), атаку (3 ур.), защиту (4 ур.), все модули (5 ур.)</td></tr>
  </table>
  <h3>Стоимость улучшения</h3>
  <table>
    <tr><th>Уровень</th><th>Стоимость</th></tr>
    <tr><td>1 → 2</td><td>50 монет</td></tr>
    <tr><td>2 → 3</td><td>150 монет</td></tr>
    <tr><td>3 → 4</td><td>300 монет</td></tr>
    <tr><td>4 → 5</td><td>500 монет</td></tr>
  </table>
  <h3>Действия в «Звёздной битве»</h3>
  <table>
    <tr><th>Действие</th><th>Стоимость</th><th>Эффект</th></tr>
    <tr><td>Атака</td><td>30 монет</td><td>Снижает случайный модуль цели на 1 уровень</td></tr>
    <tr><td>Разведка</td><td>от 0 монет</td><td>Полностью раскрывает корабль цели до конца раунда</td></tr>
    <tr><td>Восстановление</td><td>40 монет</td><td>Повышает выбранный свой модуль на 1 уровень</td></tr>
  </table>
  <p class="muted">Все действия команд подтверждает ведущий.</p>
`;

function openRules() {
  const overlay = document.createElement("div");
  overlay.className = "rules-overlay";
  overlay.innerHTML = `
    <div class="card rules-panel" style="position:relative;">
      <button class="ghost close-x" id="rulesClose">✕</button>
      ${RULES_HTML}
    </div>`;
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  overlay.querySelector("#rulesClose").addEventListener("click", () => overlay.remove());
}
