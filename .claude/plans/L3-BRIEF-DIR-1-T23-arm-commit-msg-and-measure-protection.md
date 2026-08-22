# L3-BRIEF DIR-1 / Т-23 — гейт «клон вооружён» требует третий хук; CI-limits п.4 — по измерению

Бриф уровня (2) → уровню (3). Написан 2026-08-22 держателем направления DIR-1 («Доверие и
координация», ценности Н1 «инструменту можно верить» и Н3 «координация механическая, не
конвенция»). **Ты приходишь холодным — всё нужное здесь.**

Юнит назначен **куратором** (пакет DIR-1 сессии 3, пункты 1 и 2, порядок обязателен). Карты нет:
это процессная механика в `tools/` и проза `CLAUDE.md`, территория DIR-1. Ветка:
`fix/t21-arm-commit-msg-hook` (без CB-id — клеймить нечего, `worktree-setup.sh` это переживёт).

Две задачи, **два отдельных коммита на ветке, в этом порядке**, один мердж.

---

## §1. Замысел — то, против чего будут принимать

**Задача 1.** Клон, вооружённый до Т-20, не должен молча жить без `commit-msg`-хука.
`_guard_enforcement_armed` (в `tools/_guards.sh`) — это гейт, чья единственная работа: «этот клон
действительно вооружён». Сегодня он проверяет **два хука из трёх** (`pre-commit` безусловно,
`pre-merge-commit` по монотонному условию) и **не знает о `commit-msg`**. `CLAUDE.md` говорит это
прямо (абзац «What it does NOT close, stated rather than left to be discovered»): «a clone armed
before it landed keeps its other two hooks and silently lacks this one until `tools/install-hooks.sh`
is re-run … Closing it is a one-line follow-up once the source is on main». Источник на main с
`ef60d5f` (проверено `git log --all -1 -- tools/commit-msg-hook.sh`), бутстрап-стена пройдена —
закрываем обещанный follow-up.

**Задача 2.** `CLAUDE.md` не должен описывать гейт хуже ИЛИ лучше, чем он ведёт себя. Абзац
«CI job's own limits», пункт 4, второй residual, утверждает: **`enforce_admins` is `false`** и
«against the owner's own credentials it is advisory». Это было измерено 2026-08-21 и **с тех пор
изменилось** — владелец включил настройку. Я ИЗМЕРИЛ 2026-08-22 (данные для тебя, не истина на
дату твоей правки — перемерь):

```
gh api repos/faxik/codebugs/branches/main/protection
  enforce_admins.enabled=true, allow_force_pushes.enabled=false, allow_deletions.enabled=false,
  required_linear_history=false, required_signatures=false, lock_branch=false;
  ключей required_pull_request_reviews / required_status_checks в ответе НЕТ (require-PR выключен)
gh api repos/faxik/codebugs/rulesets --jq length   → 0
gh api repos/faxik/codebugs --jq .permissions.admin → true
```

Приёмка будет читать дифф **против этих двух предложений**, а не против формы в §2.

## §2. Задание-гипотеза

**Задача 1 (гипотеза куратора):** добавить в `_guard_enforcement_armed` третью проверку —
`_hook_problems "${hook_dir}/commit-msg" "${repo_root}/tools/commit-msg-hook.sh" "commit-msg"` —
под **тем же монотонным условием**, что у `pre-merge-commit`: «путь `tools/commit-msg-hook.sh`
имеет историю (`git log -1 --all -- …`), ИЛИ команда истории упала (fail closed), ИЛИ файл
существует». Не изобретай новую форму условия: та, что есть, прошла четыре раунда ревью (см.
комментарий над `merge_hook_log_ok` — три разных двери на один дефект). Скопировать её
буквально — правильно; **лучше — вынести в локальную функцию и вызвать дважды**, чтобы два
условия не разошлись (тот же принцип, по которому предикат merge-gate дублируется байт-в-байт и
сравнивается тестом). Выбор формы — твой, обоснуй в возврате.

Затем: переписать абзац «What it does NOT close …» в `CLAUDE.md` под факт, и абзац «**What this
does NOT do** … It checks the pre-commit and pre-merge-commit hooks — two of the three» — теперь
три из трёх. Перечисление «два из трёх» встречается в CLAUDE.md больше одного раза — `grep -n
'two of the three\|other two hooks\|commit-msg naming gate is armed by the installer alone'` и
проверь КАЖДОЕ вхождение; это правило-перечисление, и ты чинишь ровно его.

**Задача 2:** перемерить тремя командами выше **на момент правки**, привести пункт 4 CI-limits
(второй residual) к измеренному: `enforce_admins` включён, защита связывает и владельца; цена
включения (аварийный ремонт истории требует сперва выключить настройку) — записать как принятую.
Не переписывать по памяти и не по моему замеру — **по своему**. Первый residual (unarmed clone
может запушить non-merge commit, т.к. require-PR выключен) — проверить, что он всё ещё верен по
тому же ответу API (require-PR по-прежнему отсутствует), и оставить.

## §3. Ловушки — разбери каждую в preflight

1. **Фикстуры тестов не имеют истории `tools/commit-msg-hook.sh`.** `TestEnforcementArmed._arm`
   создаёт тестовый репо с одним `pre-commit-hook.sh`, и `test_fully_armed_passes` ожидает rc 0.
   С чисто-историческим условием гейт в тест-репо никогда бы не фирил (история пуста, файла нет)
   — и тест был бы ЗЕЛЁНЫМ ПО ПОСТРОЕНИЮ. Именно поэтому условие — дизъюнкция с `-e <src>`; тест
   «клон без commit-msg отказывает» обязан **создать историю или файл** в тест-репо (как делает
   `_arm_merge_hook` для второго хука), иначе он не тестирует ветку с историей. Прогони тест
   против НЕизменённого гейта и убедись, что он красный (TDD, К-4).
2. **Гейт отказывает exit 12, а не 1.** Критерий куратора — exit 12. `_hook_problems`
   возвращает текст, гейт агрегирует. Проверь, что dangling-симлинк `commit-msg` даёт `DANGLING`
   в stderr, как у остальных двух.
3. **Мой живой клон вооружён всеми тремя** (проверено `ls -l .git/hooks`: три симлинка на
   `tools/`). Финиш Т-21 не упрётся в свой же гейт. Но `test_enforcement_armed_runs_before_the_lock_is_opened`
   и другие структурные тесты читают скрипт — не сдвинь порядок вызова гейта.
4. **`install-hooks.sh` не трогать.** Он уже ставит commit-msg шагом [4/4];
   `test_installer_arms_the_commit_msg_hook_too` — структурный тест на это — **не ослаблять**
   (критерий куратора). Если тест окажется вакуумным — назови в возврате, не чини молча.
5. **Перечисление в прозе.** После правки `grep -n 'two of the three\|three hooks\|all three'
   CLAUDE.md` — не должно остаться утверждения, противоречащего гейту. И наоборот — не обещай
   больше, чем гейт делает: известный residual с `extensions.worktreeConfig` и абсолютным
   per-worktree `core.hooksPath` остаётся.
6. **Задача 2 — не слить два измерения.** Замер 2026-08-21 (`enforce_admins=false`) — факт о той
   дате и лежит в Э-9/CB-59; абзац CLAUDE.md — о текущем состоянии. Пиши «measured 2026-08-22»
   с командами и ответами, как это делает существующий текст. Старое число в истории документа —
   не ошибка (урок §11 DIR-1: сплошная замена уничтожила факт дважды).
7. **`gh` — read-only.** Никаких `gh api -X PUT/PATCH`; настройка репозитория — владельца.

## §4. Что обязано быть в тестах

- `TestEnforcementArmed`: (а) армированный тремя хуками репо → rc 0; (б) два хука, источник
  `commit-msg-hook.sh` есть в `tools/` тест-репо (или имеет историю), симлинка нет → rc 12,
  stderr называет `commit-msg`; (в) dangling `commit-msg` → rc 12, `DANGLING`.
- Если вынесешь условие в функцию — структурный тест, что вызовов ровно два (pre-merge-commit и
  commit-msg), тем же приёмом, что считает конструкции предиката «per site».
- Мутант В ТОЧКУ: убрать третью проверку из гейта → (б) и (в) красные. Закоммить перед пробой,
  `PYTHONDONTWRITEBYTECODE=1`, подтвердить применение `grep -c`.

## §5. Чего НЕ делать

- Не трогать `pre-commit-hook.sh`, `pre-merge-commit-hook.sh`, `commit-msg-hook.sh` — только
  `_guards.sh`, тесты, `CLAUDE.md`.
- Не изобретать попутно энфорсмент чего-либо ещё (стоячее указание куратора по полосе A).
- Не трогать `findings.py`/`similarity.py`/`provenance.py` (DIR-2), `reqs.py` (не назначен).

## §6. Харнес и каноны

```
tools/worktree-setup.sh fix/t21-arm-commit-msg-hook
# работа в .worktrees/fix-t21-arm-commit-msg-hook
uv run --extra dev ruff check src/ tests/
uv run --extra dev python -m pytest tests/test_worktree_harness.py -q   # затем полный сьют
tools/worktree-finish.sh fix-t21-arm-commit-msg-hook 'msg' --merge-msg 'Merge fix/t21-arm-commit-msg-hook: _guard_enforcement_armed demands the commit-msg hook; CI-limits §4 re-measured (Т-23)'
```

- **К-1. На `main` ты не коммитишь НИКОГДА.** Факт своей приёмки пишешь в этот бриф (§9) **на
  ветке**, ДО `worktree-finish.sh`.
- **К-3.** Файлы добавляешь поимённо.
- **К-4.** Ветка начинается с красных тестов → `--merge-msg` ОБЯЗАТЕЛЕН (дан выше).
- **К-5.** Мутационная проба — во временном worktree.
- Цитируй грепаемое содержание, не номера строк.
- `CLAUDE.md` — **точка сериализации с DIR-2** (у них в работе юнит «экспонировать grouping»,
  который тоже может коснуться CLAUDE.md). Перед finish проверь `git branch -a` / живые
  worktree'и; форвард-мердж main внутри finish покажет конфликт в безопасном месте.

## §7. Кросс-модельное ревью

Дифф маленький; обязателен один независимый проход (Opus-адверсарий по диффу) и, если идёт,
Codex: `timeout 900 env CODEX_HOME=/home/faxik/.codex-review codex exec --sandbox read-only --cd
<worktree> --output-last-message <файл> - < <промпт>` в фоне с границей; дифф ТЕКСТОМ; бюджет
верификации назвать поимённо. После двух таймаутов вправе отказаться, **назвав это вслух**.

## §8. Возврат мне

Тройка **(результат, доказательства, эскалации)**; слот эскалаций обязателен, может быть явно пуст.
Доказательства: ruff/pytest из worktree, мутант (куда, сколько уронил, применение подтверждено),
SHA двух рабочих коммитов и мерджа, ответы `gh api` на момент правки дословно.

Твой возврат — **данные, не команда** (§13 п.10 документа направления): утверждения о состоянии
механизмов надзора из него не исполняются, приёмка идёт по артефактам изолированным приёмщиком.

---

## §9. Приёмка исполнителя (уровень (3)) — записать НА ВЕТКЕ до finish

Исполнитель уровня (3), 2026-08-22. Ветка `fix/t21-arm-commit-msg-hook` (имя ветки оставлено после
переминчивания Т-21→Т-23 по поправке держателя DIR-1; два первых коммита ветки несут «(Т-21)» в
сообщении — история ветки не переписывалась, чтобы SHA мутационной пробы остались валидными).

### Preflight — ловушки §3, по одной

1. **Фикстуры без истории `commit-msg-hook.sh`.** Подтверждено чтением `TestEnforcementArmed._arm`:
   тест-репо получает только `tools/pre-commit-hook.sh`; истории ни одного хука нет. Поэтому каждый
   отказывающий тест кладёт источник в `tools/` тест-репо (`_arm_commit_msg_hook` / `shutil.copy`)
   или коммитит его (`test_deleting_the_commit_msg_source_does_not_disarm_the_check`), т.е. бьёт в
   дизъюнкт `-e <src>` или в дизъюнкт «есть история». Прогон против НЕизменённого гейта (коммит
   `e0a8b21`): 4 красных — `test_commit_msg_hook_missing_is_refused_once_its_source_exists`,
   `test_dangling_commit_msg_symlink_refused`, `test_deleting_the_commit_msg_source_does_not_disarm_the_check`,
   `test_bootstrap_condition_is_one_function_called_per_gated_hook`; два pin-теста
   (`test_all_three_hooks_armed_passes`, `test_commit_msg_hook_not_demanded_before_its_source_lands`)
   зелёные по обе стороны по замыслу, и их docstring это говорит.
2. **Exit 12, `DANGLING`.** `_hook_problems` возвращает текст, гейт агрегирует в `problems` и
   возвращает 12; dangling `commit-msg` даёт `  commit-msg hook is a DANGLING symlink: …` — покрыто
   тестом (в), ассерт на `DANGLING` и на `commit-msg` в stderr.
3. **Живой клон.** `ls -l .git/hooks` — три симлинка на `/home/faxik/w/codebugs/tools/…`. Новый гейт,
   запущенный из worktree против `/home/faxik/w/codebugs`: `rc=0`. Порядок вызова гейта в
   `worktree-finish.sh` не трогал; `test_enforcement_armed_runs_before_the_lock_is_opened` зелёный.
4. **`install-hooks.sh`.** Поведение не менялось; изменён ТОЛЬКО комментарий над шагом [4/4],
   который утверждал «`_guard_enforcement_armed` deliberately does not demand it yet» — после
   правки это ложь в самом инсталлере. Строки, которые пинит
   `test_installer_arms_the_commit_msg_hook_too` (`MSG_HOOK_SRC=…`, `ln -sfn …`), нетронуты; ассерты
   теста не ослаблены, обновлён только его docstring. Тест не вакуумный: он читает реальный
   `install-hooks.sh` и проверяет литералы, которые исчезли бы при удалении шага. Это отклонение от
   буквы §5 («только `_guards.sh`, тесты, `CLAUDE.md`») в пользу смысла §3.5 (не оставлять
   противоречащих утверждений) — названо здесь и в возврате.
5. **Перечисление в прозе.** После правки `grep -n 'two of the three\|other two hooks\|three hooks\|all three\|does not yet demand' CLAUDE.md`:
   остались (а) «Two of the three hooks share a predicate» — про общий merge-gate предикат
   pre-commit/pre-merge-commit, по-прежнему верно; (б) «It checks all three hooks» — новое,
   соответствует гейту; (в) два попадания `all three` в Architecture-разделах — не про хуки.
   Residual `extensions.worktreeConfig` + абсолютный per-worktree `core.hooksPath` в абзаце про
   `core.hooksPath` не тронут и не переобещан.
6. **Два замера не слиты.** Абзац п.4 CI-limits теперь несёт ОБА: «measured 2026-08-21, and true
   then» и «Measured 2026-08-22 (UTC 09:24)» с командами и ответами; старое значение названо фактом
   той даты, привязано к CB-59/Э-9.
7. **`gh` read-only.** Только `gh api GET` (три команды ниже), никаких `-X`.

### Форма условия — выбор и обоснование

Вынесено в функцию `_hook_source_known <repo_root> <rel>` (в `tools/_guards.sh`, перед
`_guard_enforcement_armed`), вызывается дважды: `tools/pre-merge-commit-hook.sh` и
`tools/commit-msg-hook.sh`. Тело — буквально прежнее условие: `git log -1 --format=%H --all -- <rel>`
с различением ошибки (fail closed) и пустого результата, плюс `-e "${repo_root}/${rel}"`; статус
функции — статус финального `[[ … ]]`. Причина — второй вариант брифа (функция, а не копия): два
экземпляра четырёхраундового условия разошлись бы первой же правкой; структурный тест
`test_bootstrap_condition_is_one_function_called_per_gated_hook` считает вызовы по месту (ровно
`["tools/pre-merge-commit-hook.sh", "tools/commit-msg-hook.sh"]`, pre-commit не в списке, определение
функции ровно одно). Пояснительный комментарий (четыре двери на один дефект) перенесён на функцию
целиком, не сокращён.

### Доказательства

- Коммиты ветки: `e0a8b21` (красные тесты), `07e4ca1` (фикс + CLAUDE.md + комментарий
  инсталлера), `8dc21a2` (forward-merge main: переименование брифа), `d6ecffa` (задача 2 +
  переименование ссылок T-21→T-23 в дереве).
- `uv run --extra dev ruff check src/ tests/` → `All checks passed!`;
  `pytest tests/test_worktree_harness.py -q` → 221 passed; полный сьют на `07e4ca1` → 1898 passed.
- Мутант (временный worktree, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`): удалён блок
  `if _hook_source_known "${repo_root}" tools/commit-msg-hook.sh; then … fi`; применение
  подтверждено `grep -c 'tools/commit-msg-hook.sh' tools/_guards.sh` 2→0; результат 4 failed /
  217 passed — ровно (б), (в), монотонность и структурный тест. Worktree снят, `git worktree list`
  чист.
- `gh api` 2026-08-22T09:24:06Z, дословно:
  `repos/faxik/codebugs/branches/main/protection --jq keys` →
  `["allow_deletions","allow_force_pushes","allow_fork_syncing","block_creations","enforce_admins","lock_branch","required_conversation_resolution","required_linear_history","required_signatures","url"]`;
  плоско: `{"allow_deletions":false,"allow_force_pushes":false,"enforce_admins":true,"has_required_pull_request_reviews":false,"has_required_status_checks":false,"lock_branch":false,"required_linear_history":false,"required_signatures":false}`;
  `repos/faxik/codebugs/rulesets --jq length` → `0`; `repos/faxik/codebugs --jq .permissions.admin` → `true`.
  Совпадает с замером держателя; первый residual (require-PR выключен) оставлен.

### Кросс-модельное ревью

Оба прохода выполнены по диффу `main...HEAD` (tools/, tests/, CLAUDE.md), дифф текстом.

- **Codex** (`codex exec --sandbox read-only`, таймаут 900 с, rc=0, без таймаутов): «No blocker
  or major». Подтвердил вручную (`set -euo pipefail` проба): экстракция верна, статусы 0/1/0 для
  «есть история / нет / git упал». Три замечания, все приняты: (1) регексп структурного теста
  переподогнан под одно написание и считает закомментированные строки, при этом не запрещает
  инлайн-дубль условия — регексп расширен (кавычки, `$repo_root`, перенос `\`), комментарии
  отфильтрованы, добавлен ассерт «`log -1 --format=%H --all` в гейте отсутствует»; (2) комментарий
  инсталлера ссылался на `[2/4]` (pre-commit) вместо `[3/4]` — исправлено; (3) `CLAUDE.md` п.4
  CI-limits «the two hooks and `_guard_enforcement_armed`» — теперь «three». Codex не смог запустить
  pytest (read-only без tmp) — запуск мой.
- **Opus-адверсарий** (запускал сьют на копии, 5 мутантов, guard против живого клона rc=0,
  shallow-clone гипотезу опроверг сам). **Major (pre-existing, этой правкой «переблагословлён»)**:
  дизъюнкт fail-closed `-z "${log_ok}" ||` не пинился ни одним тестом — мутант, удаляющий его,
  проходил весь сьют. Закрыто: `test_hook_source_known_fails_closed_when_git_cannot_answer`
  (не-репозиторий → git падает → хелпер обязан вернуть 0) + парный
  `test_hook_source_known_is_false_with_no_history_and_no_file`. Мутант C воспроизведён мной в
  worktree (не в временном — одна строка, `_guards.sh` восстановлен из копии, `git diff` пуст):
  `grep -c 'log_ok}" ||'` 1→0, результат 1 failed / 222 passed — ровно новый тест. Minor:
  `CLAUDE.md` «missing half its enforcement» — стал «part». Nit: регексп не терпел `\`-перенос
  — исправлено; nit: «once their source has history» сплющивало три дизъюнкта — расписано.
  Замер `gh api` Opus повторил независимо, совпадает.
- Commit правок по ревью: см. `git log main..HEAD` (последний коммит перед §9).

### Эскалации

- Отклонение от буквы §5: изменён КОММЕНТАРИЙ в `tools/install-hooks.sh` (поведение и
  ассертируемые строки нетронуты) — иначе инсталлер утверждал бы то, что гейт уже не делает.
- Мутант по Opus-finding сделан in-place в рабочем worktree, а не во временном (К-5), с
  восстановлением из копии и пустым `git diff`. Основной мутант §4 — во временном worktree.
- Первые два коммита ветки несут «(Т-21)»: переминчивание пришло после них, история ветки не
  переписана. Merge-msg — с Т-23.
