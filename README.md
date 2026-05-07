# Java Method Generation Experiments

Экспериментальный пайплайн для оценки генерации тела Java-метода через LLM (Qwen 2.5 Coder, FIM mode).

**Гипотеза:** помогает ли аугментация из сигнатур method invocations при генерации, и влияет ли порядок сигнатур на качество? Помогает ли retrieval-based аугментация (поиск похожих методов по проекту)?

**Режимы:**

| Режим | Описание |
|---|---|
| `no_augmentation` | Только замаскированный Java-файл, без подсказок |
| `ordered_augmentation` | + блок с сигнатурами invocations в порядке появления в коде |
| `shuffled_augmentation` | + те же сигнатуры, но в случайном порядке |
| `retrieval_augmentation` | + похожие методы из проекта, найденные Lucene-ретривером |

---

## Требования

- **Java 17+** (для сборки и запуска extractor'а и retriever'а; JDK 25 поддерживается через Gradle 9.4.1)
- **Python 3.11+**
- **Git**

Проверить доступные JDK:

```bash
/usr/libexec/java_home -V
```

---

## Установка и сборка

### 1. Установить Python-зависимости

```bash
pip install -r requirements.txt
```

### 2. Собрать Java-модули

Проект содержит три Gradle-подпроекта:
- **shared** — общие модели данных
- **extractor-core** — парсер Java-проектов через Eclipse JDT (извлечение методов, invocations, типов)
- **retriever** — Lucene-ретривер для поиска похожих методов

```bash
cd extractor
JAVA_HOME=$(/usr/libexec/java_home -v 17) ./gradlew :extractor-core:jar :retriever:shadowJar
cd ..
```

Результат:
- `extractor/extractor-core/build/libs/method-extractor-0.2.0.jar` (~18 MB, fat JAR)
- `extractor/retriever/build/libs/method-retriever-0.2.0.jar` (~11 MB, shadow JAR с Lucene)

Убедиться, что JAR'ы запускаются:

```bash
java -jar extractor/extractor-core/build/libs/method-extractor-0.2.0.jar --help
java -jar extractor/retriever/build/libs/method-retriever-0.2.0.jar --help
```

### 3. Клонировать target-проект

```bash
mkdir -p target-project
git clone --branch v0.8.2 \
  https://github.com/Aelysium-Group/rustyconnector-minecraft.git \
  target-project/rustyconnector-minecraft
```

> Если `v0.8.2` не соберётся, попробовать `v0.9.1`.

> **Внимание:** В v0.8.2 Gradle-проект расположен в подпапке `plugin/`, а не в корне репозитория. Extractor и config.yaml уже указывают на `plugin/`.

### 4. Собрать target-проект

Target-проект нужно скомпилировать **до** запуска экстрактора — JDT-у нужны `.class` файлы для type resolution.

```bash
cd target-project/rustyconnector-minecraft/plugin
chmod +x gradlew
JAVA_HOME=$(/usr/libexec/java_home -v 17) ./gradlew compileJava
cd ../../..
```

> **Важно:** и сборка target-проекта, и запуск экстрактора должны использовать **Java 17**. Системная Java 25 не поддерживается Gradle 8.6, который используется в rustyconnector.

---

## Запуск экстракции

Extractor резолвит classpath через Gradle init script и извлекает методы с полным type resolution через JDT.

**Занимает ~10 секунд** (при уже скомпилированном проекте).

```bash
JAVA_HOME=$(/usr/libexec/java_home -v 17) \
  $(/usr/libexec/java_home -v 17)/bin/java -jar extractor/extractor-core/build/libs/method-extractor-0.2.0.jar \
  --project-path ./target-project/rustyconnector-minecraft/plugin \
  --output ./results/extracted_methods.json \
  --min-statements 3
```

> **Почему Java 17?** Extractor вызывает `./gradlew` target-проекта для classpath resolution. С Java 25 Gradle 8.6 падает (`Unsupported class file major version 69`). С Java 17 резолвится ~100 classpath entries и ~95% invocations получают EXACT resolution.

После завершения: `results/extracted_methods.json` содержит все извлечённые методы с сигнатурами invocations.

Посмотреть статистику:

```bash
python -c "
import json
with open('results/extracted_methods.json') as f:
    d = json.load(f)
m = d['meta']
print(f'Files: {m[\"totalFiles\"]}, Total methods: {m[\"totalMethods\"]}, Extracted: {m[\"extractedMethods\"]}')
print(f'Unresolved invocations: {m[\"unresolvedInvocations\"]}')
"
```

---

## Построение матрицы смежности call-graph'а

По JSON-у экстрактора собирается взвешенная матрица смежности call-graph'а проекта (PyTorch sparse COO).

- **Вершины** — все методы проекта (primary + siblings, дедуплицированные по каноническому `vertex_id`)
- **Рёбра** — направленные invocation'ы метода → метода; вес = количество вызовов в теле
- **Фильтрация** — дропаются только `UNRESOLVED` invocations и EXACT-инвокации во внешние библиотеки (их нет в множестве вершин — «тупики» графа). Self-loops (рекурсия) сохраняются

### Шаги

1. **Собрать JSON без фильтров по категориям** — для графа нужны все методы класса, включая геттеры/сеттеры/делегаторы:

   ```bash
   JAVA_HOME=$(/usr/libexec/java_home -v 17) \
     java -jar extractor/extractor-core/build/libs/method-extractor-0.2.0.jar \
     --project-path ./target-project/<your-project> \
     --output ./results/extracted_methods.json \
     --all-methods \
     --min-statements 0
   ```

   > Флаг `--all-methods` отключает фильтр по `MethodCategory` (по умолчанию остаются только `NORMAL`). `--min-statements 0` оставляет однострочные геттеры. Эти два флага нужны вместе — без них граф будет неполным.

2. **Построить матрицу:**

   ```bash
   python -m pipeline.call_graph_cli ./results/extracted_methods.json \
     -o ./results/call-graph \
     --verbose
   ```

   На выходе — директория с двумя файлами:

   | Файл | Что внутри |
   |---|---|
   | `adjacency.pt` | `torch.sparse_coo_tensor` формы `(N, N)`, `dtype=int64`, после `.coalesce()`. `A[i, j]` = количество инвокаций из метода `i` в метод `j` |
   | `vertices.json` | `{"vertex_ids": [...], "vertex_meta": [...]}`. `vertex_ids[i]` — канонический ID метода `i` формата `<classFqn>::<methodName>(<paramFqn1>,...) -> <returnFqn>` (для конструкторов — `<init>` + returnType = classFqn). `vertex_meta[i]` — полные метаданные вершины (class_fqn, method_name, file_path, parameter_types, return_type, исходный список invocations) |

3. **Использовать в Python:**

   ```python
   from pipeline.call_graph import load_call_graph

   g = load_call_graph("results/call-graph")
   A = g.to_dense()                  # torch.int64, shape (N, N) — веса
   binary = (A > 0).to(torch.int64)  # бинарная матрица смежности
   in_deg = A.sum(dim=0)             # in-degree каждой вершины
   out_deg = A.sum(dim=1)            # out-degree
   # Найти вершину по ID:
   idx = g.vertex_ids.index("com.example.Foo::bar(int) -> void")
   ```

### Пример: JGraphT-Builder

На [JGraphT-Builder](https://github.com/sckwokyboom/JGraphT-Builder) (~100 классов):

- **477 вершин**, **811 уникальных рёбер**, `Σw = 1168` инвокаций, 3 self-loop'а, density ≈ 0.0036
- Время построения матрицы — секунды; размер `adjacency.pt` — десятки КБ

### Валидация и визуализация

После сборки граф можно проверить и осмотреть глазами.

1. **Формальный отчёт** (статистика + 7 sanity-чеков):

   ```bash
   python -m pipeline.call_graph_check ./results/call-graph
   ```

   Печатает summary, sanity-чеки (shape / dtype / sparse / positive weights / in-bounds / meta length / unique IDs), топ-10 по out/in/total degree, распределение весов и степеней (гистограммы), self-loops, изолированные вершины и случайную выборку из 5 методов с их outgoing edges для ручной сверки. Exit-код `0`, если все чеки прошли.

2. **Интерактивный HTML-вьювер** (Google Material UI):

   ```bash
   python -m pipeline.call_graph_viewer ./results/call-graph \
     -o ./results/call_graph_viewer.html \
     --extraction-json ./results/extracted_methods.json
   ```

   Один самодостаточный HTML-файл (открывается без сервера). Вкладки:
   - **Graph** — force-directed граф на Cytoscape.js; цвет узла = пакет, размер = суммарная степень, клик по узлу открывает панель деталей, рёбра инцидентные выделяются синим
   - **Matrix** — канвас-хитмап матрицы смежности; сортировка по index / class / out-deg / in-deg, шкала log / linear / binary, размер ячейки 2–8px; клик по ячейке переходит к соответствующему ребру
   - **Vertices** — таблица всех вершин с поиском и сортировкой по out/in degree

   В панели деталей видны исходящие и входящие рёбра (кликабельны — сразу переход к целевой вершине) и тело метода с подсветкой синтаксиса Java (если передан `--extraction-json`).

### Замечания

- Коллизии `vertex_id` при flattening (например, один и тот же метод встречается и как primary, и как sibling другого метода того же класса) логируются как warning и дедуплицируются по принципу «первый выигрывает» — это нормальное поведение, связанное с форматом JSON, а не баг.
- Если хочется бинарный граф без весов — используйте `(A > 0).float()` на загруженной матрице, специальный режим не нужен.
- Модуль публикует console_scripts (после `pip install -e .`): `mgx-call-graph`, `mgx-call-graph-check`, `mgx-call-graph-viewer` — эквивалентные `python -m pipeline.call_graph_cli` / `call_graph_check` / `call_graph_viewer`.

---

## Инспекция сэмплов (без LLM)

Перед запуском эксперимента можно посмотреть, как выглядят сэмплы: тело метода, найденные сигнатуры, промпты для каждого режима.

```bash
# 3 сэмпла подряд (по умолчанию)
python -m pipeline.inspect --config config.yaml

# Конкретный сэмпл по индексу
python -m pipeline.inspect --config config.yaml --index 5

# Больше сэмплов
python -m pipeline.inspect --config config.yaml --n 10

# Только один режим аугментации
python -m pipeline.inspect --config config.yaml --mode ordered_augmentation

# Показать raw JSON первого сэмпла
python -m pipeline.inspect --config config.yaml --n 1 --json
```

**Что выводит каждый сэмпл:**

```
── METHOD METADATA ──────────────────────────────────────────────
  File:       common/src/main/java/.../SomeClass.java
  Class:      group.aelysium.rustyconnector.common.SomeClass
  Signature:  public void handleConnection(Player player)
  Statements: 7

── GROUND TRUTH BODY ────────────────────────────────────────────
  {
      String id = player.getUuid().toString();
      this.registry.add(id, player);
      ...
  }

── EXTRACTED INVOCATION SIGNATURES (5) ─────────────────────────
  [ 0]  EXACT       java.util.UUID::toString() -> java.lang.String
  [ 1]  EXACT       com.example.Registry::add(java.lang.String, ...) -> void
  [ 2]  UNRESOLVED  UNRESOLVED<process>
  ...

── PROMPT (ordered_augmentation) ────────────────────────────────
  Augmentation block:
    /*
     * Method invocations used in this method:
     * 1. java.util.UUID::toString() -> java.lang.String [EXACT]
     * 2. com.example.Registry::add(...) -> void [EXACT]
     * 3. UNRESOLVED<process> [UNRESOLVED]
     */

  ── prefix (last 20 lines) ──────────────────────────────────────
  ... (код файла до тела метода) ...

  ── <|fim_middle|>  ← model generates here ──────────────────────

  ── suffix (first 20 lines) ──────────────────────────────────────
  ... (код файла после тела метода) ...

  Prompt token estimate: ~1840 tokens
```

---

## Retrieval-augmented генерация

Ретривер ищет по проекту методы, похожие на целевой, и добавляет их как контекст в FIM-промпт. Это позволяет LLM видеть паттерны использования API из реального проекта.

### Как работает

1. **Экстракция** — для каждого метода извлекаются расширенные метаданные: imports, поля класса, supertypes, сигнатуры соседних методов, используемые типы
2. **Индексация** — все методы индексируются в Lucene с полями: `typeProfile` (простые имена типов), `methodCard` (структурная карточка), `invocationProfile` (сигнатуры вызовов с bigrams)
3. **Поиск** — для целевого метода строится query из его типов, сигнатуры, типов полей класса. Используется MultiSimilarity (BM25 + LM Jelinek-Mercer + LM Dirichlet)
4. **Аугментация** — найденные методы вставляются как Java-код перед целевым методом в FIM-промпте
5. **Leakage prevention** — исключается сам метод, методы из того же файла и near-duplicates

### Сборка индекса

```bash
JAVA_HOME=$(/usr/libexec/java_home -v 17) \
  java -jar extractor/retriever/build/libs/method-retriever-0.2.0.jar index \
  --input ./results/extracted_methods.json \
  --index-dir ./results/lucene-index
```

> Индекс автоматически создаётся при запуске пайплайна с `retrieval_augmentation`, если его нет.

### Инспекция retrieval (без LLM)

Визуальная инспекция того, что находит ретривер и как выглядят промпты:

```bash
# HTML-инспектор с полной визуализацией
JAVA_HOME=$(/usr/libexec/java_home -v 17) \
  python -m pipeline.inspect_retrieval --config config.yaml --n 15

# Открыть в браузере
open results/retrieval_inspection.html
```

Инспектор показывает для каждого сэмпла:
- **Full Prompt** — полный FIM-промпт с подсвеченными FIM-токенами
- **Retrieved Methods** — каждый найденный метод с Lucene explain, overlap analysis (Type IoU, Oracle Recall, shared types/owners)
- **Search Query** — Lucene query + все поля search request (imports, field types, sibling owner types)
- **Augmentation Block** — блок как он вставлен в промпт
- **Target Method** — oracle invocations, поля класса, supertypes
- **Compare Prompts** — промпт без аугментации vs с аугментацией

```bash
# Терминальная инспекция (компактнее)
JAVA_HOME=$(/usr/libexec/java_home -v 17) \
  python -m pipeline.inspect --config config.yaml --n 3 --mode retrieval_augmentation
```

### Retrieval metrics

Помимо стандартных метрик генерации, для `retrieval_augmentation` считаются:

| Метрика | Описание |
|---|---|
| **Recall@K** | Доля oracle invocations, чьи owner+method name найдены в аугментации |
| **API Coverage@K** | Доля нужных (ownerType, methodName) пар, покрытых retrieved методами |
| **MRR** | Mean Reciprocal Rank для нахождения метода с высоким token overlap с ground truth |

---

## Запуск эксперимента

### Настройка endpoint'а

В `config.yaml` указать адрес OpenAI-compatible endpoint с моделью Qwen 2.5 Coder:

```yaml
llm:
  endpoint_url: "http://localhost:8080/v1/completions"
  model_name: "qwen2.5-coder-7b"
  temperature: 0.0
  max_tokens: 512
```

Endpoint должен поддерживать `/v1/completions` (не `/v1/chat/completions`) и FIM-токены Qwen 2.5 Coder:
`<|fim_prefix|>`, `<|fim_suffix|>`, `<|fim_middle|>`.

### Полный прогон (все режимы, 100 сэмплов)

```bash
JAVA_HOME=$(/usr/libexec/java_home -v 17) python -m pipeline.run --config config.yaml
```

> `JAVA_HOME` нужен для retrieval (Lucene) и compilability (javac). Если `retrieval_augmentation` не используется, можно опустить.

### Отдельные режимы

```bash
# Baseline + retrieval (основной эксперимент)
JAVA_HOME=$(/usr/libexec/java_home -v 17) \
  python -m pipeline.run --config config.yaml --mode no_augmentation retrieval_augmentation

# Только oracle аугментация
python -m pipeline.run --config config.yaml --mode ordered_augmentation

# Без проверки компилируемости (быстрее)
python -m pipeline.run --config config.yaml --skip-compilability

# Пропустить экстракцию (если уже сделана)
python -m pipeline.run --config config.yaml --skip-extraction
```

---

## Визуализация и анализ

Есть три уровня визуализации — до LLM, после LLM (общий), после LLM (retrieval-ориентированный).

### 1. Инспекция retrieval до запуска LLM (`inspect_retrieval`)

Самый важный инструмент для отладки ретривера. Генерирует HTML **без обращения к LLM** — только экстракция, индексация, поиск, форматирование промптов.

```bash
JAVA_HOME=$(/usr/libexec/java_home -v 17) \
  python -m pipeline.inspect_retrieval --config config.yaml --n 15
open results/retrieval_inspection.html
```

Для каждого сэмпла доступны 6 табов:

| Tab | Что показывает |
|-----|---------------|
| **Full Prompt** | Полный FIM-промпт с подсвеченными `<\|fim_prefix\|>` / `<\|fim_suffix\|>` / `<\|fim_middle\|>` токенами + ground truth |
| **Retrieved Methods** | Каждый retrieved метод раскрывается: **Overlap Analysis** (Type IoU, Owner IoU, Oracle Recall, shared types/owners — цветные тэги), **Lucene Score Explain** (декомпозиция score по термам и полям), Type Profile, Method Card, Invocation Profile, тело метода с подсветкой Java |
| **Search Query** | Lucene query string + все поля search request: imports, field types, sibling owner types, sibling signatures, FIM prefix/suffix (что ушло в query) |
| **Augmentation Block** | Блок Java-кода как он вставлен в промпт + ground truth рядом для сравнения |
| **Target Method** | Полная инфо о целевом методе: oracle invocations (таблица), поля класса, supertypes, imports |
| **Compare Prompts** | Промпт без аугментации vs с аугментацией бок о бок, с разницей в размере |

Навигация: стрелки вверх/вниз по сэмплам, цифры 1-6 по табам, поиск по имени метода. В заголовке каждого retrieved метода сразу видны badge: Type IoU, кол-во shared owners, Oracle Recall%.

```bash
# Все 100 сэмплов
JAVA_HOME=$(/usr/libexec/java_home -v 17) \
  python -m pipeline.inspect_retrieval --config config.yaml

# Указать выходной файл
JAVA_HOME=$(/usr/libexec/java_home -v 17) \
  python -m pipeline.inspect_retrieval --config config.yaml --n 20 --output ./my_inspection.html
```

### 2. Общий вьювер результатов эксперимента (`viewer`)

После прогона эксперимента (с LLM). Показывает все режимы одновременно — baseline, oracle, retrieval.

```bash
python -m pipeline.viewer --results-dir ./results
open results/viewer.html
```

Самодостаточный HTML-файл. Табы:
- **Code** — подсветка Java для ground truth и сгенерированного кода всех режимов
- **Diff** — side-by-side и unified diff между generated vs ground truth, cross-mode
- **Prompt** — полный FIM-промпт с подсвеченными FIM-токенами, augmentation block
- **Meta** — latency, token usage, compilability, ошибки, таблица invocations

Навигация: поиск, фильтры (EM Only, Compilable, Has Errors), стрелки, цифры 1-4, тёмная/светлая тема.

```bash
python -m pipeline.viewer --results-dir ./results --no-prompts   # без промптов (меньше файл)
python -m pipeline.viewer --results-dir ./results --output ./report.html
```

### 3. Retrieval-ориентированный вьювер результатов (`retrieval_viewer`)

После прогона эксперимента. Фокус на retrieval-метриках и сравнении baseline vs retrieval.

```bash
python -m pipeline.retrieval_viewer --results-dir ./results
open results/retrieval_viewer.html
```

Табы:
- **Overview** — агрегированная таблица метрик (включая Recall@K, API Coverage, MRR) по всем режимам
- **Sample** — per-sample метрики всех режимов бок о бок (badge для каждого mode), oracle invocations
- **Retrieval** — retrieved методы с rank/score, Lucene query, retrieval metrics, augmentation block
- **Code** — ground truth + generated code каждого режима бок о бок

```bash
python -m pipeline.retrieval_viewer --results-dir ./results --output ./retrieval_report.html
```

### Терминальная инспекция (без HTML)

Для быстрого просмотра в терминале:

```bash
# Oracle режимы
python -m pipeline.inspect --config config.yaml --n 3

# Retrieval режим
JAVA_HOME=$(/usr/libexec/java_home -v 17) \
  python -m pipeline.inspect --config config.yaml --n 3 --mode retrieval_augmentation

# Конкретный сэмпл
JAVA_HOME=$(/usr/libexec/java_home -v 17) \
  python -m pipeline.inspect --config config.yaml --index 5 --mode retrieval_augmentation
```

---

## Результаты

После прогона в `results/` будет:

```
results/
├── extracted_methods.json         # Все извлечённые методы (из экстрактора)
├── dataset.json                   # Индекс сэмплированных методов
├── lucene-index/                  # Lucene-индекс для ретривера
├── no_augmentation/
│   ├── aggregate.json             # Агрегированные метрики по режиму
│   └── samples/
│       ├── sample_000.json        # Один сэмпл: промпт, ответ, метрики
│       └── ...
├── ordered_augmentation/
│   └── ...
├── retrieval_augmentation/        # + retrieval results, query debug
│   └── ...
├── summary.json                   # Сравнение всех режимов
├── comparison_table.txt           # Таблица метрик
├── viewer.html                    # Интерактивный вьювер (oracle эксперименты)
├── retrieval_viewer.html          # Вьювер для retrieval эксперимента
└── retrieval_inspection.html      # Инспектор retrieval (без LLM)
```

### Пример таблицы сравнения

```
+----------+--------------------+--------------------+--------------------+
| Metric   | no_augmentation    | ordered_augmentation | shuffled_augmentation |
+----------+--------------------+--------------------+--------------------+
| em       | 0.0300 (std=...)   | 0.0800 (std=...)   | 0.0600 (std=...)   |
| es       | 0.4500 (std=...)   | 0.6200 (std=...)   | 0.5800 (std=...)   |
| iou      | 0.3800 (std=...)   | 0.5500 (std=...)   | 0.5100 (std=...)   |
| lcs_ratio| 0.4100 (std=...)   | 0.5800 (std=...)   | 0.5400 (std=...)   |
| compilable| 0.1200 (std=...)  | 0.2500 (std=...)   | 0.2100 (std=...)   |
+----------+--------------------+--------------------+--------------------+
```

### Метрики

Все считаются на **нормализованном коде** (без комментариев, collapsed whitespace):

| Метрика | Описание |
|---|---|
| **EM** | Exact Match — бинарный, 1 если тела совпали после нормализации |
| **ES** | Edit Similarity — `1 - levenshtein(gen, ref) / max(len_a, len_b)` |
| **IoU** | Jaccard на множестве токенов (multiset intersection / union) |
| **LCS ratio** | Длина LCS на токенах / `max(len(gen_tokens), len(ref_tokens))` |
| **Compilable** | Успешная компиляция файла через `javac` после подстановки тела |
| **Test Pass** | Доля сэмплов, где все тесты прошли после подстановки сгенерированного тела (pass@1) |
| **Recall@K** | Доля oracle invocations, покрытых retrieved аугментацией (только `retrieval_augmentation`) |
| **API Coverage@K** | Доля нужных API (owner+method), найденных ретривером (только `retrieval_augmentation`) |
| **MRR** | Mean Reciprocal Rank похожего метода в retrieved результатах (только `retrieval_augmentation`) |

---

## Структура кода

```
extractor/                              # Gradle multi-project (Java 17)
  settings.gradle.kts                   # include("shared", "extractor-core", "retriever")
  shared/                               # Общие модели данных
    src/.../shared/model/
      ExtractedMethod.java              # Метод с расширенными метаданными
      ClassField.java, SiblingMethod.java, ...
  extractor-core/                       # Парсер Java-проектов
    src/.../extractor/
      cli/ExtractorCli.java             # CLI entry point (picocli)
      analysis/MethodExtractor.java     # JDT two-pass AST visitor
      analysis/MethodClassifier.java    # Фильтрация getter/setter/test/...
      classpath/ClasspathResolver.java  # Gradle init script → classpath
  retriever/                            # Lucene-ретривер
    src/.../retriever/
      cli/RetrieverCli.java             # CLI: index + search-batch
      index/IndexBuilder.java           # Построение Lucene-индекса
      index/MethodCardBuilder.java      # Структурная карточка метода
      index/TypeProfileBuilder.java     # Профиль типов (simple names)
      index/InvocationProfileBuilder.java # Invocation signatures + bigrams
      search/QueryBuilder.java          # Type query + Signature query + Invocation query
      search/SearchExecutor.java        # Поиск + leakage filter + class diversity
      search/LeakageFilter.java         # Исключение target/same-file/near-dup
      similarity/CompositeSimilarity.java # MultiSimilarity: BM25 + LM Jelinek-Mercer + LM Dirichlet

pipeline/                               # Python пайплайн
  config.py            # Dataclass-based YAML config (вкл. RetrievalConfig)
  models.py            # Python mirrors of Java model + RetrievalResult
  dataset.py           # Загрузка, фильтрация, сэмплирование
  prompt.py            # FIM prompt + augmentation block (oracle + retrieval)
  llm.py               # HTTP client для v1/completions
  retrieval.py         # Оркестрация retrieval: index, search, format augmentation
  normalize.py         # Нормализация кода + identifier unification
  metrics.py           # EM, ES, IoU, LCS, CodeBLEU + Recall@K, API Coverage, MRR
  compilability.py     # javac subprocess check
  coverage.py          # JaCoCo интеграция: запуск, парсинг XML, CoverageMap
  test_runner.py       # Замена тела метода → прогон тестов → pass@k
  report.py            # Агрегация + таблицы (вкл. retrieval и test_pass метрики)
  run.py               # Главный оркестратор (вкл. coverage map + test evaluation фаза)
  inspect.py           # Dry-run инспекция сэмплов (вкл. retrieval_augmentation)
  inspect_retrieval.py # HTML-инспектор retrieval (Lucene explain, overlap analysis)
  viewer.py            # HTML-вьювер для oracle экспериментов
  retrieval_viewer.py  # HTML-вьювер для retrieval экспериментов
```

---

## Фильтрация сэмплов для оценки (functional-style + test coverage)

Pipeline поддерживает расширенную фильтрацию методов для оценки method generation через pass@k. Цель — отобрать методы с "богатой функциональной сигнатурой", покрытые тестами.

### Критерии фильтрации

| Критерий | Параметр конфига | Описание |
|----------|-----------------|----------|
| Non-void return type | `extraction.require_non_void: true` | Исключает `void`-методы и конструкторы — оставляет методы, отражающие data flow через сигнатуру |
| Наличие параметров | `extraction.require_parameters: true` | Данные должны поступать через параметры, а не через side effects |
| Покрытие тестами | `extraction.require_test_coverage: true` | Метод должен быть покрыт хотя бы одним тестом (через JaCoCo) |
| ≥3 statement | `extraction.min_statements: 3` | Нетривиальное тело метода |
| Resolved invocations | (всегда) | Все invocations должны иметь EXACT resolution |
| Не getter/setter/... | `extraction.exclude_categories` | Исключаются GETTER, SETTER, DELEGATOR, GENERATED, TEST |

### JaCoCo интеграция (`pipeline/coverage.py`)

Для определения покрытия тестами используется JaCoCo:

1. Запускаются тесты с JaCoCo agent (Maven: `jacoco:prepare-agent test jacoco:report`, Gradle: init script или встроенный плагин)
2. Парсится XML-отчёт — строится `CoverageMap`: `{class_fqn → [MethodCoverage]}`
3. Для матчинга JVM-дескрипторов (из JaCoCo) с FQN-типами (из экстрактора) используется встроенный конвертер

Для multi-module Gradle-проектов (например, JUnit 5) можно объединить exec-файлы через JaCoCo CLI:

```bash
java -jar org.jacoco.cli-nodeps.jar merge */build/jacoco/*.exec --destfile merged.exec
java -jar org.jacoco.cli-nodeps.jar report merged.exec --classfiles */build/classes/java/main --xml report.xml
```

### Test runner для pass@k (`pipeline/test_runner.py`)

После генерации тела метода LLM pipeline может оценить корректность через прогон тестов:

1. Тело метода заменяется в исходном файле (offset-based replacement)
2. Запускается `./gradlew test` или `mvn test`
3. Парсятся XML-отчёты тестов (Surefire/JUnit format)
4. Оригинальный файл восстанавливается из `file_content` (+ fallback через `git checkout`)

Конфигурация:

```yaml
test_evaluation:
  enabled: true
  timeout_seconds: 300
  build_system: "gradle"   # или "maven"
```

Результат: метрика `test_pass` в агрегированном отчёте (доля сэмплов, где все тесты прошли после подстановки сгенерированного тела).

### Датасет компилируемых, но тест-падающих генераций

После прогона генерации, `javac`-проверки и `test_evaluation` можно собрать датасет методов, которые:

1. компилируются (`metrics.compilable == true`);
2. не совпадают с target-методом по Exact Match (`metrics.em == false`);
3. ломают тесты проекта (`test_eval.success == false`, при успешной сборке тестового запуска).

```bash
python -m pipeline.failure_dataset ./results \
  -o ./results/failure_dataset.jsonl \
  --dedupe method_generated
```

Если нужен более строгий срез, где упавший тест эвристически связан с методом
через `direct_test_methods`, `test_file_paths` или имя test-класса:

```bash
python -m pipeline.failure_dataset ./results \
  -o ./results/related_failure_dataset.jsonl \
  --require-related-failure \
  --dedupe method_generated
```

CLI также доступен как `mgx-failure-dataset` после установки пакета. Рядом с датасетом создаётся summary-файл с количеством найденных кандидатов, распределением по режимам и причинами отбраковки.

### Выбор target-проекта

Текущий target-проект: **JUnit 5** (`junit-team/junit5`).

| Параметр | Значение |
|----------|----------|
| Билд | Gradle 9.4.1 |
| Тесты | ~6200, ~49 секунд |
| Извлечено методов | 1079 |
| **После всех фильтров** | **368** |
| JaCoCo coverage | 5414/6905 методов (78%) |

Критерии выбора проекта:
- **Gradle** (не Maven) — для совместимости с закрытым контуром
- **Быстрые тесты** (<2 минут) — для практичного pass@k
- **Много functional-style методов** — utility/library код с non-void return types
- **Совместимость с JDK 25** — Gradle 9.x

Проверенные и отклонённые проекты:

| Проект | Причина отклонения |
|--------|-------------------|
| Apache Commons Lang | Maven (проблемы с прокси в закрытом контуре), 566 методов |
| Caffeine | Тесты 30+ минут (стресс-тестирование кэша) |
| PureFun | Слишком мало методов (61 после фильтров) |
| Picocli | Несовместим с Gradle 9 |
| Functional Java | Старый Gradle, не собирается на JDK 25 |
| Vavr | Maven (не Gradle) |

### Безопасность данных

Pipeline полностью локальный — исходники target-проекта **не отправляются** в сеть:

- **LLM endpoint** (`llm.endpoint_url`) — единственный сетевой вызов. По умолчанию `http://localhost:8080` (локальный). Если LLM запущен локально, данные не покидают машину
- **CDN-ссылки** в HTML-вьюверах — только загрузка JS-библиотек (highlight.js, diff2html) для подсветки синтаксиса в браузере. Исходный код туда не отправляется
- **Экстрактор, ретривер, метрики, coverage, test runner** — полностью офлайн

---

## Воспроизводимость

Все параметры, влияющие на результат, фиксируются в `config.yaml` и логируются в начале каждого прогона:

- `dataset.random_seed` — выбор 100 сэмплов из датасета
- `experiment.shuffle_seed` — перемешивание invocation signatures в `shuffled_augmentation`
- `llm.seed` — seed для LLM (если endpoint поддерживает)
- `llm.temperature`, `llm.max_tokens`, `llm.stop_sequences` — параметры генерации
- `compilability.mode` — уровень проверки компилируемости (`file`)

Каждый `sample_NNN.json` содержит полный промпт и ответ модели, что позволяет воспроизвести и перепроверить любой сэмпл.
