# Т-131 — ведомость переезда

**Состояние: ПЛАН. Текст ещё не двигался.** После переезда графа «граница»
заполняется предметом: для каждого правила, у которого была названная граница,
показывается, что граница осталась ПРИ правиле, а не уехала отдельно.

База: `CLAUDE.md` = **195 123 байта** (перемерено 2026-08-31 после посадки Т-127).
Единиц — 159, и покрытие файла ими доказано: сумма байтов единиц плюс межстрочные
переводы равна размеру файла точно (`T131-segment.py` падает, если нет).

## Итог

- остаётся в корне — **36149** байт (18.5 %)
- уезжает — **158816** байт (81.5 %)
- из уезжающего собственно милстоунного — **1 646** (1,5 %), решено НЕ выделять
  в отдельный файл: выигрыш три процента ценой четвёртого впрыскиваемого файла,
  и раздел работает ориентацией о существовании подсистемы.

## Построчно

| № | раздел | подраздел | байт | в корень | уезжает | адрес | граница |
|---:|---|---|---:|---:|---:|---|---|
| 0 | (preamble) | — | 10 | 10 | 0 | корень целиком | |
| 1 | (preamble) | — | 93 | 93 | 0 | корень целиком | |
| 2 | Workflow — `main` is never | — | 47 | 47 | 0 | корень целиком | |
| 3 | Workflow — `main` is never | — | 1015 | 355 | 660 | корень + `docs/claude-md-rationale/workflow.md` | |
| 4 | Workflow — `main` is never | — | 1647 | 1647 | 0 | корень целиком | |
| 5 | Workflow — `main` is never | — | 2763 | 967 | 1796 | корень + `docs/claude-md-rationale/workflow.md` | |
| 6 | Workflow — `main` is never | — | 914 | 91 | 823 | корень + `docs/claude-md-rationale/workflow.md` | |
| 7 | Workflow — `main` is never | — | 315 | 284 | 32 | корень + `docs/claude-md-rationale/workflow.md` | |
| 8 | Workflow — `main` is never | — | 845 | 59 | 786 | корень + `docs/claude-md-rationale/workflow.md` | |
| 9 | Workflow — `main` is never | — | 2990 | 748 | 2242 | корень + `docs/claude-md-rationale/workflow.md` | |
| 10 | Workflow — `main` is never | — | 170 | 94 | 76 | корень + `docs/claude-md-rationale/workflow.md` | |
| 11 | Workflow — `main` is never | — | 354 | 177 | 177 | корень + `docs/claude-md-rationale/workflow.md` | |
| 12 | Workflow — `main` is never | — | 357 | 125 | 232 | корень + `docs/claude-md-rationale/workflow.md` | |
| 13 | Workflow — `main` is never | — | 146 | 102 | 44 | корень + `docs/claude-md-rationale/workflow.md` | |
| 14 | Workflow — `main` is never | — | 16848 | 4212 | 12636 | корень + `docs/claude-md-rationale/workflow.md` | |
| 15 | Workflow — `main` is never | — | 248 | 99 | 149 | корень + `docs/claude-md-rationale/workflow.md` | |
| 16 | Workflow — `main` is never | — | 602 | 150 | 452 | корень + `docs/claude-md-rationale/workflow.md` | |
| 17 | Workflow — `main` is never | — | 335 | 100 | 234 | корень + `docs/claude-md-rationale/workflow.md` | |
| 18 | Workflow — `main` is never | — | 1932 | 773 | 1159 | корень + `docs/claude-md-rationale/workflow.md` | |
| 19 | Workflow — `main` is never | — | 685 | 171 | 514 | корень + `docs/claude-md-rationale/workflow.md` | |
| 20 | Workflow — `main` is never | — | 9331 | 3079 | 6252 | корень + `docs/claude-md-rationale/workflow.md` | |
| 21 | Workflow — `main` is never | — | 2796 | 1817 | 979 | корень + `docs/claude-md-rationale/workflow.md` | |
| 22 | Workflow — `main` is never | — | 274 | 247 | 27 | корень + `docs/claude-md-rationale/workflow.md` | |
| 23 | Workflow — `main` is never | — | 303 | 303 | 0 | корень целиком | |
| 24 | Workflow — `main` is never | — | 1054 | 949 | 105 | корень + `docs/claude-md-rationale/workflow.md` | |
| 25 | Workflow — `main` is never | — | 619 | 588 | 31 | корень + `docs/claude-md-rationale/workflow.md` | |
| 26 | Workflow — `main` is never | — | 2434 | 974 | 1460 | корень + `docs/claude-md-rationale/workflow.md` | |
| 27 | Workflow — `main` is never | — | 905 | 543 | 362 | корень + `docs/claude-md-rationale/workflow.md` | |
| 28 | Workflow — `main` is never | — | 231 | 208 | 23 | корень + `docs/claude-md-rationale/workflow.md` | |
| 29 | Workflow — `main` is never | — | 189 | 189 | 0 | корень целиком | |
| 30 | Workflow — `main` is never | — | 6366 | 1592 | 4774 | корень + `docs/claude-md-rationale/workflow.md` | |
| 31 | Releasing | — | 13 | 13 | 0 | корень целиком | |
| 32 | Releasing | — | 185 | 185 | 0 | корень целиком | |
| 33 | Releasing | — | 189 | 189 | 0 | корень целиком | |
| 34 | Releasing | — | 182 | 182 | 0 | корень целиком | |
| 35 | Architecture | — | 16 | 16 | 0 | корень целиком | |
| 36 | Architecture | — | 268 | 268 | 0 | корень целиком | |
| 37 | Architecture | — | 171 | 171 | 0 | корень целиком | |
| 38 | Architecture | — | 250 | 250 | 0 | корень целиком | |
| 39 | Architecture | — | 576 | 432 | 144 | корень + `docs/claude-md-rationale/workflow.md` | |
| 40 | Architecture | — | 171 | 171 | 0 | корень целиком | |
| 41 | Architecture | — | 258 | 258 | 0 | корень целиком | |
| 42 | Architecture | — | 121 | 121 | 0 | корень целиком | |
| 43 | Architecture | Known architectura | 29 | 12 | 17 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 44 | Architecture | Known architectura | 502 | 201 | 301 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 45 | Architecture | Known architectura | 322 | 97 | 225 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 46 | Architecture | Known architectura | 273 | 27 | 246 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 47 | Architecture | Known architectura | 469 | 70 | 399 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 48 | Architecture | Known architectura | 352 | 53 | 299 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 49 | Architecture | Known architectura | 475 | 48 | 428 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 50 | Architecture | Known architectura | 2469 | 123 | 2346 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 51 | Architecture | Known architectura | 631 | 63 | 568 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 52 | Architecture | Known architectura | 2513 | 126 | 2387 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 53 | Architecture | Known architectura | 2579 | 129 | 2450 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 54 | Architecture | Known architectura | 604 | 0 | 604 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 55 | Code rules | — | 14 | 14 | 0 | корень целиком | |
| 56 | Code rules | Module structure | 20 | 6 | 14 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 57 | Code rules | Module structure | 132 | 33 | 99 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 58 | Code rules | Module structure | 142 | 36 | 106 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 59 | Code rules | Module structure | 142 | 36 | 106 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 60 | Code rules | Naming and style | 20 | 6 | 14 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 61 | Code rules | Naming and style | 61 | 52 | 9 | корень + `docs/claude-md-rationale/workflow.md` | |
| 62 | Code rules | Naming and style | 49 | 44 | 5 | корень + `docs/claude-md-rationale/workflow.md` | |
| 63 | Code rules | Naming and style | 83 | 17 | 66 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 64 | Code rules | Naming and style | 166 | 25 | 141 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 65 | Code rules | Naming and style | 128 | 19 | 109 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 66 | Code rules | Database | 12 | 4 | 8 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 67 | Code rules | Database | 464 | 139 | 325 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 68 | Code rules | Database | 1471 | 118 | 1353 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 69 | Code rules | Database | 1252 | 188 | 1064 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 70 | Code rules | Database | 765 | 61 | 704 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 71 | Code rules | Database | 799 | 64 | 735 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 72 | Code rules | Database | 6209 | 186 | 6023 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 73 | Code rules | Database | 3875 | 194 | 3681 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 74 | Code rules | Database | 1763 | 88 | 1675 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 75 | Code rules | Database | 3143 | 157 | 2986 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 76 | Code rules | Database | 125 | 38 | 88 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 77 | Code rules | Database | 114 | 40 | 74 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 78 | Code rules | Database | 75 | 64 | 11 | корень + `docs/claude-md-rationale/workflow.md` | |
| 79 | Code rules | Database | 296 | 44 | 252 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 80 | Code rules | Database | 692 | 173 | 519 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 81 | Code rules | Database | 991 | 59 | 932 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 82 | Code rules | Database | 811 | 41 | 770 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 83 | Code rules | Database | 1583 | 111 | 1472 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 84 | Code rules | Database | 968 | 48 | 920 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 85 | Code rules | Database | 1516 | 106 | 1410 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 86 | Code rules | Database | 2164 | 108 | 2056 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 87 | Code rules | Database | 379 | 38 | 341 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 88 | Code rules | Database | 1270 | 89 | 1181 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 89 | Code rules | Database | 2387 | 143 | 2244 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 90 | Code rules | Database | 732 | 37 | 695 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 91 | Code rules | Database | 1235 | 99 | 1136 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 92 | Code rules | Database | 1443 | 87 | 1356 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 93 | Code rules | Database | 2156 | 129 | 2027 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 94 | Code rules | Database | 2125 | 128 | 1998 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 95 | Code rules | Database | 16051 | 482 | 15569 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 96 | Code rules | Database | 2315 | 278 | 2037 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 97 | Code rules | Database | 517 | 52 | 465 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 98 | Code rules | Database | 3247 | 97 | 3150 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 99 | Code rules | Database | 348 | 52 | 296 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 100 | Code rules | Database | 7487 | 599 | 6888 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 101 | Code rules | Error handling | 18 | 5 | 13 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 102 | Code rules | Error handling | 92 | 23 | 69 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 103 | Code rules | Error handling | 81 | 16 | 65 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 104 | Code rules | Error handling | 78 | 16 | 62 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 105 | Code rules | Error handling | 1877 | 94 | 1783 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 106 | Code rules | Error handling | 1647 | 82 | 1565 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 107 | Code rules | Error handling | 1956 | 98 | 1858 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 108 | Code rules | Error handling | 153 | 31 | 122 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 109 | Code rules | Testing | 11 | 11 | 0 | корень целиком | |
| 110 | Code rules | Testing | 106 | 32 | 74 | корень + `tests/CLAUDE.md` | |
| 111 | Code rules | Testing | 104 | 10 | 94 | корень + `tests/CLAUDE.md` | |
| 112 | Code rules | Testing | 5189 | 259 | 4930 | корень + `tests/CLAUDE.md` | |
| 113 | Code rules | Testing | 60 | 12 | 48 | корень + `tests/CLAUDE.md` | |
| 114 | Code rules | Testing | 1504 | 75 | 1429 | корень + `tests/CLAUDE.md` | |
| 115 | Code rules | Testing | 48 | 48 | 0 | корень целиком | |
| 116 | Code rules | Testing | 43 | 43 | 0 | корень целиком | |
| 117 | Code rules | Testing | 152 | 61 | 91 | корень + `docs/claude-md-rationale/workflow.md` | |
| 118 | Code rules | MCP tool registrat | 25 | 8 | 18 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 119 | Code rules | MCP tool registrat | 118 | 24 | 94 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 120 | Code rules | MCP tool registrat | 92 | 18 | 74 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 121 | Code rules | MCP tool registrat | 120 | 18 | 102 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 122 | Code rules | MCP tool registrat | 129 | 26 | 103 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 123 | Code rules | MCP tool registrat | 1556 | 78 | 1478 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 124 | Code rules | MCP tool registrat | 840 | 84 | 756 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 125 | Code rules | MCP tool registrat | 1362 | 68 | 1294 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 126 | Code rules | MCP tool registrat | 651 | 163 | 488 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 127 | Code rules | CLI | 7 | 2 | 5 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 128 | Code rules | CLI | 111 | 22 | 89 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 129 | Code rules | CLI | 77 | 15 | 62 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 130 | Code rules | CLI | 120 | 24 | 96 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 131 | Code rules | CLI | 527 | 132 | 395 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 132 | Code rules | CLI | 1631 | 82 | 1549 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 133 | Code rules | CLI | 3944 | 197 | 3747 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 134 | Code rules | CLI | 3171 | 317 | 2854 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 135 | Code rules | CLI | 1184 | 118 | 1066 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 136 | Architecture migration (in | — | 39 | 12 | 27 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 137 | Architecture migration (in | — | 433 | 130 | 303 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 138 | Architecture migration (in | — | 183 | 46 | 137 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 139 | Architecture migration (in | — | 103 | 10 | 93 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 140 | Architecture migration (in | — | 139 | 14 | 125 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 141 | Architecture migration (in | — | 78 | 23 | 55 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 142 | Embeddings | — | 13 | 3 | 10 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 143 | Embeddings | — | 13001 | 910 | 12091 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 144 | Claims module | — | 16 | 5 | 11 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 145 | Claims module | — | 479 | 168 | 311 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 146 | Claims module | — | 501 | 276 | 225 | корень + `docs/claude-md-rationale/workflow.md` | |
| 147 | Claims module | — | 225 | 112 | 112 | корень + `docs/claude-md-rationale/workflow.md` | |
| 148 | Claims module | — | 151 | 23 | 128 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 149 | Claims module | — | 636 | 64 | 572 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 150 | Claims module | — | 286 | 29 | 257 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 151 | Claims module | — | 3263 | 1632 | 1632 | корень + `docs/claude-md-rationale/workflow.md` | |
| 152 | Claims module | — | 2325 | 1395 | 930 | корень + `docs/claude-md-rationale/workflow.md` | |
| 153 | Claims module | — | 248 | 50 | 198 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 154 | Milestones module | — | 20 | 5 | 15 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 155 | Milestones module | — | 291 | 102 | 189 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 156 | Milestones module | — | 191 | 29 | 162 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 157 | Milestones module | — | 562 | 56 | 506 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
| 158 | Milestones module | — | 825 | 124 | 701 | корень + `src/codebugs/CLAUDE.md` (правило) + справка домена (почему) | |
