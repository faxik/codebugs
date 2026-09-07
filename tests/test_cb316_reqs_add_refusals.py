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
import os
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
        """ПОГЛОЩЕНА следующей проверкой, и это сказано вслух, а не обойдено.

        Ни один член `refusals.INPUT_REFUSALS` не является и не может стать
        потомком `sqlite3.Error`, поэтому зелёная следующая проверка делает эту
        зелёной автоматически. Она оставлена потому, что говорит о дефекте на
        языке самой карточки — «чужой пакету класс вышел наружу», — и потому,
        что на непочиненном дереве краснела; поглощение не то же, что
        бессодержательность.
        """
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


class TestTheRefusalIsNarrowerThanTheExceptionTree:
    """Только НАРУШЕНИЕ ОГРАНИЧЕНИЙ становится отказом — не всё дерево SQLite.

    Эта граница — ратифицированное решение CB-99 тридцатью строками ниже в том
    же файле: перехват `sqlite3.Error` (всё дерево) считал бы средовой сбой,
    пришедший посреди записи — полный диск, отказ ввода-вывода, — «плохой
    строкой» и докладывал бы его как ошибку ввода. Это строго хуже аварийной
    распечатки, потому что распечатка громкая.

    Без этой проверки граница не закреплена ничем: все прочие подсовываемые в
    этом файле отказы — нарушения ограничений, поэтому мутант, расширяющий
    перехват до `sqlite3.DatabaseError`, проходил бы весь файл незамеченным.
    """

    def test_an_operational_failure_passes_through_unchanged(self):
        """Таблицы нет — вставка даёт `OperationalError`, а не отказ по вводу.

        `sqlite3.OperationalError` — сестра `IntegrityError` под общим предком
        `sqlite3.DatabaseError`, поэтому расширение перехвата до предка ловит
        именно её. Отсутствие таблицы взято как самый дешёвый способ получить
        настоящий `OperationalError` из НАСТОЯЩЕЙ вставки; предмет проверки —
        класс, а не причина его появления.
        """
        bare = sqlite3.connect(":memory:")
        bare.row_factory = sqlite3.Row
        try:
            with pytest.raises(sqlite3.OperationalError):
                reqs.add_requirement(bare, req_id="FR-1", description="x")
        finally:
            bare.close()

    def test_an_operational_failure_is_not_an_input_refusal(self):
        """Вторая половина того же утверждения, и она нужна отдельно.

        Первая проверка требует `OperationalError`; эта требует, чтобы он не
        был вдобавок отказом по вводу. Порознь их обойти можно, вместе — нет.
        """
        bare = sqlite3.connect(":memory:")
        bare.row_factory = sqlite3.Row
        try:
            with pytest.raises(sqlite3.Error) as caught:
                reqs.add_requirement(bare, req_id="FR-1", description="x")
        finally:
            bare.close()
        assert not isinstance(caught.value, refusals.INPUT_REFUSALS), (
            "средовой сбой посреди записи доложен как ошибка ввода — "
            "ровно то, что запретила CB-99"
        )


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

    def test_a_conflict_on_ANOTHER_table_is_not_reported_as_a_duplicate(self, conn):
        """Код ошибки называет РАЗРЯД ограничения, а не таблицу — вход построен.

        Измерено: триггер `BEFORE INSERT ON requirements`, вставляющий строку в
        ЧУЖУЮ таблицу с конфликтом её первичного ключа, даёт исключение с
        `sqlite_errorname == 'SQLITE_CONSTRAINT_PRIMARYKEY'` и текстом
        `UNIQUE constraint failed: audit.id`. Различение по этому имени кода
        отвечало здесь «требование FR-НОВЫЙ уже существует» про строку, которой
        в таблице требований нет вовсе.

        Сегодняшняя поставляемая схема триггеров не несёт (измерено: ноль строк
        типа `trigger` в `sqlite_master`), поэтому этот вход недостижим у
        пользователя — но утверждение «нет входа, на котором это выдумало бы
        дубликат» было шире своего замера, а это ровно тот класс дефекта,
        которым живёт направление. Закрыто структурно: конфликт по `id`
        доказывается ЦЕЛЕВЫМ предложением `ON CONFLICT(id) DO NOTHING`, вернувшим
        ноль строк, а не догадкой по коду.
        """
        conn.execute("CREATE TABLE audit (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO audit VALUES ('only')")
        conn.execute(
            "CREATE TRIGGER t_audit BEFORE INSERT ON requirements "
            "BEGIN INSERT INTO audit VALUES ('only'); END"
        )
        with pytest.raises(refusals.INPUT_REFUSALS) as caught:
            reqs.add_requirement(conn, req_id="FR-НОВЫЙ", description="x")
        text = str(caught.value)
        assert "already exists" not in text, (
            "нарушено ограничение ЧУЖОЙ таблицы, а отказ доложил дубликат "
            f"требования, которого в таблице нет: {text!r}"
        )

    def test_the_databases_own_words_do_not_reach_the_client(self, conn):
        """Запасное сообщение не выносит наружу произвольный текст базы.

        `RAISE(ABORT, '…')` в триггере кладёт в текст исключения любые слова, и
        под версией 2.1.1 библиотеки протокола такой текст РАНЬШЕ придерживался
        — авария доходила до клиента без слов. Шапка `refusals.py` объясняет
        почему: сообщение неожиданного исключения может нести данные другого
        вызывающего. Превратив этот класс в отказ, мы обязаны не приложить к
        нему чужие слова.

        Исходное исключение остаётся в цепочке через `raise … from`, поэтому
        вызывающий из библиотеки увидит слова базы в трассировке — для
        единственной достижимой сегодня аудитории не теряется ничего.
        """
        conn.execute(
            "CREATE TRIGGER t_raise BEFORE INSERT ON requirements "
            "BEGIN SELECT RAISE(ABORT, 'SECRET-MARKER-9137'); END"
        )
        with pytest.raises(refusals.INPUT_REFUSALS) as caught:
            reqs.add_requirement(conn, req_id="FR-НОВЫЙ", description="x")
        assert "SECRET-MARKER-9137" not in str(caught.value), str(caught.value)
        assert "SECRET-MARKER-9137" in str(caught.value.__cause__), (
            "слова базы обязаны остаться в цепочке — иначе потеряна диагностика"
        )

    def test_an_identifier_carrying_a_newline_cannot_forge_a_second_line(self, conn):
        """Идентификаторы ничем не ограничены, а договор обещает ОДНУ строку.

        Подставленный как есть, `FR-9\\nforged` печатает две непустые строки, и
        вторая читается как самостоятельное сообщение программы. Подстановка
        через представление — уже принятая в пакете форма: `types._resolve`
        пишет `f"Invalid {label}: {value!r}"`.
        """
        forged = "FR-9\nforged"
        reqs.add_requirement(conn, req_id=forged, description="первое")
        with pytest.raises(refusals.INPUT_REFUSALS) as caught:
            reqs.add_requirement(conn, req_id=forged, description="второе")
        text = str(caught.value)
        assert "\n" not in text, f"отказ подделал вторую строку вывода: {text!r}"


class TestARefusalInsideTheCallersTransaction:
    """Вложенный случай: `db.txn` под чужой транзакцией НЕ делает ничего.

    При уже открытой транзакции `db.txn` отдаёт `False` и не выполняет ни
    `BEGIN`, ни `ROLLBACK` — распоряжается кадр-владелец. Значит утверждение
    «к моменту построения текста база улеглась» верно только для транзакции,
    открытой ЭТИМ кадром, и проверка ниже закрепляет, что в чужой транзакции
    работа вызывающего цела, а сама транзакция осталась открытой.

    Поведение при этом корректно и без всяких усилий: политика разрешения
    конфликта по умолчанию (`ABORT`) отменяет только неудавшийся оператор, а
    не транзакцию.
    """

    def test_the_callers_transaction_stays_open_and_intact(self, conn):
        reqs.add_requirement(conn, req_id="FR-1", description="первое")
        with db.txn(conn) as owned:
            assert owned is True
            reqs.add_requirement(conn, req_id="FR-2", description="работа вызывающего")
            with pytest.raises(refusals.INPUT_REFUSALS):
                reqs.add_requirement(conn, req_id="FR-1", description="повтор")
            assert conn.in_transaction, "чужая транзакция закрыта чужим кадром"
        stored = {r[0] for r in conn.execute("SELECT id FROM requirements")}
        assert stored == {"FR-1", "FR-2"}, stored

    def test_a_constraint_violation_inside_it_is_also_survivable(self, conn):
        """Тот же вопрос для отказа, поднятого САМОЙ базой, а не Python-ветвью.

        Повтор идентификатора отбивается целевым `ON CONFLICT` и до исключения
        SQLite не доходит; нарушение обязательности значения доходит, и именно
        на нём проверяется, что оператор отменён, а транзакция — нет.
        """
        with db.txn(conn):
            reqs.add_requirement(conn, req_id="FR-2", description="работа вызывающего")
            with pytest.raises(refusals.INPUT_REFUSALS):
                reqs.add_requirement(conn, req_id="FR-3", description=None)
            assert conn.in_transaction
        stored = {r[0] for r in conn.execute("SELECT id FROM requirements")}
        assert stored == {"FR-2"}, stored


class TestTheCommandLineSurface:
    """Обе половины через НАСТОЯЩИЙ вход процесса.

    Через подпроцесс, а не вызовом обработчика: половина (б) — это отсутствующая
    ОБЁРТКА, и её отсутствие видно только на пути, где исключение доходит до
    входа процесса. Проверка, зовущая доменную функцию, зелена и с дефектом, и
    без него.
    """

    @staticmethod
    def _run(project, *args, env=None):
        return subprocess.run(
            [sys.executable, "-m", "codebugs.cli", *args],
            cwd=project,
            capture_output=True,
            text=True,
            env={**os.environ, **env} if env else None,
        )

    def test_a_committed_write_is_never_reported_as_bad_input(self, tmp_project):
        """CB-15/CB-16 в новом месте: печать успеха стояла ВНУТРИ помощника.

        Механизм, целиком. `UnicodeEncodeError` наследует от `ValueError`,
        который единый источник классификации называет отказом по вводу. Пока
        строка «Added: …» печаталась внутри `with domain_errors():`, отказ
        кодировки при её печати — то есть сбой, случившийся ПОСЛЕ того, как
        запись уже совершилась и транзакция закрылась, — ловился плечом
        проверки ввода, печатался одной опрятной строкой и завершал процесс
        кодом 1. Требование записано в файле правил подсистемы дословно: сбой,
        поднятый после коммита, никогда не докладывается через плечо проверки
        ввода, потому что «ошибка ввода» читается как «ничего не произошло».

        Воспроизведение: `PYTHONIOENCODING=ascii` плюс идентификатор с
        кириллицей. Измерено на непочиненном дереве — `Ж-1` в базе ЕСТЬ, а
        команда отчиталась неудачей.

        Различает здесь ФОРМА, а не код возврата: он равен 1 в обоих случаях.
        Аварийная распечатка — правильный ответ на такой сбой, тот же вывод,
        что и у `TestRetriageCliContract::test_a_committed_write_is_never_
        reported_as_bad_input` для испорченной хранимой строки.
        """
        r = self._run(tmp_project, "reqs-add", "Ж-1", "-d", "x", env={"PYTHONIOENCODING": "ascii"})
        connection = db.connect(tmp_project)
        try:
            landed = connection.execute(
                "SELECT count(*) FROM requirements WHERE id = 'Ж-1'"
            ).fetchone()[0]
        finally:
            connection.close()
        assert landed == 1, "предпосылка проверки: запись обязана была совершиться"
        assert "Traceback" in r.stderr, (
            "запись совершилась, а сбой печати доложен опрятной строкой — "
            f"успех подан как отказ по вводу: {r.stderr!r}"
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

    @pytest.mark.parametrize(
        "req_id, flag, marker",
        [
            ("FR-2", "--priority", "Invalid priority"),
            ("FR-3", "--status", "Invalid requirement status"),
        ],
        ids=["priority", "status"],
    )
    def test_an_unknown_vocabulary_value_prints_one_line(self, tmp_project, req_id, flag, marker):
        """Половина (б). Механизм здесь — отсутствующая обёртка, а НЕ ограничение
        ``CHECK`` в схеме: резолверы ``types.resolve_priority`` и
        ``types.resolve_requirement_status`` вызываются первыми же строками тела
        ``add_requirement`` и отбивают недопустимое значение раньше, чем
        что-либо доходит до SQL.

        Параметризовано, а не скопировано: в первой редакции это были два
        почти одинаковых тела, и второе по недосмотру потеряло утверждение о
        единственной строке вывода — имя обещало «одна строка», а проверялось
        это только в первом. Один набор утверждений над обоими случаями делает
        такую потерю невыразимой.
        """
        r = self._run(tmp_project, "reqs-add", req_id, "-d", "x", flag, "bogus")
        assert r.returncode == 1, r.stderr
        assert "Traceback" not in r.stderr, r.stderr
        assert marker in r.stderr, r.stderr
        lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
        assert len(lines) == 1, r.stderr

    def test_the_successful_path_is_untouched(self, tmp_project):
        r = self._run(tmp_project, "reqs-add", "FR-4", "-d", "x")
        assert r.returncode == 0, r.stderr
        assert "Added: FR-4" in r.stdout, r.stdout

    def test_a_refused_add_leaves_no_row_behind(self, tmp_project):
        """Читается прямо из базы, а не глазами через `reqs-query`.

        Форматировщик таблицы стоит между строкой и утверждением и в принципе
        может её усечь, так что проверка «второе» не видно» через него
        проверяет вывод, а не запись. Прямое чтение спрашивает ровно то, что
        нужно, и экономит третий запуск интерпретатора.
        """
        self._run(tmp_project, "reqs-add", "FR-5", "-d", "первое")
        self._run(tmp_project, "reqs-add", "FR-5", "-d", "второе")
        connection = db.connect(tmp_project)
        try:
            stored = connection.execute(
                "SELECT description FROM requirements WHERE id = 'FR-5'"
            ).fetchall()
        finally:
            connection.close()
        assert [r[0] for r in stored] == ["первое"]


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

        with factory() as connection:
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
