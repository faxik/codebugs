"""CB-326: отказ на входе в инструмент говорит словами ПРОЕКТА, а не чужой библиотеки.

ЧТО ЗА ГРАНИЦА И ПОЧЕМУ ОНА ЧЕТВЁРТАЯ. Модель ошибок этого проекта описывает
три канала, по которым отказ доходит до вызывающего: доменный отказ (текст
проекта, результат с признаком ошибки), авария (текст удерживается, в журнале
сервера), отказ слоя строгих имён аргументов (текст проекта, но ошибкой
протокола). Четвёртый канал — проверка аргументов на ВХОДЕ в инструмент, ДО его
тела, — не был описан вовсе, и клиент получал по нему текст библиотеки проверки
типов вместе со ссылкой на её сайт.

ДВЕ ОСИ, И ВТОРУЮ ПЕРВЫЙ КРУГ ПОЧИНКИ ПРОГЛЯДЕЛ. Проверять этот канал надо
вдоль двух независимых осей, и упустить любую значит проверить не то:

- **редакция протокола.** `initialize()` согласует СТАРУЮ редакцию, `discover()`
  — ТЕКУЩУЮ, где у результата вызова есть ОБЯЗАТЕЛЬНОЕ поле `resultType`.
  Первый круг починки собирал ответ руками из полей `content` и `isError`; в
  старой редакции это проходило, а в текущей клиентская библиотека отвергала
  такой ответ своим же проверяльщиком, и вызов не возвращал значение, а
  ПОДНИМАЛ исключение. То есть менялся КАНАЛ доставки отказа — ровно то, что
  этой карте запрещено менять. Поэтому здесь почти всё проверяется под обеими
  редакциями, а не под одной.
- **форма поля аргументов.** Вызов вовсе без аргументов и вызов с пустым
  отображением — это ДВЕ РАЗНЫЕ формы на проводе: в первом случае клиентская
  библиотека не посылает поле `arguments` совсем. Первый круг проверял только
  «это отображение?», отсутствующее поле давало `None`, проверка не проходила —
  и самая частая форма ровно того дефекта, ради которого заведена карта,
  оставалась открытой.

КАК УСТРОЕНА ПОЧИНКА, И ПОЧЕМУ ИМЕННО ТАК. Слой вычисляет недостающие
обязательные поля ДО вызова, затем вызывает следующее звено КАК ОБЫЧНО, и лишь
потом, если вернулся результат с признаком ошибки и недостающие поля есть,
подменяет в ответе ТОЛЬКО СОДЕРЖИМОЕ, оставляя конверт (`resultType`,
метаданные и всё прочее) таким, каким его построила сама библиотека. Так снят
целый класс поломок: конверт перестал быть заботой этого проекта, и смена
редакции протокола его больше не ломает.

Различитель при этом не текстовый: множество недостающих полей вычислено ДО
вызова, и если оно непусто, то дальше заведомо будет отказ проверки схемы —
сопоставлять текст чужой библиотеки не требуется. (Первый круг отверг этот путь,
ошибочно решив, что различить отказ проверки схемы и доменный отказ можно только
по тексту.) Тело инструмента при этом всё равно не запускается: его
останавливает та же проверка схемы, что и раньше.

ПОЧЕМУ ПРОГОН ЧЕРЕЗ НАСТОЯЩЕГО КЛИЕНТА. Урок CB-310: десять тестов в наборе
проверяли поверхность, построенную руками, и все десять молчали о том, что видит
клиент. Обещание этой карты дано клиенту, поэтому и проверяется на клиенте.
Обвязка живого сеанса берётся из `tests/test_cb310_refusal_text.py` — одно
определение на весь набор; ось редакции протокола добавлена туда же, а не заведена
здесь второй копией.
"""

from __future__ import annotations

import contextlib

import pytest

from codebugs import db, server, usage
from tests.test_cb310_refusal_text import call_over_the_wire

#: Признаки чужого происхождения текста. Ссылка на сайт — буквальная формулировка
#: пункта критерия приёмки; две другие добавлены потому, что библиотека может
#: убрать ссылку, не перестав говорить своим голосом, и тогда проверка по одной
#: ссылке зазеленела бы на неисправленной беде.
_FOREIGN_MARKS = ("errors.pydantic.dev", "validation error", "Arguments")

#: Обе редакции протокола. Прогонять под обеими — не перестраховка: расхождение
#: между ними и было блокирующим дефектом первого круга.
PROTOCOLS = ("initialize", "discover")


@pytest.fixture
def tracker(tmp_path):
    """Настоящий трекер на диске плюс фабрика соединений, которую берёт сервер."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project = str(project_dir)
    db.init_project(project)

    @contextlib.contextmanager
    def _conn():
        conn = db.connect(project)
        try:
            yield conn
        finally:
            conn.close()

    return project, _conn


def _built(tracker, mode: str = "findings"):
    _, factory = tracker
    return server._build_server(mode, factory)


def _innermost(exc: BaseException) -> BaseException:
    """Самое внутреннее исключение: группы исключений разворачиваются.

    Живой сеанс идёт под группой задач, и отказ протокола выходит наружу
    завёрнутым. Без разворачивания проверка утверждала бы про класс обёртки, а
    не про класс отказа.
    """
    inner = getattr(exc, "exceptions", None)
    return _innermost(inner[0]) if inner else exc


def _rows(tracker):
    _, factory = tracker
    with factory() as conn:
        return usage.usage_summary(conn)["rows"]


#: Полный набор аргументов для `add`. `new_category` обязателен: в пустом трекере
#: любая категория новая, и её появление огорожено отдельным гейтом (CB-60).
_COMPLETE = {
    "severity": "low",
    "category": "x",
    "file": "y",
    "description": "z",
    "new_category": True,
}


class TestTheRefusalSpeaksTheProjectsWords:
    """Предмет карты: не поданное обязательное поле отказывается словами проекта."""

    @pytest.mark.parametrize("protocol", PROTOCOLS)
    def test_the_answer_carries_no_pointer_to_the_validation_librarys_site(
        self, tracker, protocol
    ):
        """Буквальный пункт критерия приёмки: чужой марки в ответе нет.

        Под ОБЕИМИ редакциями протокола, потому что первый круг починки был
        зелен под старой и ронял клиента под текущей.
        """
        _, text = call_over_the_wire(
            _built(tracker),
            "add",
            {"category": "x", "file": "y", "description": "z"},
            protocol=protocol,
        )

        for mark in _FOREIGN_MARKS:
            assert mark not in text, f"в ответе осталась чужая марка {mark!r}: {text!r}"

    @pytest.mark.parametrize("protocol", PROTOCOLS)
    def test_the_refusal_is_RETURNED_and_never_RAISED(self, tracker, protocol):
        """ГЛАВНАЯ проверка второго круга, и она про КАНАЛ, а не про текст.

        Отказ проверки аргументов всегда приходил РЕЗУЛЬТАТОМ с признаком
        ошибки: клиентская библиотека его ВОЗВРАЩАЕТ, и код вызывающего написан
        под возврат. Собранный руками ответ первого круга не нёс обязательного
        поля `resultType`, поэтому под текущей редакцией протокола клиентская
        библиотека отвергала его своим же проверяльщиком и ПОДНИМАЛА исключение
        — то есть чинилка меняла способ, которым каждый вызывающий обязан
        обрабатывать этот отказ. Здесь проверяется, что не поднимает: сам факт
        возврата пары значений и есть утверждение, потому что исключение
        вылетело бы из вызова.
        """
        is_error, text = call_over_the_wire(
            _built(tracker),
            "add",
            {"category": "x", "file": "y", "description": "z"},
            protocol=protocol,
        )

        assert is_error is True
        assert text, "отказ обязан нести текст, иначе это безымянная авария CB-310"

    @pytest.mark.parametrize("protocol", PROTOCOLS)
    def test_a_call_with_NO_arguments_at_all_is_answered_in_the_projects_words(
        self, tracker, protocol
    ):
        """Самая частая форма этого дефекта, и первый круг её не закрыл.

        Вызывая инструмент без аргументов, клиентская библиотека НЕ ПОСЫЛАЕТ
        поле `arguments` совсем — это отдельная форма на проводе, не пустое
        отображение. Первый круг спрашивал «это отображение?», получал `None` и
        пропускал запрос к чужой проверке схемы, так что именно самый обычный
        способ ошибиться оставался неисправленным.
        """
        is_error, text = call_over_the_wire(_built(tracker), "add", None, protocol=protocol)

        assert is_error is True
        for mark in _FOREIGN_MARKS:
            assert mark not in text, f"в ответе осталась чужая марка {mark!r}: {text!r}"
        assert "severity" in text

    @pytest.mark.parametrize("protocol", PROTOCOLS)
    def test_an_EMPTY_arguments_mapping_is_answered_in_the_projects_words(
        self, tracker, protocol
    ):
        """Вторая форма той же оси: поле послано и равно пустому отображению.

        Отличается от предыдущей на проводе, поэтому проверяется отдельно, а не
        считается тем же случаем. Обе обязаны отвечать одинаково — «ничего не
        подано» есть «ничего не подано», как бы клиент это ни записал.
        """
        is_error, text = call_over_the_wire(_built(tracker), "add", {}, protocol=protocol)

        assert is_error is True
        for mark in _FOREIGN_MARKS:
            assert mark not in text
        assert "severity" in text

    def test_a_missing_required_field_is_named_in_the_projects_own_text(self, tracker):
        """Имя пропущенного поля обязано сохраниться: сегодняшний чужой текст
        называет `severity`, и это весь полезный ответ. Починка, которая
        избавилась бы от чужой марки ценой имени поля, была бы ухудшением.

        ЗЕЛЁН ПО ОБЕ СТОРОНЫ ПРАВКИ, и это его назначение: он закрепляет
        СОДЕРЖАНИЕ, которое правка обязана сохранить, а не отличие, которое она
        вносит (правило `tests/CLAUDE.md`).
        """
        is_error, text = call_over_the_wire(
            _built(tracker), "add", {"category": "x", "file": "y", "description": "z"}
        )

        assert is_error is True
        assert "severity" in text
        assert "add" in text

    def test_two_missing_fields_are_both_named(self, tracker):
        """Перечисление, а не первое попавшееся имя. ЗЕЛЁН ПО ОБЕ СТОРОНЫ."""
        _, text = call_over_the_wire(_built(tracker), "add", {"category": "x", "description": "z"})

        assert "severity" in text
        assert "file" in text

    def test_the_answer_says_WHICH_fields_are_missing_and_not_merely_that_some_are(self, tracker):
        """Эту проверку добавила мутационная проба, а не замысел.

        Первая формулировка — «в тексте есть слово `severity`» — мутанта
        пережила: он выбросил перечень ПРОПУЩЕННЫХ полей, оставив перечень
        ОБЯЗАТЕЛЬНЫХ, а тот содержит все имена сразу, поэтому по вхождению
        отдельного слова полезный ответ неотличим от бесполезного.

        Различие берётся сравнением ДВУХ ответов, а не разбором формата одного:
        вызов без одного поля и вызов без двух обязаны отвечать РАЗНЫМ текстом.
        Так проверка не закрепляет ни порядок слов, ни знаки препинания — и
        переживает переписывание формулировки, оставаясь способной поймать
        потерю самого содержания.
        """
        one = call_over_the_wire(
            _built(tracker), "add", {"category": "x", "file": "y", "description": "z"}
        )[1]
        two = call_over_the_wire(_built(tracker), "add", {"category": "x", "description": "z"})[1]

        assert one != two, (
            "ответы на «не подано одно поле» и «не подано два» совпали — значит "
            f"текст не называет, каких именно полей не хватает: {one!r}"
        )

    def test_the_required_set_is_read_from_the_tools_own_schema(self, tracker):
        """Слой обязан быть общим, а не написанным под инструмент `add`.
        Проверяется на другом инструменте с другим набором обязательных полей:
        у требований это `req_id`, которого у находок нет вовсе."""
        is_error, text = call_over_the_wire(
            _built(tracker, "reqs"), "reqs_add", {"description": "z"}
        )

        assert is_error is True
        assert "req_id" in text
        for mark in _FOREIGN_MARKS:
            assert mark not in text

    def test_the_answer_does_not_claim_that_nothing_was_written(self, tracker):
        """Отказ не имеет права утверждать больше, чем знает.

        Первая формулировка обещала «инструмент не запускался и ничего не
        записано». Вторая половина — неправда: слой учёта вызовов пишет строку в
        таблицу `tool_calls` на каждом вызове, и это доказывает соседняя
        проверка в `TestPlacementInTheChain`. Отказ вправе утверждать ровно то,
        что верно: ТЕЛО инструмента не выполнялось. Утверждение про базу
        целиком было бы той самой ложью об исходе, которую модель ошибок этого
        проекта запрещает (CB-15/CB-16).
        """
        _, text = call_over_the_wire(
            _built(tracker), "add", {"category": "x", "file": "y", "description": "z"}
        )

        assert "nothing was written" not in text
        assert "tool body did not run" in text


class TestTheDeclaredPrecedence:
    """Что происходит, когда бед сразу две. Правило объявлено — значит проверено."""

    @pytest.mark.parametrize("protocol", PROTOCOLS)
    def test_a_missing_field_wins_over_a_wrong_type_in_the_same_call(self, tracker, protocol):
        """ОБЪЯВЛЕННОЕ ПРАВИЛО СТАРШИНСТВА, и оно стоит своей цены.

        Если одно обязательное поле не подано, а другое подано неверного типа,
        ответ говорит о НЕ ПОДАННОМ и молчит о типе. Так выходит потому, что
        слой подменяет содержимое целиком, а не дописывает к чужому тексту:
        дописать значило бы оставить в ответе чужую марку и провалить главный
        пункт этой карты.

        ЦЕНА НАЗВАНА, А НЕ ЗАМОЛЧАНА: до этой карты клиент видел обе беды сразу
        и чинил их за один заход, а теперь узнаёт о второй лишь следующим
        вызовом. Это лишний круг переписки в редком случае. Другой выход —
        диагностировать типы самим — означал бы второй проверщик схемы рядом с
        настоящим, а два проверщика расходятся, и расходятся в дорогую сторону,
        начиная отказывать значениям, которые инструмент бы принял. Из двух зол
        выбран лишний круг.
        """
        is_error, text = call_over_the_wire(
            _built(tracker),
            "add",
            {"category": "x", "file": "y", "severity": 5},
            protocol=protocol,
        )

        assert is_error is True
        assert "description" in text
        for mark in _FOREIGN_MARKS:
            assert mark not in text, "чужая марка вернулась через смешанный случай"

    @pytest.mark.parametrize("protocol", PROTOCOLS)
    def test_a_wrong_type_alone_is_deliberately_not_claimed_by_this_layer(self, tracker, protocol):
        """ОБЪЯВЛЕННЫЙ ОСТАТОК, а не забытый случай.

        Когда пропущенных полей НЕТ, слой не вмешивается вовсе, и неверный тип
        отвечает словами чужой библиотеки. Проверка утверждает про СВОЁ
        поведение, а не про чужой текст: чужой текст принадлежит библиотеке,
        допущенной диапазоном версий, и закреплять его здесь значило бы делать
        набор хрупким к обновлению, которое проект разрешает.
        """
        is_error, text = call_over_the_wire(
            _built(tracker),
            "add",
            {"severity": 5, "category": "x", "file": "y", "description": "z"},
            protocol=protocol,
        )

        assert is_error is True
        assert "Missing required" not in text


class TestTheOtherChannelsAreUntouched:
    """Контроль. Проверка, у которой нет случаев, обязанных не измениться, не
    умеет отличить «ничего лишнего не сломалось» от «смотрит не туда».

    ВЕСЬ ЭТОТ КЛАСС ЗЕЛЁН ПО ОБЕ СТОРОНЫ ПРАВКИ, и иначе быть не может: он про
    поведение, которое правка обязана оставить нетронутым. Его краснота
    означала бы, что новый слой захватил чужой канал.
    """

    @pytest.mark.parametrize("protocol", PROTOCOLS)
    def test_an_undeclared_argument_name_still_arrives_as_a_protocol_error(
        self, tracker, protocol
    ):
        """Слой строгих имён аргументов решает ДРУГОЙ вопрос, и его нынешняя
        работа не входит в задание этого юнита."""
        with pytest.raises(BaseException) as excinfo:  # noqa: B017 — предмет и есть форма
            call_over_the_wire(
                _built(tracker),
                "add",
                {"severity": "low", "category": "x", "file": "y", "description": "z", "bogus": 1},
                protocol=protocol,
            )

        assert "Unknown argument(s)" in str(_innermost(excinfo.value))

    @pytest.mark.parametrize("protocol", PROTOCOLS)
    def test_a_domain_refusal_still_carries_its_own_text(self, tracker, protocol):
        """Доменный отказ приходит в той же ФОРМЕ, что и отказ проверки
        аргументов, поэтому именно он показал бы, что новый слой хватает
        лишнее."""
        is_error, text = call_over_the_wire(
            _built(tracker), "update", {"finding_id": "CB-1", "status": "bogus"}, protocol=protocol
        )

        assert is_error is True
        assert "Invalid finding status" in text

    @pytest.mark.parametrize("protocol", PROTOCOLS)
    def test_an_unknown_tool_name_is_still_answered_by_the_sdk(self, tracker, protocol):
        """Слой строгих имён намеренно оставляет несуществующее имя инструмента
        чужой библиотеке — «не наше дело отвечать». Новый слой обязан вести себя
        так же, иначе он подменит собой чужой ответ на чужой вопрос."""
        is_error, text = call_over_the_wire(
            _built(tracker), "no_such_tool", {"anything": 1}, protocol=protocol
        )

        assert is_error is True
        assert "no_such_tool" in text
        assert "Missing required" not in text

    @pytest.mark.parametrize("protocol", PROTOCOLS)
    def test_a_complete_call_still_runs(self, tracker, protocol):
        """Обратная сторона: слой, отказывающий всем, прошёл бы каждую проверку
        выше."""
        is_error, text = call_over_the_wire(_built(tracker), "add", dict(_COMPLETE),
                                            protocol=protocol)

        assert is_error is False, text
        assert '"dedup_action": "created"' in text


class TestPlacementInTheChain:
    """Размещение слоя. Это не деталь: оно решает, что видит учёт вызовов.

    ОБА ТЕСТА ЗЕЛЕНЫ ПО ОБЕ СТОРОНЫ ПРАВКИ — они закрепляют учёт, каким он был
    ДО неё. Различающим их делает не правка, а МУТАНТ: перестановка нового слоя
    наружу учётного красит первый из них, и только его.
    """

    def test_a_missing_field_refusal_is_still_counted_as_a_failure_of_that_tool(self, tracker):
        """ОРАКУЛ РАЗМЕЩЕНИЯ. Такой вызов записывается в `tool_calls` как отказ
        инструмента `add`, потому что чужая библиотека перехватывает его ВНУТРИ,
        за спиной учётного слоя. Новый слой, поставленный снаружи учётного,
        отобрал бы у таблицы эту запись — не изменив при этом ни байта в ответе
        клиенту, то есть незаметно для всех остальных проверок. А улика самой
        карты CB-326 взята именно из этой таблицы."""
        call_over_the_wire(
            _built(tracker), "add", {"category": "x", "file": "y", "description": "z"}
        )

        rows = _rows(tracker)
        assert [(r["tool_name"], r["calls"], r["failures"]) for r in rows] == [("add", 1, 1)]

    def test_an_undeclared_argument_refusal_is_still_not_counted(self, tracker):
        """Вторая половина того же вопроса, и она про ДРУГОЙ канал. Отказ слоя
        строгих имён не считается сегодня — это записанное решение
        (`install_usage_tracking`), а не случайность порядка вызовов."""
        with contextlib.suppress(BaseException):
            call_over_the_wire(
                _built(tracker),
                "add",
                {"severity": "low", "category": "x", "file": "y", "description": "z", "bogus": 1},
            )

        assert _rows(tracker) == []


class TestTheLayerIsActuallyInstalled:
    """Слой можно написать, проверить и никогда не подключить — и набор тестов
    останется зелёным. Тот же урок отдельно закреплён для сторожей харнеса
    рабочих деревьев (`tests/test_worktree_harness.py`)."""

    def test_build_server_puts_the_layer_inside_usage_tracking(self, tracker):
        """Порядок списка — снаружи внутрь. Строгие имена снаружи (их отказ не
        считается), учёт посередине, обязательные поля внутри (их отказ
        считается).

        Утверждение делается о ВЗАИМНОМ порядке слоёв ЭТОГО пакета, а не о всём
        списке, и это не послабление, а точность. Замерено: чужая библиотека
        сама кладёт в тот же список два своих слоя впереди наших — под обеими
        допущенными версиями одинаково. Они не принадлежат этому проекту, и
        закрепление их присутствия покрасило бы набор при обновлении библиотеки
        по причине, никак не связанной с этой картой. Свои слои отбираются по
        признаку, которого у чужих нет: они простые функции и потому несут
        `__name__`.
        """
        built = _built(tracker)

        ours = [mw.__name__ for mw in built.middleware if hasattr(mw, "__name__")]
        assert ours == [
            "reject_unknown_arguments",
            "record_tool_usage",
            "refuse_missing_required_arguments",
        ]
