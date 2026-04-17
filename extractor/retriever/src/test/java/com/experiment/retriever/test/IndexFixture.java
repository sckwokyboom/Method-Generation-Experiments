package com.experiment.retriever.test;

import com.experiment.retriever.index.IndexBuilder;
import com.experiment.shared.model.*;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/**
 * Creates a small Lucene index from synthetic methods for integration tests.
 * Reuses {@link IndexBuilder} to ensure the test index matches production behavior.
 */
public final class IndexFixture {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private IndexFixture() {}

    /**
     * Builds a Lucene index in the given directory from the default set of test methods.
     *
     * @param indexDir directory where the Lucene index will be created
     * @return the list of methods that were indexed (for building queries/assertions)
     */
    public static List<ExtractedMethod> buildIndex(Path indexDir) throws IOException {
        List<ExtractedMethod> methods = createTestMethods();

        ExtractionMeta meta = new ExtractionMeta(
                "2026-01-01T00:00:00Z", 100, 3, methods.size(), methods.size(), 0);
        ExtractionResult extraction = new ExtractionResult("test-project", meta, List.of(), methods);

        Path tempFile = Files.createTempFile("extraction-", ".json");
        try {
            Files.writeString(tempFile, MAPPER.writeValueAsString(extraction), StandardCharsets.UTF_8);
            new IndexBuilder().buildIndex(tempFile, indexDir);
        } finally {
            Files.deleteIfExists(tempFile);
        }

        return methods;
    }

    /**
     * Creates synthetic methods spanning multiple classes and packages.
     * Includes one method in the same file as a typical target (for leakage filter testing).
     */
    public static List<ExtractedMethod> createTestMethods() {
        String redisFile = "src/main/java/com/example/redis/RedisConnection.java";
        String redisFileContent = """
                package com.example.redis;

                import java.util.Optional;

                public class RedisConnection {
                    private RedisConfig config;
                    private RedisClient client;

                    public RedisClient connect(RedisConfig config) {
                        this.config = config;
                        this.client = RedisClient.create(config.getHost(), config.getPort());
                        return this.client;
                    }

                    public void disconnect() {
                        if (client != null) {
                            client.close();
                            client = null;
                        }
                    }

                    public Optional<String> get(String key) {
                        return Optional.ofNullable(client.get(key));
                    }
                }
                """;

        String cacheFile = "src/main/java/com/example/cache/CacheManager.java";
        String cacheFileContent = """
                package com.example.cache;

                import java.util.Optional;
                import java.util.function.Supplier;
                import java.util.concurrent.ConcurrentHashMap;

                public class CacheManager {
                    private final ConcurrentHashMap<String, Object> store = new ConcurrentHashMap<>();

                    public <T> Optional<T> getOrCreate(String key, Supplier<T> supplier) {
                        Object value = store.computeIfAbsent(key, k -> supplier.get());
                        return Optional.of((T) value);
                    }

                    public void invalidate(String key) {
                        store.remove(key);
                    }
                }
                """;

        String brokerFile = "src/main/java/com/example/messaging/MessageBroker.java";
        String brokerFileContent = """
                package com.example.messaging;

                import java.util.concurrent.CompletableFuture;

                public class MessageBroker {
                    public CompletableFuture<Void> publish(Message message, Topic topic) {
                        return CompletableFuture.runAsync(() -> topic.send(message));
                    }
                }
                """;

        return List.of(
                new ExtractedMethod(
                        redisFile, redisFileContent,
                        "com.example.redis.RedisConnection",
                        "connect(RedisConfig): RedisClient",
                        "connect",
                        "{ this.config = config; this.client = RedisClient.create(config.getHost(), config.getPort()); return this.client; }",
                        168, 290, 3,
                        MethodCategory.NORMAL,
                        List.of(
                                new ResolvedInvocation("RedisClient.create(String, int)", "RESOLVED", 0),
                                new ResolvedInvocation("RedisConfig.getHost()", "RESOLVED", 1),
                                new ResolvedInvocation("RedisConfig.getPort()", "RESOLVED", 2)
                        ),
                        List.of("java.util.Optional"),
                        List.of(new ClassField("config", "com.example.redis.RedisConfig"),
                                new ClassField("client", "com.example.redis.RedisClient")),
                        List.of(),
                        List.of(new SiblingMethod("disconnect(): void", "disconnect", List.of(), "void", List.of(), List.of("RedisClient")),
                                new SiblingMethod("get(String): Optional", "get", List.of("String"), "Optional", List.of(), List.of("RedisClient", "Optional"))),
                        "RedisClient",
                        List.of("RedisConfig"),
                        List.of(),
                        List.of("RedisClient", "RedisConfig")
                ),
                new ExtractedMethod(
                        redisFile, redisFileContent,
                        "com.example.redis.RedisConnection",
                        "disconnect(): void",
                        "disconnect",
                        "{ if (client != null) { client.close(); client = null; } }",
                        310, 380, 2,
                        MethodCategory.NORMAL,
                        List.of(new ResolvedInvocation("RedisClient.close()", "RESOLVED", 0)),
                        List.of("java.util.Optional"),
                        List.of(new ClassField("config", "com.example.redis.RedisConfig"),
                                new ClassField("client", "com.example.redis.RedisClient")),
                        List.of(),
                        List.of(new SiblingMethod("connect(RedisConfig): RedisClient", "connect", List.of("RedisConfig"), "RedisClient",
                                List.of(new ResolvedInvocation("RedisClient.create(String, int)", "RESOLVED", 0)),
                                List.of("RedisClient", "RedisConfig"))),
                        "void",
                        List.of(),
                        List.of(),
                        List.of("RedisClient")
                ),
                new ExtractedMethod(
                        redisFile, redisFileContent,
                        "com.example.redis.RedisConnection",
                        "get(String): Optional",
                        "get",
                        "{ return Optional.ofNullable(client.get(key)); }",
                        400, 450, 1,
                        MethodCategory.NORMAL,
                        List.of(
                                new ResolvedInvocation("Optional.ofNullable(Object)", "RESOLVED", 0),
                                new ResolvedInvocation("RedisClient.get(String)", "RESOLVED", 1)
                        ),
                        List.of("java.util.Optional"),
                        List.of(new ClassField("config", "com.example.redis.RedisConfig"),
                                new ClassField("client", "com.example.redis.RedisClient")),
                        List.of(),
                        List.of(),
                        "Optional",
                        List.of("String"),
                        List.of(),
                        List.of("Optional", "RedisClient")
                ),
                new ExtractedMethod(
                        cacheFile, cacheFileContent,
                        "com.example.cache.CacheManager",
                        "getOrCreate(String, Supplier): Optional",
                        "getOrCreate",
                        "{ Object value = store.computeIfAbsent(key, k -> supplier.get()); return Optional.of((T) value); }",
                        200, 310, 2,
                        MethodCategory.NORMAL,
                        List.of(
                                new ResolvedInvocation("ConcurrentHashMap.computeIfAbsent(Object, Function)", "RESOLVED", 0),
                                new ResolvedInvocation("Supplier.get()", "RESOLVED", 1),
                                new ResolvedInvocation("Optional.of(Object)", "RESOLVED", 2)
                        ),
                        List.of("java.util.Optional", "java.util.function.Supplier", "java.util.concurrent.ConcurrentHashMap"),
                        List.of(new ClassField("store", "java.util.concurrent.ConcurrentHashMap")),
                        List.of(),
                        List.of(new SiblingMethod("invalidate(String): void", "invalidate", List.of("String"), "void", List.of(), List.of("ConcurrentHashMap"))),
                        "Optional",
                        List.of("String", "Supplier"),
                        List.of(),
                        List.of("Optional", "ConcurrentHashMap", "Supplier")
                ),
                new ExtractedMethod(
                        cacheFile, cacheFileContent,
                        "com.example.cache.CacheManager",
                        "invalidate(String): void",
                        "invalidate",
                        "{ store.remove(key); }",
                        330, 355, 1,
                        MethodCategory.NORMAL,
                        List.of(new ResolvedInvocation("ConcurrentHashMap.remove(Object)", "RESOLVED", 0)),
                        List.of("java.util.concurrent.ConcurrentHashMap"),
                        List.of(new ClassField("store", "java.util.concurrent.ConcurrentHashMap")),
                        List.of(),
                        List.of(new SiblingMethod("getOrCreate(String, Supplier): Optional", "getOrCreate", List.of("String", "Supplier"), "Optional",
                                List.of(new ResolvedInvocation("ConcurrentHashMap.computeIfAbsent(Object, Function)", "RESOLVED", 0)),
                                List.of("Optional", "ConcurrentHashMap", "Supplier"))),
                        "void",
                        List.of("String"),
                        List.of(),
                        List.of("ConcurrentHashMap")
                ),
                new ExtractedMethod(
                        brokerFile, brokerFileContent,
                        "com.example.messaging.MessageBroker",
                        "publish(Message, Topic): CompletableFuture",
                        "publish",
                        "{ return CompletableFuture.runAsync(() -> topic.send(message)); }",
                        130, 200, 1,
                        MethodCategory.NORMAL,
                        List.of(
                                new ResolvedInvocation("CompletableFuture.runAsync(Runnable)", "RESOLVED", 0),
                                new ResolvedInvocation("Topic.send(Message)", "RESOLVED", 1)
                        ),
                        List.of("java.util.concurrent.CompletableFuture"),
                        List.of(),
                        List.of(),
                        List.of(),
                        "CompletableFuture",
                        List.of("Message", "Topic"),
                        List.of(),
                        List.of("CompletableFuture", "Topic", "Message")
                )
        );
    }
}
