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

- **Java 17** (для сборки и запуска extractor'а и retriever'а)
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

## Интерактивный просмотр результатов

После прогона эксперимента можно сгенерировать интерактивный HTML-вьювер для детального анализа каждого сэмпла:

```bash
python -m pipeline.viewer --results-dir ./results
```

Откроет `results/viewer.html` — самодостаточный HTML-файл без серверных зависимостей.

**Возможности:**

- **Code** — подсветка синтаксиса Java для ground truth и сгенерированного кода всех 3 режимов (полные методы с сигнатурой)
- **Diff** — side-by-side и unified diff между любыми парами: generated vs ground truth, cross-mode сравнение
- **Prompt** — полный FIM-промпт с подсвеченными токенами `<|fim_prefix|>` / `<|fim_suffix|>` / `<|fim_middle|>`, augmentation block
- **Meta** — latency, token usage, compilability, ошибки компиляции, таблица invocations

**Навигация:** поиск по method ID, фильтры (EM Only, Compilable, Has Errors), стрелки вверх/вниз для переключения сэмплов, цифры 1-4 для табов, переключение светлой/тёмной темы.

```bash
# Исключить промпты (значительно уменьшает размер файла)
python -m pipeline.viewer --results-dir ./results --no-prompts

# Указать путь к выходному файлу
python -m pipeline.viewer --results-dir ./results --output ./report.html
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
  report.py            # Агрегация + таблицы (вкл. retrieval метрики)
  run.py               # Главный оркестратор
  inspect.py           # Dry-run инспекция сэмплов (вкл. retrieval_augmentation)
  inspect_retrieval.py # HTML-инспектор retrieval (Lucene explain, overlap analysis)
  viewer.py            # HTML-вьювер для oracle экспериментов
  retrieval_viewer.py  # HTML-вьювер для retrieval экспериментов
```

---

## Воспроизводимость

Все параметры, влияющие на результат, фиксируются в `config.yaml` и логируются в начале каждого прогона:

- `dataset.random_seed` — выбор 100 сэмплов из датасета
- `experiment.shuffle_seed` — перемешивание invocation signatures в `shuffled_augmentation`
- `llm.seed` — seed для LLM (если endpoint поддерживает)
- `llm.temperature`, `llm.max_tokens`, `llm.stop_sequences` — параметры генерации
- `compilability.mode` — уровень проверки компилируемости (`file`)

Каждый `sample_NNN.json` содержит полный промпт и ответ модели, что позволяет воспроизвести и перепроверить любой сэмпл.
