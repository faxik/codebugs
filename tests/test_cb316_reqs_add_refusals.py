"""CB-316: у поверхности создания требования отказ обязан быть отказом СО СЛОВАМИ.

ЧТО БЫЛО СЛОМАНО, В ДВУХ ПОЛОВИНАХ. Функция ``reqs.add_requirement`` — общая
точка записи для обеих поверхностей — не защищала вставку ничем, кроме
ограничения ``PRIMARY KEY`` в схеме, а нарушение этого ограничения библиотека
доступа к SQLite поднимает как ``sqlite3.IntegrityError``. Этот класс — чужой:
его объявляет стандартная библиотека, единый источник классификации
``refusals.CLASSIFICATION`` его не называет, и по правилу таблицы («класс, не
названный в таблице, — авария, что бы он ни наследовал») он классифицируется
как АВАРИЯ. Половина (б) — обработчик командной строки ``_cmd_reqs_add`` не был
проведён через общий помощник ``cli.domain_errors``, в отличие от соседей по
файлу, поэтому даже настоящий отказ по вводу (недопустимый приоритет) печатал
аварийную распечатку.

ПОЧЕМУ ЭТО БЫЛО ХУЖЕ, ЧЕМ «НЕКРАСИВЫЙ ТЕКСТ», И ЭТО ЗАМЕРЕНО, А НЕ ВЫВЕДЕНО.
Адаптер ``server._RefusalsReachTheClient`` переводит в читаемое клиентом
исключение ``ToolError`` только классы, названные в таблице отказами. Под
библиотекой ``mcp`` версии 2.0.0 SDK приписывает текст ЛЮБОГО исключения к
своему сообщению, поэтому там потеря незаметна; под 2.1.1, которая стоит у
владельца, клиент получал ``Error executing tool reqs_add`` БЕЗ ТЕКСТА ВОВСЕ.
Оба снимка лежат в ``.claude/plans/cb316/``.

ЧЕМ ЭТИ ПРОВЕРКИ ОТЛИЧАЮТСЯ ОТ УЖЕ СУЩЕСТВУЮЩИХ. ``test_refusal_classification``
проверяет ТАБЛИЦУ и обе границы класс за классом — то есть что названный
отказом класс доходит до человека. Здесь проверяется другое: что конкретная
функция записи вообще ПОДНИМАЕТ класс, который таблица называет отказом, а не
чужой. Ни один обход таблицы этого увидеть не может, потому что до таблицы
чужой класс просто не доходит.
"""

from __future__ import annotations

import contextlib
import sqlite3
import subprocess
import sys

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from codebugs import db, refusals, reqs, server


@pytest.fixture
def conn():
    """База в памяти со схемой требований — доменный слой без поверхностей."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    reqs.ensure_schema(c)
    yield c
    c.close()


@pytest.fixture
def tmp_project(tmp_path):
    """Настоящий трекер на диске — для проб через процесс и через протокол."""
    project = tmp_path / "project"
    project.mkdir()
    db.init_project(str(project))
    return str(project)


class TestADuplicateIdIsARefusalAndNotACrash:
    """Половина (а): конверсия в доменном слое, как ратифицировано в §2.1 брифа.

    Отказ доводится до вызывающего конверсией здесь, а НЕ добавлением строки
    ``sqlite3.IntegrityError`` в ``refusals.CLASSIFICATION``: таблица
    классифицирует по точному имени класса, но обе границы применяют её как
    ``except <кортеж>``, а ``except`` в Python сопоставляет по наследованию, —
    так что назвав чужой класс отказом, мы объявили бы отказом всякое нарушение
    всякого ограничения во всём пакете.
    """

    def test_the_second_write_of_one_id_raises_a_refusal(self, conn):
        reqs.add_requirement(conn, req_id="FR-1", description="первое")
        with pytest.raises(Exception) as caught:  # noqa: PT011 - класс и есть предмет
            reqs.add_requirement(conn, req_id="FR-1", description="второе")
        assert not isinstance(caught.value, sqlite3.Error), (
            "нарушение ограничения таблицы ушло вызывающему как есть — "
            f"поднялся {type(caught.value).__name__}, чужой пакету класс"
        )

    def test_the_class_raised_is_one_the_single_source_calls_a_refusal(self, conn):
        """Именно ЭТО делает текст видимым клиенту, а не сам факт исключения.

        Проверяется принадлежность кортежу, выведенному из
        ``refusals.CLASSIFICATION``, а не равенство ``ValueError``: если завтра
        таблица назовёт отказом более узкий класс, проверка обязана остаться
        верной, а не окаменеть на сегодняшнем написании.
        """
        reqs.add_requirement(conn, req_id="FR-1", description="первое")
        with pytest.raises(refusals.INPUT_REFUSALS) as caught:
            reqs.add_requirement(conn, req_id="FR-1", description="второе")
        assert refusals.kind_of(type(caught.value)) == refusals.INPUT, (
            "класс подняли, но единый источник называет его аварией — "
            "значит адаптер придержит текст, и клиент снова получит пустую ошибку"
        )

    def test_the_text_names_the_identifier_and_says_it_already_exists(self, conn):
        reqs.add_requirement(conn, req_id="FR-1", description="первое")
        with pytest.raises(refusals.INPUT_REFUSALS) as caught:
            reqs.add_requirement(conn, req_id="FR-1", description="второе")
        text = str(caught.value)
        assert "FR-1" in text, text
        assert "already exists" in text, text

    def test_the_refused_write_lands_nothing(self, conn):
        """Отказ обязан быть отказом и на диске, а не только на словах."""
        reqs.add_requirement(conn, req_id="FR-1", description="первое")
        with contextlib.suppress(refusals.INPUT_REFUSALS):
            reqs.add_requirement(conn, req_id="FR-1", description="второе")
        rows = conn.execute("SELECT description FROM requirements WHERE id = 'FR-1'").fetchall()
        assert [r[0] for r in rows] == ["первое"]


class TestTheMessageNeverClaimsMoreThanIsKnown:
    """Развилка §2.2: сообщение о дубликате, выданное на другое нарушение, — ложь.

    ``PRIMARY KEY`` — не единственное ограничение этой вставки. Колонки
    ``description``, ``section``, ``source`` и ``test_coverage`` объявлены
    ``NOT NULL``, и их нарушение достижимо у доменной функции (замерено:
    ``add_requirement(conn, req_id='FR-9', description=None)`` поднимает
    ``IntegrityError`` с ``sqlite_errorname == 'SQLITE_CONSTRAINT_NOTNULL'``).
    С обеих ПОВЕРХНОСТЕЙ оно сегодня недостижимо — командная строка требует
    ``-d`` и подменяет ``None`` пустой строкой, а SDK протокола отбивает
    ``None`` собственной проверкой типов раньше тела инструмента, — но
    ``add_requirement`` публична, и правило «сообщение не имеет права
    утверждать больше, чем известно» проверяется там, где оно живёт.
    """

    def test_a_not_null_violation_is_not_reported_as_a_duplicate(self, conn):
        with pytest.raises(refusals.INPUT_REFUSALS) as caught:
            reqs.add_requirement(conn, req_id="FR-НОВЫЙ", description=None)
        text = str(caught.value)
        assert "already exists" not in text, (
            "требования FR-НОВЫЙ в таблице нет — сообщение о дубликате здесь было бы "
            f"ложью, выданной за отказ: {text!r}"
        )

    def test_a_not_null_violation_is_still_a_refusal_with_words(self, conn):
        """Не различить — законно; промолчать или упасть аварией — нет."""
        with pytest.raises(refusals.INPUT_REFUSALS) as caught:
            reqs.add_requirement(conn, req_id="FR-НОВЫЙ", description=None)
        text = str(caught.value)
        assert "FR-НОВЫЙ" in text, text
        assert text.strip(), "отказ без слов — это та же потеря текста, только ближе"


class TestTheCommandLineSurface:
    """Обе половины через НАСТОЯЩИЙ вход процесса.

    Через подпроцесс, а не вызовом обработчика: половина (б) — это отсутствующая
    ОБЁРТКА, и её отсутствие видно только на пути, где исключение доходит до
    входа процесса. Проверка, зовущая доменную функцию, зелена и с дефектом, и
    без него.
    """

    @staticmethod
    def _run(project, *args):
        return subprocess.run(
            [sys.executable, "-m", "codebugs.cli", *args],
            cwd=project,
            capture_output=True,
            text=True,
        )

    def test_a_duplicate_identifier_prints_one_line_and_no_traceback(self, tmp_project):
        first = self._run(tmp_project, "reqs-add", "FR-1", "-d", "первое")
        assert first.returncode == 0, first.stderr
        second = self._run(tmp_project, "reqs-add", "FR-1", "-d", "второе")
        assert second.returncode == 1, second.stderr
        assert "Traceback" not in second.stderr, second.stderr
        assert "sqlite3" not in second.stderr, (
            "сырой текст библиотеки доступа к базе — это внутренность, "
            f"а не ответ человеку: {second.stderr!r}"
        )
        assert "FR-1" in second.stderr, second.stderr
        lines = [ln for ln in second.stderr.splitlines() if ln.strip()]
        assert len(lines) == 1, second.stderr

    def test_an_unknown_priority_prints_one_line_and_no_traceback(self, tmp_project):
        """Половина (б). Механизм здесь — отсутствующая обёртка, а НЕ ограничение
        ``CHECK`` в схеме: резолвер ``types.resolve_priority`` вызывается первой
        же строкой тела ``add_requirement`` и отбивает недопустимое значение
        раньше, чем что-либо доходит до SQL.
        """
        r = self._run(tmp_project, "reqs-add", "FR-2", "-d", "x", "--priority", "bogus")
        assert r.returncode == 1, r.stderr
        assert "Traceback" not in r.stderr, r.stderr
        assert "Invalid priority" in r.stderr, r.stderr
        lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
        assert len(lines) == 1, r.stderr

    def test_an_unknown_status_prints_one_line_and_no_traceback(self, tmp_project):
        r = self._run(tmp_project, "reqs-add", "FR-3", "-d", "x", "--status", "bogus")
        assert r.returncode == 1, r.stderr
        assert "Traceback" not in r.stderr, r.stderr
        assert "Invalid requirement status" in r.stderr, r.stderr

    def test_the_successful_path_is_untouched(self, tmp_project):
        r = self._run(tmp_project, "reqs-add", "FR-4", "-d", "x")
        assert r.returncode == 0, r.stderr
        assert "Added: FR-4" in r.stdout, r.stdout

    def test_a_refused_add_leaves_no_row_behind(self, tmp_project):
        self._run(tmp_project, "reqs-add", "FR-5", "-d", "первое")
        self._run(tmp_project, "reqs-add", "FR-5", "-d", "второе")
        shown = self._run(tmp_project, "reqs-query", "--id", "FR-5")
        assert "первое" in shown.stdout, shown.stdout
        assert "второе" not in shown.stdout, shown.stdout


class TestTheProtocolSurface:
    """Тот же отказ на второй поверхности, проверенный ВЕРСИОННО-НЕЗАВИСИМО.

    Проверяется класс, выходящий из тела инструмента через адаптер CB-310, а не
    текст на проводе, и причина ровно та, что записана в шапке
    ``tests/test_cb310_refusal_text.py``: под ``mcp`` 2.0.0 SDK приписывает
    текст любого исключения к своему сообщению, поэтому проверка по тексту была
    бы зелена и с дефектом — она измеряла бы SDK, а не починку. Класс — решение
    самого пакета, и оно от версии не зависит.
    """

    @staticmethod
    def _factory(project):
        @contextlib.contextmanager
        def factory():
            connection = db.connect(project)
            try:
                yield connection
            finally:
                connection.close()

        return factory

    def test_a_duplicate_identifier_becomes_a_tool_error_carrying_its_text(self, tmp_project):
        factory = self._factory(tmp_project)

        def body():
            with factory() as connection:
                return reqs.add_requirement(connection, req_id="FR-1", description="второе")

        with self._factory(tmp_project)() as connection:
            reqs.add_requirement(connection, req_id="FR-1", description="первое")

        wrapped = server._refusal_reaches_the_client(body)
        with pytest.raises(ToolError) as caught:
            wrapped()
        assert "FR-1" in str(caught.value), str(caught.value)
        assert "already exists" in str(caught.value), str(caught.value)

    def test_an_unknown_priority_becomes_a_tool_error_carrying_its_text(self, tmp_project):
        factory = self._factory(tmp_project)

        def body():
            with factory() as connection:
                return reqs.add_requirement(
                    connection, req_id="FR-2", description="x", priority="bogus"
                )

        wrapped = server._refusal_reaches_the_client(body)
        with pytest.raises(ToolError) as caught:
            wrapped()
        assert "Invalid priority" in str(caught.value), str(caught.value)
