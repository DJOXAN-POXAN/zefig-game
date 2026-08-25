MODULES = ["attack", "defense", "scout", "stealth"]
MAX_LEVEL = 5

ACTION_BASE_COST = {"attack": 30, "scout": 20, "repair": 40}


def rating(team) -> int:
    """Общий рейтинг = сумма уровней всех модулей."""
    return team.lvl_attack + team.lvl_defense + team.lvl_scout + team.lvl_stealth


def visible_fields(stealth_level: int) -> list:
    """Какие поля видны другим командам в зависимости от уровня скрытности."""
    base = ["name", "rating"]
    if stealth_level < 2:
        base.append("coins")
    if stealth_level < 3:
        base.append("attack")
    if stealth_level < 4:
        base.append("defense")
    if stealth_level < 5:
        base.append("scout")
    # Всегда показываем уровень скрытности, чтобы видеть иконку мозга
    base.append("stealth")
    return base


def upgrade_cost(current_level: int) -> int | None:
    """Стоимость улучшения с current_level на current_level+1. None если уже макс."""
    table = {1: 50, 2: 150, 3: 300, 4: 500}
    return table.get(current_level)  # если уровень 5 – вернёт None


def scout_free_uses(team) -> int:
    """Количество бесплатных разведок в раунде, доступных команде."""
    if team.lvl_scout >= 5:
        return 999  # безлимит
    if team.lvl_scout >= 4:
        return 2
    return 1


def resolve_attack(attacker, target, target_has_shield=False):
    """
    Возвращает (нанесённый_урон, имя_модуля_получившего_урон, сообщение).
    Если у цели активен щит, урон равен 0, и щит снимается.
    """
    import random

    # Проверяем щит
    if target_has_shield:
        return 0, None, "Атака заблокирована щитом!"

    dmg = max(0, attacker.lvl_attack - target.lvl_defense)
    if dmg <= 0:
        return 0, None, f"Атака не нанесла урона (защита {target.name} поглотила всё)."

    # Выбираем случайный модуль цели, который можно понизить (кроме тех, что уже 1)
    mods = []
    for m in ["attack", "defense", "energy", "tactics"]:   # новые модули
        if getattr(target, f"lvl_{m}") > 1:
            mods.append(m)
    if not mods:
        return dmg, None, f"Урон {dmg}, но все модули цели уже на минимуме."

    chosen = random.choice(mods)
    new_lvl = getattr(target, f"lvl_{chosen}") - 1
    setattr(target, f"lvl_{chosen}", new_lvl)
    names = {"attack": "Атака", "defense": "Защита", "energy": "Энергия", "tactics": "Тактика"}
    return dmg, chosen, f"«{attacker.name}» наносит {dmg} урона «{target.name}» и снижает модуль «{names[chosen]}» до {new_lvl}."
