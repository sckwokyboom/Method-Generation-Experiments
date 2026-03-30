# Java Method Generation Experiments

Экспериментальный пайплайн для оценки генерации тела Java-метода через LLM (Qwen 2.5 Coder, FIM mode).

**Гипотеза:** помогает ли аугментация из сигнатур method invocations при генерации, и влияет ли порядок сигнатур на качество?

**Три режима:**

| Режим | Описание |
|---|---|
| `no_augmentation` | Только замаскированный Java-файл, без подсказок |
| `ordered_augmentation` | + блок с сигнатурами invocations в порядке появления в коде |
| `shuffled_augmentation` | + те же сигнатуры, но в случайном порядке |

---

## Требования

- **Java 17** (для сборки и запуска extractor'а)
- **Java 21+** (нужна для сборки target-проекта rustyconnector)
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

### 2. Собрать extractor JAR

Extractor — это отдельный Java-модуль, который парсит Java-проекты через Eclipse JDT и извлекает методы с типизированными сигнатурами invocations.

```bash
cd extractor
JAVA_HOME=$(/usr/libexec/java_home -v 17) ./gradlew jar
cd ..
```

Результат: `extractor/build/libs/method-extractor.jar` (~17 MB, fat JAR со всеми зависимостями).

Убедиться, что JAR запускается:

```bash
java -jar extractor/build/libs/method-extractor.jar --help
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
  $(/usr/libexec/java_home -v 17)/bin/java -jar extractor/build/libs/method-extractor.jar \
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

### Полный прогон (все 3 режима, 100 сэмплов)

```bash
python -m pipeline.run --config config.yaml
```

### Отдельные режимы

```bash
# Только без аугментации
python -m pipeline.run --config config.yaml --mode no_augmentation

# Два режима
python -m pipeline.run --config config.yaml --mode ordered_augmentation shuffled_augmentation

# Без проверки компилируемости (быстрее)
python -m pipeline.run --config config.yaml --skip-compilability

# Пропустить экстракцию (если уже сделана)
python -m pipeline.run --config config.yaml --skip-extraction
```

---

## Результаты

После прогона в `results/` будет:

```
results/
├── extracted_methods.json         # Все извлечённые методы (из экстрактора)
├── dataset.json                   # Индекс сэмплированных методов
├── no_augmentation/
│   ├── aggregate.json             # Агрегированные метрики по режиму
│   └── samples/
│       ├── sample_000.json        # Один сэмпл: промпт, ответ, метрики
│       └── ...
├── ordered_augmentation/
│   └── ...
├── shuffled_augmentation/
│   └── ...
├── summary.json                   # Сравнение всех режимов
└── comparison_table.txt           # Таблица метрик
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

---

## Структура кода

```
extractor/
  src/main/java/com/experiment/extractor/
    cli/ExtractorCli.java           # CLI entry point (picocli)
    analysis/MethodExtractor.java   # JDT two-pass AST visitor
    analysis/MethodClassifier.java  # Фильтрация getter/setter/test/...
    classpath/ClasspathResolver.java # Gradle init script → classpath
    model/                          # Data records

pipeline/
  config.py          # Dataclass-based YAML config
  models.py          # Python mirrors of Java model
  dataset.py         # Загрузка, фильтрация, сэмплирование
  prompt.py          # FIM prompt + augmentation block
  llm.py             # httpx client для v1/completions
  normalize.py       # Нормализация кода + identifier unification
  metrics.py         # EM, ES, IoU, LCS
  compilability.py   # javac subprocess check
  report.py          # Агрегация + таблицы
  run.py             # Главный оркестратор
  inspect.py         # Dry-run инспекция сэмплов
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
