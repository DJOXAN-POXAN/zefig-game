"""
Бэкенд игры «Зефирные космолёты».
FastAPI + WebSockets + SQLite (SQLAlchemy).
Запуск: uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""
import random
import string

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import Base, engine, get_db
import models as m
import game_logic as gl
from ws_manager import manager

app = FastAPI(title="Зефирные космолёты")

@app.on_event("startup")
def startup():
    try:
        import models
        models.Base.metadata.create_all(bind=engine)
        print("✅ Таблицы созданы (или уже существуют)")
    except Exception as e:
        print(f"⚠️ Ошибка создания таблиц: {e}")
        
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PHASE_ORDER = ["lobby", "challenge", "upgrade", "battle", "finished"]


# ---------------------------------------------------------------- utils ----

def gen_room_code() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=5))


def get_game(db: Session, room_code: str) -> m.Game:
    game = db.query(m.Game).filter(m.Game.room_code == room_code.upper()).first()
    if not game:
        raise HTTPException(404, "Игра с таким кодом не найдена")
    return game


def require_admin(db: Session, room_code: str, token: str) -> m.Game:
    game = get_game(db, room_code)
    if token != game.admin_token:
        raise HTTPException(403, "Неверный токен ведущего")
    return game


def require_team(db: Session, room_code: str, token: str) -> tuple[m.Game, m.Team]:
    game = get_game(db, room_code)
    team = db.query(m.Team).filter(
        m.Team.game_id == game.id, m.Team.token == token
    ).first()
    if not team:
        raise HTTPException(403, "Неверный токен команды")
    return game, team


def log(db: Session, game: m.Game, text: str):
    db.add(m.HistoryEntry(game_id=game.id, round_number=game.round_number, text=text))


def scouted_ids(team: m.Team) -> set:
    return set(x for x in (team.scouted_targets or "").split(",") if x)


# ------------------------------------------------------------ serializers --

def team_full_dict(t: m.Team) -> dict:
    return {
        "id": t.id, "name": t.name, "coins": t.coins,
        "attack": t.lvl_attack, "defense": t.lvl_defense,
        "scout": t.lvl_scout, "stealth": t.lvl_stealth,
        "rating": gl.rating(t), "connected": t.connected,
    }


def team_filtered_dict(t: m.Team, revealed: bool) -> dict:
    d = {"id": t.id, "name": t.name, "rating": gl.rating(t), "revealed": revealed}
    fields = gl.visible_fields(0 if revealed else t.lvl_stealth)
    if "coins" in fields:
        d["coins"] = t.coins
    if "attack" in fields:
        d["attack"] = t.lvl_attack
    if "defense" in fields:
        d["defense"] = t.lvl_defense
    if "scout" in fields:
        d["scout"] = t.lvl_scout
    if "stealth" in fields:
        d["stealth"] = t.lvl_stealth
    return d


def build_view(db: Session, game: m.Game, role: str, team_id: str | None) -> dict:
    base = {
        "type": "state",
        "room_code": game.room_code,
        "phase": game.phase,
        "round_number": game.round_number,
        "total_rounds": game.total_rounds,
        "history": [h.text for h in sorted(game.history, key=lambda x: x.created_at)][-50:],
    }
    if role == "admin":
        base["teams"] = [team_full_dict(t) for t in game.teams]
        base["actions"] = [
            {
                "id": a.id, "team_id": a.team_id,
                "team_name": next((t.name for t in game.teams if t.id == a.team_id), "?"),
                "action_type": a.action_type, "target_team_id": a.target_team_id,
                "target_name": next((t.name for t in game.teams if t.id == a.target_team_id), None),
                "module": a.module, "cost": a.cost, "status": a.status,
                "result_text": a.result_text,
            }
            for a in sorted(game.actions, key=lambda x: x.created_at)
            if a.round_number == game.round_number
        ]
        base["admin_token"] = game.admin_token
        return base

    # role == "team"
    me = next((t for t in game.teams if t.id == team_id), None)
    base["me"] = team_full_dict(me) if me else None
    revealed = scouted_ids(me) if me else set()
    base["board"] = [
        team_full_dict(t) if t.id == team_id else team_filtered_dict(t, t.id in revealed)
        for t in game.teams
    ]
    if me:
        my_actions = [a for a in game.actions if a.team_id == me.id and a.round_number == game.round_number]
        base["my_actions"] = [
            {
                "id": a.id, "action_type": a.action_type,
                "target_team_id": a.target_team_id, "module": a.module,
                "cost": a.cost, "status": a.status, "result_text": a.result_text,
            }
            for a in my_actions
        ]
        base["upgrade_costs"] = {
            mod: gl.upgrade_cost(getattr(me, f"lvl_{mod}")) for mod in gl.MODULES
        }
    return base


async def push(db: Session, game: m.Game):
    db.commit()
    db.refresh(game)

    def builder(role, team_id):
        return build_view(db, game, role, team_id)

    await manager.broadcast_state(game.room_code, builder)


# ------------------------------------------------------------- schemas ----

class CreateGameIn(BaseModel):
    total_rounds: int = 5
    max_teams: int = 6


class JoinIn(BaseModel):
    name: str


class ScoreIn(BaseModel):
    token: str
    scores: dict[str, int]  # team_id -> points


class PhaseIn(BaseModel):
    token: str


class ConfirmActionIn(BaseModel):
    token: str
    action_id: str
    approve: bool


class UpgradeIn(BaseModel):
    token: str
    module: str


class TeamActionIn(BaseModel):
    token: str
    action_type: str
    target_team_id: str | None = None
    module: str | None = None


# ------------------------------------------------------------- endpoints --

@app.post("/api/games")
def create_game(body: CreateGameIn, db: Session = Depends(get_db)):
    code = gen_room_code()
    while db.query(m.Game).filter(m.Game.room_code == code).first():
        code = gen_room_code()
    game = m.Game(
        room_code=code, total_rounds=body.total_rounds, max_teams=body.max_teams
    )
    db.add(game)
    db.commit()
    db.refresh(game)
    return {"room_code": game.room_code, "admin_token": game.admin_token}


@app.post("/api/games/{room_code}/join")
async def join_game(room_code: str, body: JoinIn, db: Session = Depends(get_db)):
    game = get_game(db, room_code)
    if game.phase != "lobby":
        raise HTTPException(400, "Игра уже началась, присоединиться нельзя")
    if len(game.teams) >= game.max_teams:
        raise HTTPException(400, "Все места заняты")
    name = body.name.strip()[:30]
    if not name:
        raise HTTPException(400, "Введите название команды")
    if any(t.name.lower() == name.lower() for t in game.teams):
        raise HTTPException(400, "Команда с таким названием уже есть")
    team = m.Team(game_id=game.id, name=name)
    db.add(team)
    log(db, game, f"Команда «{name}» присоединилась к игре.")
    await push(db, game)
    return {"team_id": team.id, "token": team.token, "room_code": game.room_code}


@app.get("/api/games/{room_code}/state")
def get_state(room_code: str, token: str, role: str, db: Session = Depends(get_db)):
    game = get_game(db, room_code)
    if role == "admin":
        require_admin(db, room_code, token)
        return build_view(db, game, "admin", None)
    _, team = require_team(db, room_code, token)
    return build_view(db, game, "team", team.id)


@app.post("/api/games/{room_code}/admin/start")
async def admin_start(room_code: str, body: PhaseIn, db: Session = Depends(get_db)):
    game = require_admin(db, room_code, body.token)
    if game.phase != "lobby":
        raise HTTPException(400, "Игра уже запущена")
    if not game.teams:
        raise HTTPException(400, "Нет ни одной команды")
    game.phase = "challenge"
    game.round_number = 1
    log(db, game, f"Игра началась. Раунд {game.round_number}: «Космический вызов».")
    await push(db, game)
    return {"ok": True}


@app.post("/api/games/{room_code}/admin/score")
async def admin_score(room_code: str, body: ScoreIn, db: Session = Depends(get_db)):
    game = require_admin(db, room_code, body.token)
    if game.phase != "challenge":
        raise HTTPException(400, "Начисление очков доступно только в фазе «Космический вызов»")
    for team_id, points in body.scores.items():
        team = next((t for t in game.teams if t.id == team_id), None)
        if not team:
            continue
        points = max(0, min(20, int(points)))
        team.coins += points
        log(db, game, f"«{team.name}» получает {points} монет за раунд {game.round_number}.")
    await push(db, game)
    return {"ok": True}


@app.post("/api/games/{room_code}/admin/next_phase")
async def admin_next_phase(room_code: str, body: PhaseIn, db: Session = Depends(get_db)):
    game = require_admin(db, room_code, body.token)
    if game.phase == "challenge":
        game.phase = "upgrade"
        log(db, game, "Фаза «Техобслуживание»: команды прокачивают корабли.")
    elif game.phase == "upgrade":
        game.phase = "battle"
        log(db, game, "Фаза «Звёздная битва»: команды выбирают действия.")
    elif game.phase == "battle":
        if game.round_number >= game.total_rounds:
            game.phase = "finished"
            log(db, game, "Игра завершена!")
        else:
            game.round_number += 1
            game.phase = "challenge"
            for t in game.teams:
                t.scouted_targets = ""
            log(db, game, f"Раунд {game.round_number}: «Космический вызов».")
    else:
        raise HTTPException(400, "Нет следующей фазы")
    await push(db, game)
    return {"ok": True}


@app.post("/api/games/{room_code}/admin/reset")
async def admin_reset(room_code: str, body: PhaseIn, db: Session = Depends(get_db)):
    game = require_admin(db, room_code, body.token)
    for t in game.teams:
        t.coins = 0
        t.lvl_attack = t.lvl_defense = t.lvl_scout = t.lvl_stealth = 1
        t.scouted_targets = ""
    db.query(m.Action).filter(m.Action.game_id == game.id).delete()
    db.query(m.HistoryEntry).filter(m.HistoryEntry.game_id == game.id).delete()
    game.phase = "lobby"
    game.round_number = 0
    log(db, game, "Игра сброшена ведущим.")
    await push(db, game)
    return {"ok": True}


@app.post("/api/games/{room_code}/admin/confirm_action")
async def admin_confirm_action(room_code: str, body: ConfirmActionIn, db: Session = Depends(get_db)):
    game = require_admin(db, room_code, body.token)
    action = db.query(m.Action).filter(
        m.Action.id == body.action_id, m.Action.game_id == game.id
    ).first()
    if not action or action.status != "pending":
        raise HTTPException(400, "Действие не найдено или уже обработано")
    team = next((t for t in game.teams if t.id == action.team_id), None)

    if not body.approve:
        action.status = "rejected"
        action.result_text = "Отклонено ведущим."
        log(db, game, f"Действие «{team.name}» ({action.action_type}) отклонено ведущим.")
        await push(db, game)
        return {"ok": True}

    if team.coins < action.cost:
        action.status = "rejected"
        action.result_text = "Недостаточно монет на момент подтверждения."
        log(db, game, f"Действие «{team.name}» отклонено: не хватило монет.")
        await push(db, game)
        return {"ok": True}

    team.coins -= action.cost

    if action.action_type == "attack":
        target = next((t for t in game.teams if t.id == action.target_team_id), None)
        damage, hit_module, text = gl.resolve_attack(team, target)
        action.result_text = text
        log(db, game, text)
    elif action.action_type == "scout":
        target_id = action.target_team_id
        current = scouted_ids(team)
        current.add(target_id)
        team.scouted_targets = ",".join(current)
        target = next((t for t in game.teams if t.id == target_id), None)
        action.result_text = f"«{team.name}» провёл(а) разведку «{target.name}»."
        log(db, game, action.result_text)
    elif action.action_type == "repair":
        mod = action.module
        current = getattr(team, f"lvl_{mod}")
        new_level = min(gl.MAX_LEVEL, current + 1)
        setattr(team, f"lvl_{mod}", new_level)
        names = {"attack": "Атака", "defense": "Защита", "scout": "Разведка", "stealth": "Скрытность"}
        action.result_text = f"«{team.name}» восстановил(а) модуль «{names[mod]}» до уровня {new_level}."
        log(db, game, action.result_text)

    action.status = "applied"
    await push(db, game)
    return {"ok": True}


@app.post("/api/games/{room_code}/team/upgrade")
async def team_upgrade(room_code: str, body: UpgradeIn, db: Session = Depends(get_db)):
    game, team = require_team(db, room_code, body.token)
    if game.phase != "upgrade":
        raise HTTPException(400, "Прокачка доступна только в фазе «Техобслуживание»")
    if body.module not in gl.MODULES:
        raise HTTPException(400, "Неизвестный модуль")
    current = getattr(team, f"lvl_{body.module}")
    cost = gl.upgrade_cost(current)
    if cost is None:
        raise HTTPException(400, "Модуль уже максимального уровня")
    if team.coins < cost:
        raise HTTPException(400, "Недостаточно монет")
    team.coins -= cost
    setattr(team, f"lvl_{body.module}", current + 1)
    names = {"attack": "Атака", "defense": "Защита", "scout": "Разведка", "stealth": "Скрытность"}
    log(db, game, f"«{team.name}» улучшает модуль «{names[body.module]}» до уровня {current + 1}.")
    await push(db, game)
    return {"ok": True}


@app.post("/api/games/{room_code}/team/action")
async def team_action(room_code: str, body: TeamActionIn, db: Session = Depends(get_db)):
    game, team = require_team(db, room_code, body.token)
    if game.phase != "battle":
        raise HTTPException(400, "Действия доступны только в фазе «Звёздная битва»")

    this_round_actions = [
        a for a in game.actions
        if a.team_id == team.id and a.round_number == game.round_number
        and a.status != "rejected"
    ]

    if body.action_type == "attack":
        if any(a.action_type == "attack" for a in this_round_actions):
            raise HTTPException(400, "Атака уже использована в этом раунде")
        target = next((t for t in game.teams if t.id == body.target_team_id), None)
        if not target or target.id == team.id:
            raise HTTPException(400, "Выберите корректную цель")
        cost = gl.ACTION_BASE_COST["attack"]
        action = m.Action(
            game_id=game.id, round_number=game.round_number, team_id=team.id,
            action_type="attack", target_team_id=target.id, cost=cost,
        )
    elif body.action_type == "scout":
        prior_scouts = [a for a in this_round_actions if a.action_type == "scout"]
        limit = gl.scout_free_uses(team) if team.lvl_scout >= 4 else 1
        if team.lvl_scout >= 5:
            limit = 999
        if len(prior_scouts) >= limit:
            raise HTTPException(400, "Лимит разведок в этом раунде исчерпан")
        target = next((t for t in game.teams if t.id == body.target_team_id), None)
        if not target or target.id == team.id:
            raise HTTPException(400, "Выберите корректную цель")
        use_index = len(prior_scouts) + 1
        if team.lvl_scout >= 5:
            cost = 0
        elif use_index == 1:
            cost = 0
        else:
            cost = 15 if team.lvl_scout >= 2 else gl.ACTION_BASE_COST["scout"]
        action = m.Action(
            game_id=game.id, round_number=game.round_number, team_id=team.id,
            action_type="scout", target_team_id=target.id, cost=cost,
        )
    elif body.action_type == "repair":
        if any(a.action_type == "repair" for a in this_round_actions):
            raise HTTPException(400, "Восстановление уже использовано в этом раунде")
        if body.module not in gl.MODULES:
            raise HTTPException(400, "Выберите модуль для восстановления")
        cost = gl.ACTION_BASE_COST["repair"]
        action = m.Action(
            game_id=game.id, round_number=game.round_number, team_id=team.id,
            action_type="repair", module=body.module, cost=cost,
        )
    else:
        raise HTTPException(400, "Неизвестное действие")

    if team.coins < action.cost:
        raise HTTPException(400, "Недостаточно монет для этого действия")

    db.add(action)
    log(db, game, f"«{team.name}» предлагает действие: {body.action_type} (ждёт подтверждения ведущего).")
    await push(db, game)
    return {"ok": True}


# ---------------------------------------------------------------- websocket

@app.websocket("/ws/{room_code}")
async def ws_endpoint(websocket: WebSocket, room_code: str, token: str, role: str):
    db = next(get_db())
    try:
        game = get_game(db, room_code)
    except HTTPException:
        await websocket.close(code=4404)
        return

    team_id = None
    if role == "admin":
        if token != game.admin_token:
            await websocket.close(code=4403)
            return
    else:
        team = db.query(m.Team).filter(m.Team.game_id == game.id, m.Team.token == token).first()
        if not team:
            await websocket.close(code=4403)
            return
        team_id = team.id
        team.connected = True
        db.commit()

    await manager.connect(room_code, websocket, role, team_id)
    await websocket.send_json(build_view(db, game, role, team_id))
    try:
        while True:
            await websocket.receive_text()  # клиент может присылать ping
    except WebSocketDisconnect:
        manager.disconnect(room_code, websocket)
        if role == "team" and team_id:
            team = db.query(m.Team).filter(m.Team.id == team_id).first()
            if team:
                team.connected = False
                db.commit()


# -------------------------------------------------------- статика фронтенда

app.mount("/static", StaticFiles(directory="../frontend"), name="static")


@app.get("/")
def root():
    return FileResponse("../frontend/index.html")


@app.get("/admin")
def admin_page():
    return FileResponse("../frontend/admin.html")


@app.get("/team")
def team_page():
    return FileResponse("../frontend/team.html")
