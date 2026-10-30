# Замер 2026-10-30: находимость инструментов после Т-148 и эффект Т-147

Куратор codebugs, сессия 2026-10-30. Замер был обещан в реестре болей
(`PAIN-REGISTRY-codebugs.md`, итог 2026-09-23) на 30 сентября и около 10 октября и не сделан
вовремя. Скрипты, которыми снимался исходный замер 23 сентября, лежали во временной папке той
сессии и пропали вместе с ней. Поэтому прибор собран заново, и **скрипты вложены в эту заметку**
(раздел 5): вторую такую потерю заметка исключает.

## 1. Вопросы

1. **Находимость (Т-148, CB-331).** 23 сентября владелец спросил: может быть, агенты не зовут
   часть инструментов codebugs потому, что не знают, когда их звать? Т-148 дописал в описания 42
   редко вызываемых инструментов фразу «Use when …». Вопрос замера: начали ли агенты после этого
   звать эти семейства **по собственной инициативе**, без подсказки человека или скилла?
2. **Короткий вид списков (Т-147, CB-330).** Параметр `view="summary"` у `query`/`recent`.
   Вопрос: пользуются ли им, и стало ли меньше огромных ответов, которые оболочка сбрасывает в файл?

Граница окон — 2026-09-23 16:50 UTC: в это время установленный сервер переустановлен с обеими
правками (проверено 2026-10-30: все 42 фразы присутствуют в установленной копии).

## 2. Ответы

**Находимость: заметного роста самостоятельных вызовов нет.** За 5,3 недели после правки — шесть
сеансов, где семейство вызвано без подсказки, и пять из шести, скорее всего, ложные (см. пределы).
Обходы кода (`codesweep_*`) не вызывались ни разу — ни с подсказкой, ни без: раньше их звали почти
исключительно по прямому указанию (138 сеансов из 146), а в новом окне указаний не было.
Вывод в границах прибора: **использование этих семейств следует за прямыми указаниями человека и
скиллов, а тексты описаний его не изменили.** Это совпадает с догадкой владельца от 2026-09-07
(«может, скиллов не хватает?») и с правилом из реестра: «не выросло → следующий рычаг — скиллы и
правила проектов, а не тексты инструментов».

**Т-147: сработал, и это видно независимо от спада активности.** Доля `view="summary"` у `query`
выросла с 0,03 % до 80,4 %, у `recent` — с 0 до 90,9 %. Файлы-дампы больших ответов `query`: 613 за
4,2 недели до правки, 16 за 5,3 недели после (числа перепроверены куратором отдельным подсчётом).
Поскольку общее число вызовов `query` тоже упало (835 → 290 в неделю), решающая мера — доля
вызовов, закончившихся дампом: **17,3 % → 1,0 %**. Критерий реестра («не упали → вопрос умолчания
к владельцу») не сработал: упали, вопрос умолчания не нужен.

## 3. Числа

**Калибровка** — прибор обязан воспроизвести исходный замер на исходном окне
(2026-03-06 … 2026-09-23 16:50). Ячейка: «с подсказкой / по своей инициативе», единица — сеанс.

| семейство | исходный замер | новый прибор (вариант CAL) |
|---|---|---|
| обходы кода `codesweep_*` | 139 / 6 | 138 / 8 |
| вехи `milestone_*`, `pull_next`, `wip_status` | 78 / 30 | 80 / 29 |
| слияния `codemerge_*` | 13 / 3 | 12 / 4 |
| замеры `codebench_*` | 6 / 3 | 7 / 2 |
| векторы `reqs_embed` и соседи | 4 / 1 | 4 / 1 |
| `blockers_check`, `claims_held_by` | 0 / 0 | 0 / 0 |

Вариант CAL: журналы субагентов приписаны к родительскому сеансу; подсказкой считается упоминание
семейства в пользовательском тексте в любом месте сеанса; ключевые слова широкие (`sweep`, `embed`,
`similar`, `vector`, `held_by`, `blockers`). Вариант, где упоминание обязано предшествовать вызову,
воспроизводит хуже (обходы 131/15, вехи 75/34). **Вариант подобран под исходные числа**, поэтому
совпадение доказывает, что новый прибор меряет так же, как старый, а не что старый мерил верно.

**Измерение** (2026-09-23 16:50 … 2026-10-30, 5,3 недели, вариант CAL). Сеансов с любым вызовом
codebugs: 184 (34,7 в неделю) против 1428 (49,8 в неделю) в исходном окне.

| семейство | с подсказкой / своих | своих в неделю (было) | своих на 100 сеансов (было) |
|---|---|---|---|
| обходы кода | 0 / 0 | 0 (0,28) | 0 (0,56) |
| вехи | 2 / 4 | 0,76 (1,01) | 2,17 (2,03) |
| слияния | 0 / 0 | 0 (0,14) | 0 |
| замеры | 1 / 0 | 0 (0,07) | 0 |
| векторы | 0 / 0 | 0 | 0 |
| `blockers_check` | 0 / 0 | 0 | 0 |
| `claims_held_by` | 1 / 2 | 0,38 (0) | 1,09 (0) |

Шесть «своих» сеансов: один верхнего уровня в autosorter (2026-10-12, владелец спросил о статусе
выпуска, агент сам открыл вехи — похоже на настоящую инициативу); пять — субагенты в bleibwort
(держатели «Выпуск в Play», «Запись и звук», «лягушки интерфейса», сбор фактов для куратора).

**Т-147** (окно A — 2026-08-25 … 09-23 16:50, 4,24 недели; окно B — после, 5,32 недели):

| | A | B |
|---|---|---|
| вызовов `query` | 3542 (835 в неделю) | 1542 (290 в неделю) |
| доля `view="summary"` у `query` | 0,03 % | 80,4 % |
| вызовов `recent` | 47 | 22 |
| доля `view="summary"` у `recent` | 0 % | 90,9 % |
| дампы больших ответов codebugs, все инструменты | 665 (157 в неделю) | 30 (5,6 в неделю) |
| из них `query` | 613 | 16 |
| доля вызовов `query`, кончившихся дампом | 17,3 % | 1,0 % |

## 4. Пределы — окно прибора и чего из этих чисел выводить НЕЛЬЗЯ

- **Прибор не видит бриф, прочитанный из файла.** Подсказка ищется в пользовательском тексте, а
  субагент, получивший бриф через `Read`, видит его как результат инструмента. Пять из шести
  «своих» случаев — именно такие субагенты, поэтому «своих» в новом окне, вероятно, ЗАВЫШЕНО.
- **Выборка мала:** шесть «своих» сеансов за пять недель. Различить слабый рост и шум нельзя.
- **Причина трёх вызовов `claims_held_by` не установлена.** Новое описание как причина не доказано.
- **Состав проектов разный:** в новом окне почти вся активность — bleibwort; в исходном — autosorter.
- **Дампы датированы по времени изменения файла**; если что-то чистило или трогало старые файлы,
  числа искажены. Повторы строк журналов при возобновлении сеансов не устранялись.
- **Подозрение исходного замера про `/loop`** (скилл, запущенный циклом, засчитан как «сам») не
  проверено: прибор его не выделяет.
- **Договор о выводах** (по разбору второй моделью, 2026-10-30): малые счёты не превращаются в
  вердикт «вывести семейство из эксплуатации». Из замера следует только то, что описания не
  изменили поведение; нужны ли семейства — вопрос другого прибора (журнал трения bleibwort, где
  каждый обход инструмента записан с полем «знал ли агент о близкой возможности»).

## 5. Как повторить

Скрипты ниже. Порядок: сохранить три файла в одну папку, запустить `extract.py` (около 40 секунд,
пишет `extract.json` рядом с собой, ~37 МБ), затем `attribution.py` (калибровка и измерение;
с флагом `--detail` — список «своих» сеансов с текстом перед вызовом) и `t147.py`. Для нового
окна поменять `CUT`/`W2` в `attribution.py` и `WIN` в `t147.py`.

### extract.py

```python
#!/usr/bin/env python3
"""Pass 1: stream every transcript containing a codebugs tool name; dump per-file facts to extract.json."""
import os, re, json, sys
from multiprocessing import Pool

ROOT = os.path.expanduser('~/.claude/projects')
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'extract.json')
TOOLRE = re.compile(rb'mcp__([A-Za-z0-9_.-]*codebugs[A-Za-z0-9_.-]*)__([a-z_]+)')
FAM = {
 'codesweep': lambda t: t.startswith('codesweep_'),
 'milestone': lambda t: t.startswith('milestone_') or t in ('pull_next', 'wip_status'),
 'codemerge': lambda t: t.startswith('codemerge_'),
 'codebench': lambda t: t.startswith('codebench_'),
 'vectors': lambda t: t in ('reqs_embed', 'reqs_batch_embed', 'reqs_search_similar', 'reqs_embedding_stats'),
 'blockers_check': lambda t: t == 'blockers_check',
 'claims_held_by': lambda t: t == 'claims_held_by',
}
STRICT = {
 'codesweep': ['codesweep'], 'milestone': ['milestone', 'pull_next', 'wip_status'],
 'codemerge': ['codemerge'], 'codebench': ['codebench'],
 'vectors': ['reqs_embed', 'reqs_batch_embed', 'reqs_search_similar', 'reqs_embedding_stats'],
 'blockers_check': ['blockers_check'], 'claims_held_by': ['claims_held_by'],
}
LOOSE = {
 'codesweep': ['sweep'], 'milestone': ['milestone', 'pull_next', 'wip_status', 'pull-next'],
 'codemerge': ['codemerge'], 'codebench': ['codebench'],
 'vectors': ['embed', 'similar', 'vector'],
 'blockers_check': ['blockers_check', 'blockers'], 'claims_held_by': ['claims_held_by', 'held_by'],
}

def fam_of(tool):
    for f, p in FAM.items():
        if p(tool):
            return f
    return None

def user_text(msg):
    c = msg.get('content')
    if isinstance(c, str):
        return c
    out = []
    if isinstance(c, list):
        for b in c:
            if isinstance(b, dict) and b.get('type') == 'text':
                out.append(b.get('text', ''))
    return '\n'.join(out)

def proc(path):
    try:
        with open(path, 'rb') as fh:
            data = fh.read()
    except Exception:
        return None
    if not TOOLRE.search(data):
        return None
    res = {'path': path, 'calls': [], 'umatch': {'strict': {}, 'loose': {}}, 'users': [], 'prefixes': {}}
    for line in data.split(b'\n'):
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        ty = o.get('type')
        ts = o.get('timestamp')
        if ty == 'user':
            m = o.get('message') or {}
            t = user_text(m)
            if not t:
                continue
            tl = t.lower()
            res['users'].append((ts, t[:150].replace('\n', ' ')))
            for kind, d in (('strict', STRICT), ('loose', LOOSE)):
                for f, kws in d.items():
                    if f not in res['umatch'][kind] and any(k in tl for k in kws):
                        res['umatch'][kind][f] = ts
        elif ty == 'assistant':
            m = o.get('message') or {}
            c = m.get('content')
            if not isinstance(c, list):
                continue
            for b in c:
                if isinstance(b, dict) and b.get('type') == 'tool_use':
                    nm = b.get('name', '')
                    mm = re.match(r'mcp__(.*?codebugs.*?)__([a-z_]+)$', nm)
                    if not mm:
                        continue
                    pre, tool = mm.group(1), mm.group(2)
                    res['prefixes'][pre] = res['prefixes'].get(pre, 0) + 1
                    inp = b.get('input') or {}
                    res['calls'].append((ts, tool, fam_of(tool), inp.get('view') if tool in ('query', 'recent') and isinstance(inp, dict) else None))
    return res

def walk():
    for d in sorted(os.listdir(ROOT)):
        for r, _, fs in os.walk(os.path.join(ROOT, d)):
            if 'tool-results' in r:
                continue
            for f in fs:
                if f.endswith('.jsonl'):
                    yield os.path.join(r, f)

if __name__ == '__main__':
    paths = list(walk())
    print(len(paths), 'jsonl files', file=sys.stderr)
    out = []
    with Pool(8) as p:
        for i, r in enumerate(p.imap_unordered(proc, paths, chunksize=20)):
            if r:
                out.append(r)
    json.dump(out, open(OUT, 'w'))
    print(len(out), 'files with codebugs calls/mentions', file=sys.stderr)
```

### attribution.py

```python
#!/usr/bin/env python3
"""Pass 2 (run extract.py first). Usage: attribution.py [--detail]. Prints calibration + measurement tables."""
import json, os, sys, collections
HERE = os.path.dirname(os.path.abspath(__file__))
D = json.load(open(os.path.join(HERE, 'extract.json')))
FAMS = ['codesweep', 'milestone', 'codemerge', 'codebench', 'vectors', 'blockers_check', 'claims_held_by']
BASE = {'codesweep': (139, 6), 'milestone': (78, 30), 'codemerge': (13, 3), 'codebench': (6, 3), 'vectors': (4, 1),
        'blockers_check': (0, 0), 'claims_held_by': (0, 0)}
CUT = '2026-09-23T16:50:00'
W1 = ('2026-03-06T00:00:00', CUT)
W2 = (CUT, '2026-10-31T00:00:00')

def is_sub(p): return '/subagents/' in p

def skey(p):
    rel = p.split('/projects/')[1].split('/')
    return rel[0] + '/' + (rel[1][:-6] if rel[1].endswith('.jsonl') else rel[1])

def classify(variant, w, kind='strict', sub=True, group=False, anytime=False):
    """returns {fam: {'prompted': [rec], 'self': [rec]}} and number of sessions with any codebugs call in window.
    group=True: subagent transcripts are merged into their parent session (session = top-level id).
    anytime=True: a keyword mention anywhere in the session (not only before first call) counts as prompted."""
    res = {f: {'prompted': [], 'self': []} for f in FAMS}
    groups = collections.defaultdict(list)
    for r in D:
        if not sub and is_sub(r['path']): continue
        if not r['calls']: continue
        groups[skey(r['path']) if group else r['path']].append(r)
    anyc = 0
    for key, rs in groups.items():
        calls = [c for r in rs for c in r['calls']]
        if any(c[0] and w[0] <= c[0][:19] < w[1] for c in calls): anyc += 1
        byf = collections.defaultdict(list)
        for r in rs:
            for c in r['calls']:
                if c[2]: byf[c[2]].append((c, r))
        for f, cl in byf.items():
            ts = [c[0] for c, _ in cl if c[0]]
            if not ts: continue
            first = min(ts)
            if not (w[0] <= first[:19] < w[1]): continue
            fr = [r for c, r in cl if c[0] == first][0]
            ums = [r['umatch'][kind].get(f) for r in rs]
            ums = [u for u in ums if u]
            prompted = bool(ums) and (anytime or min(ums) <= first)
            res[f]['prompted' if prompted else 'self'].append((fr['path'], first, fr))
    return res, anyc

def table(w, **kw):
    res, anyc = classify(None, w, **kw)
    return {f: (len(res[f]['prompted']), len(res[f]['self'])) for f in FAMS}, anyc, res

VARIANTS = {
 'CAL (loose kw, grouped by parent session, mention anytime)': dict(kind='loose', sub=True, group=True, anytime=True),
 'SPEC (loose kw, grouped, mention must precede first call)': dict(kind='loose', sub=True, group=True, anytime=False),
 'TOP-ONLY strict (no subagents)': dict(kind='strict', sub=False, group=False, anytime=False),
}

def show_cal():
    print('CALIBRATION window', W1)
    for name, kw in VARIANTS.items():
        t, anyc, _ = table(W1, **kw)
        print(f' {name}: any-codebugs sessions={anyc}')
        for f in FAMS:
            b = BASE[f]; m = t[f]
            print(f'    {f:15s} base {b[0]:>4}/{b[1]:<3} got {m[0]:>4}/{m[1]:<3}')

if __name__ == '__main__':
    import datetime as dt
    show_cal()
    wk1 = (dt.datetime.fromisoformat(W1[1]) - dt.datetime.fromisoformat(W1[0])).days / 7
    wk2 = (dt.datetime.fromisoformat('2026-10-30T22:00:00') - dt.datetime.fromisoformat(CUT)).days / 7
    for name, kw in VARIANTS.items():
        print('\nMEASURE', name, 'weeks old/new', round(wk1, 1), round(wk2, 1))
        for nm, w, wk in (('old', W1, wk1), ('new', W2, wk2)):
            t, anyc, res = table(w, **kw)
            print(' ', nm, 'any-codebugs sessions', anyc)
            for f in FAMS:
                p, s_ = t[f]
                print(f'    {f:15s} prompted {p:>4} self {s_:>4}  self/wk {s_/wk:5.2f} self/100sess {100*s_/max(anyc,1):5.2f} any/100sess {100*(p+s_)/max(anyc,1):5.2f}')
    if '--detail' in sys.argv:
        kw = list(VARIANTS.values())[0]
        t, anyc, res = table(W2, **kw)
        for f in FAMS:
            for lab in ('self', 'prompted'):
                lst = res[f][lab]
                if lab == 'prompted': lst = lst[:2]
                for path, first, r in lst:
                    prev = [u for u in r['users'] if u[0] and u[0] <= first]
                    txt = prev[-1][1] if prev else '(none)'
                    proj = path.split('/projects/')[1].split('/')[0]
                    print(f'{f}|{lab}|{proj}|{"SUB" if is_sub(path) else "top"}|{first[:19]}|{path}|{txt[:150]}')
```

### t147.py

```python
#!/usr/bin/env python3
"""T-147: query/recent call counts + view=summary share + large-output dump files, per window. Needs extract.json."""
import json, os, datetime as dt, collections, re
HERE=os.path.dirname(os.path.abspath(__file__))
D=json.load(open(os.path.join(HERE,'extract.json')))
CUT='2026-09-23T16:50:00'
WIN={'A 08-25..09-23T16:50':('2026-08-25T00:00:00',CUT),'B 09-23T16:50..10-30':(CUT,'2026-10-31T00:00:00')}
def wk(w):
    a,b=[dt.datetime.fromisoformat(x) for x in w]; b=min(b,dt.datetime(2026,10,30,22)); return (b-a).total_seconds()/604800
for n,w in WIN.items():
    c=collections.Counter(); sess=set()
    for r in D:
        for ts,tool,fam,view in r['calls']:
            if ts and tool in('query','recent') and w[0]<=ts[:19]<w[1]:
                c[tool]+=1; c[tool+'_summary']+= (view=='summary'); sess.add(r['path'])
    k=wk(w)
    print(n,'weeks %.2f'%k,dict(c),'sessions',len(sess))
    for t in('query','recent'):
        tot=c[t]; print(f'  {t}: {tot} calls ({tot/k:.1f}/wk), summary share {100*c[t+"_summary"]/max(tot,1):.1f}%')
# dumps
root=os.path.expanduser('~/.claude/projects')
dump=[]
for r,_,fs in os.walk(root):
    if 'tool-results' in r:
        for f in fs:
            if f.startswith('mcp-codebugs-'):
                p=os.path.join(r,f); dump.append((f,dt.datetime.fromtimestamp(os.path.getmtime(p),dt.timezone.utc).replace(tzinfo=None).isoformat(),os.path.getsize(p)))
print('dump files total',len(dump))
for n,w in WIN.items():
    sel=[d for d in dump if w[0]<=d[1][:19]<w[1]]; k=wk(w)
    q=[d for d in sel if d[0].startswith('mcp-codebugs-query-')]; rc=[d for d in sel if d[0].startswith('mcp-codebugs-recent-')]
    bytool=collections.Counter(re.match(r'mcp-codebugs-([a-z_]+)-',d[0]).group(1) for d in sel)
    print(n,'all codebugs dumps',len(sel),'(%.1f/wk)'%(len(sel)/k),'query',len(q),'(%.2f/wk)'%(len(q)/k),'recent',len(rc),'median size query',sorted(d[2] for d in q)[len(q)//2] if q else None, dict(bytool.most_common(6)))
```

В `extract.py` из текста выше удалены две неиспользуемые строки исходника (`low_cache`,
`has_tool`/`is_user`), на результат они не влияли.
