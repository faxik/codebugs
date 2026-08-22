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

*(заполняет исполнитель)*
