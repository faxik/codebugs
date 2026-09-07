"""CB-326: проверка ПРЕДПОСЫЛКИ — что промежуточный слой видит на отказе проверки аргументов.

ЗАЧЕМ ЭТОТ ФАЙЛ. Бриф юнита Т-146 (§2, вторая поправка) требует подтвердить
замером, а не принять на веру, одно утверждение, от которого зависит выбор
способа починки. Утверждение такое: обработчик вызова инструмента внутри чужой
библиотеки перехватывает исключение ДО того, как его увидит промежуточный слой
проекта, и потому слой получает РЕЗУЛЬТАТ вызова с признаком ошибки, а не
исключение. Если это верно, «расширить существующий слой» означает переписать
результат; если неверно — означает что-то другое, и строить надо иначе.

Проверяется это тремя вопросами, на каждый из четырёх случаев брифа:

1. Что делает `call_next` — возвращает значение или поднимает исключение?
2. Если возвращает, то ЧТО ИМЕННО: какого типа объект, несёт ли он признак
   ошибки, какой в нём текст.
3. Можно ли этот объект ПОДМЕНИТЬ так, чтобы подмену увидел клиент. Первые два
   вопроса без третьего ничего не решают: слой может видеть результат и всё
   равно не иметь возможности его переписать.

Четвёртый вопрос задаётся отдельным разделом и решает не «можно ли», а «куда
ставить». Сервер ведёт собственный учёт вызовов инструментов (таблица
`tool_calls`, её показывает команда `codebugs usage`), и ведёт его тоже
промежуточным слоем. Значит новый слой, поставленный СНАРУЖИ учётного, отнимет
у учёта те вызовы, которые тот считает сегодня. Это важно не абстрактно: улика
в самой карте CB-326 («инструмент `add` отказывает в 11 процентах вызовов, 65
из 591») взята ИМЕННО ИЗ ЭТОЙ ТАБЛИЦЫ, поэтому починка, которая незаметно
перестанет считать отказы, обесценит собственное основание карты. Раздел
измеряет, что таблица записывает СЕГОДНЯ на каждом из четырёх случаев.

Наблюдатель ставится в САМОЕ ВНЕШНЕЕ положение (в начало списка), потому что
список промежуточных слоёв идёт снаружи внутрь, и только снаружи видно оба
канала сразу — и отказ проверки схемы, и отказ слоя строгих имён аргументов,
который поднимает исключение раньше, чем дело дойдёт до чужой библиотеки.

КАК ЗАПУСКАТЬ (только из своего рабочего дерева, под обеими версиями):

    uv run --extra dev python tests/manual/probe_cb326_middleware_premise.py
    uv run --with 'mcp==2.1.1' --extra dev python tests/manual/probe_cb326_middleware_premise.py
"""

from __future__ import annotations

import contextlib
import importlib.metadata as md
import pathlib
import sys
import tempfile
from typing import Any

from mcp_types import CallToolResult, TextContent

from codebugs import db, server

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from test_cb310_refusal_text import call_over_the_wire  # noqa: E402

#: Метка, которой наблюдатель подменяет текст. Выбрана заведомо непохожей на всё,
#: что может написать сам проект или чужая библиотека, чтобы её появление у
#: клиента нельзя было объяснить ничем, кроме удавшейся подмены.
REWRITE_MARKER = "ПОДМЕНЕНО-НАБЛЮДАТЕЛЕМ-CB326"


def _tracker(root: str):
    db.init_project(root)

    @contextlib.contextmanager
    def _conn():
        conn = db.connect(root)
        try:
            yield conn
        finally:
            conn.close()

    return _conn


def _describe(value: Any) -> str:
    """Одна строка про возвращённый объект: тип, признак ошибки, начало текста."""
    kind = type(value).__module__ + "." + type(value).__name__
    if isinstance(value, CallToolResult):
        text = value.content[0].text if value.content else ""
        return f"type={kind} is_error={value.is_error} text[:60]={text[:60]!r}"
    if isinstance(value, dict):
        return f"type={kind} keys={sorted(value)} isError={value.get('isError')!r}"
    return f"type={kind} repr[:80]={value!r:.80}"


def _rewritten(value: Any) -> Any:
    """Попытка подменить текст в возвращённом объекте, не меняя его формы.

    Две формы обрабатываются потому, что обе встречаются у этой библиотеки: сам
    проект уже читает результат как `CallToolResult(is_error=True) | {"isError":
    True}` в слое учёта вызовов. Подмена делается КОПИЕЙ (`model_copy`), а не
    правкой на месте: правка на месте прошла бы и на объекте, который библиотека
    успела куда-то сохранить, и тогда замер сказал бы «можно» про способ, который
    в настоящем слое окажется небезопасным.
    """
    if isinstance(value, CallToolResult):
        return value.model_copy(update={"content": [TextContent(type="text", text=REWRITE_MARKER)]})
    if isinstance(value, dict) and "content" in value:
        out = dict(value)
        out["content"] = [{"type": "text", "text": REWRITE_MARKER}]
        return out
    return value


def _innermost(exc: BaseException) -> BaseException:
    """Самое внутреннее исключение: группы исключений разворачиваются.

    Живой сеанс идёт под группой задач, поэтому отказ протокола выходит наружу
    завёрнутым. Без разворачивания замер сообщал бы класс обёртки вместо класса
    отказа — то есть отвечал бы не на тот вопрос, который задан.
    """
    inner = getattr(exc, "exceptions", None)
    return _innermost(inner[0]) if inner else exc


def _observe(built, name: str, arguments: dict, *, rewrite: bool) -> tuple[str, str]:
    """Прогон одного случая через живого клиента с наблюдателем в самом внешнем слое.

    Возвращает пару «что увидел наблюдатель» и «что в итоге получил клиент».

    Обвязка живого сеанса ЗАИМСТВУЕТСЯ из `tests/test_cb310_refusal_text.py`, а
    не пишется здесь заново — тот же довод, что и у соседнего снимка договора в
    этой же ветке. Она дважды обращается к внутренности чужой библиотеки, две
    допущенные версии которой в этом проекте уже ведут себя по-разному; вторая
    копия жила бы своей жизнью и при поломке молчала бы, потому что этот скрипт
    запускают руками и ровно в ту минуту, когда им хотят что-то ДОКАЗАТЬ.
    Наблюдатель к ней не относится: он ставится в список слоёв ДО вызова, и
    заимствованная функция о нём ничего знать не обязана.
    """
    seen: list[str] = []

    async def observer(ctx: Any, call_next: Any) -> Any:
        if ctx.method != "tools/call":
            return await call_next(ctx)
        try:
            result = await call_next(ctx)
        except BaseException as exc:  # noqa: BLE001 — предмет замера и есть форма отказа
            seen.append(f"RAISED {type(exc).__module__}.{type(exc).__name__}: {exc}"[:200])
            raise
        seen.append("RETURNED " + _describe(result))
        return _rewritten(result) if rewrite else result

    built.middleware.insert(0, observer)
    try:
        try:
            is_error, text = call_over_the_wire(built, name, arguments)
            client = f"is_error={is_error} text[:70]={text[:70]!r}"
        except BaseException as exc:  # noqa: BLE001 — форма ответа и есть предмет замера
            inner = _innermost(exc)
            client = f"RAISED {type(inner).__name__}: {inner}"[:200]
    finally:
        built.middleware.remove(observer)
    return (seen[0] if seen else "<наблюдатель не сработал>"), client


CASES = [
    ("не подано обязательное поле", "add", {"category": "x", "file": "y", "description": "z"}),
    (
        "неверный тип поля",
        "add",
        {"severity": 5, "category": "x", "file": "y", "description": "z"},
    ),
    (
        "незаявленное имя аргумента",
        "add",
        {"severity": "low", "category": "x", "file": "y", "description": "z", "bogus": 1},
    ),
    ("доменный отказ (для контраста)", "update", {"finding_id": "CB-1", "status": "bogus"}),
]


def _usage_after_one_call(tool: str, arguments: dict) -> str:
    """Что учётная таблица записала после ОДНОГО вызова — на свежем трекере.

    Свежий трекер под каждый случай нужен потому, что таблица накапливает: на
    общем трекере пришлось бы вычитать предыдущие строки, а вычитание — это уже
    рассуждение, тогда как здесь нужен прямой замер.
    """
    with tempfile.TemporaryDirectory() as root:
        factory = _tracker(root)
        built = server._build_server("findings", factory)
        _observe(built, tool, arguments, rewrite=False)
        with factory() as conn:
            rows = server.usage.usage_summary(conn)["rows"]
    if not rows:
        return "таблица пуста — вызов не посчитан"
    return "; ".join(
        f"{r['tool_name']}: вызовов={r['calls']} из них отказов={r['failures']}" for r in rows
    )


def main() -> None:
    print(f"mcp     {md.version('mcp')}")
    print(f"pydantic {md.version('pydantic')}")
    print()
    with tempfile.TemporaryDirectory() as root:
        factory = _tracker(root)
        for rewrite in (False, True):
            print("=" * 78)
            print("ПОДМЕНА ВКЛЮЧЕНА" if rewrite else "ТОЛЬКО НАБЛЮДЕНИЕ")
            print("=" * 78)
            for label, tool, args in CASES:
                built = server._build_server("findings", factory)
                seen, client = _observe(built, tool, args, rewrite=rewrite)
                print(f"--- {label} ({tool}) ---")
                print(f"  слой видит : {seen}")
                print(f"  клиент     : {client}")
                print()
    print("=" * 78)
    print("ЧТО УЧЁТНАЯ ТАБЛИЦА ПИШЕТ СЕГОДНЯ (по одному вызову на свежем трекере)")
    print("=" * 78)
    for label, tool, args in CASES:
        print(f"--- {label} ({tool}) ---")
        print(f"  учёт       : {_usage_after_one_call(tool, args)}")
        print()


if __name__ == "__main__":
    main()
