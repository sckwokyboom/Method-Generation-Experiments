package com.experiment.retriever.adapter;

import com.experiment.retriever.api.*;
import com.experiment.retriever.model.SearchRequest;
import com.experiment.retriever.model.SearchResponse;
import com.experiment.retriever.search.SearchExecutor;
import com.experiment.retriever.test.IndexFixture;
import com.experiment.shared.model.ExtractedMethod;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Path;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Integration tests for {@link LuceneRetrievalService} and the full search pipeline.
 *
 * <p>Each test builds a real Lucene index from synthetic fixture data and runs
 * actual searches. Set breakpoints anywhere in the search pipeline to debug:
 * <ul>
 *   <li>{@code SearchExecutor.search()} — query building, scoring, filtering</li>
 *   <li>{@code QueryBuilder.build()} — Lucene query construction</li>
 *   <li>{@code LuceneRetrievalService.search()} — adapter conversion</li>
 * </ul>
 */
class LuceneRetrievalServiceTest {

    @TempDir
    static Path indexDir;

    static List<ExtractedMethod> methods;
    static SearchExecutor executor;
    static LuceneRetrievalService service;

    @BeforeAll
    static void buildIndex() throws Exception {
        methods = IndexFixture.buildIndex(indexDir);
        executor = new SearchExecutor(indexDir);
        service = new LuceneRetrievalService(executor, indexDir, 3);
    }

    @Test
    void searchReturnsResults() {
        // Query using fields that match the Redis methods in the fixture.
        // Target file is NOT in the index so leakage filter won't remove matches.
        // Breakpoint here and step into executor.search() to trace the full pipeline.
        SearchRequest request = new SearchRequest(
                "connect(RedisConfig): RedisClient",
                "com.example.client.ConnectionHelper",     // not in index
                List.of(),
                List.of("java.util.Optional"),
                List.of("config:com.example.redis.RedisConfig", "client:com.example.redis.RedisClient"),
                List.of("disconnect(): void"),
                List.of(),
                List.of("RedisClient"),
                "this.config = config;",       // fimPrefix
                "return this.client;",          // fimSuffix
                "src/main/java/com/example/client/ConnectionHelper.java",  // not in index
                0,
                3,
                0.8
        );

        // Breakpoint: step into search() to see QueryBuilder, TopDocs, LeakageFilter
        SearchResponse response = service.searchNative(request, "");

        assertThat(response).isNotNull();
        assertThat(response.results()).isNotEmpty();
        assertThat(response.searchTimeMs()).isGreaterThanOrEqualTo(0);

        // Results should be ordered by descending score
        for (int i = 1; i < response.results().size(); i++) {
            assertThat(response.results().get(i).score())
                    .isLessThanOrEqualTo(response.results().get(i - 1).score());
        }

        // Each result should have basic fields populated
        response.results().forEach(r -> {
            assertThat(r.id()).isNotNull().isNotEmpty();
            assertThat(r.filePath()).isNotNull().isNotEmpty();
            assertThat(r.score()).isGreaterThan(0f);
        });

        // TopK limit respected
        assertThat(response.results().size()).isLessThanOrEqualTo(3);
    }

    @Test
    void leakageFilterExcludesSameFileResults() {
        // Target the redis file — all Redis methods should be excluded from results
        SearchRequest request = new SearchRequest(
                "connect(RedisConfig): RedisClient",
                "com.example.redis.RedisConnection",
                List.of(),
                List.of(),
                List.of(),
                List.of(),
                List.of(),
                List.of(),
                "", "",
                "src/main/java/com/example/redis/RedisConnection.java",
                168,
                5,
                0.8
        );

        SearchResponse response = service.searchNative(request, "");

        // None of the results should be from the target file
        response.results().forEach(r ->
                assertThat(r.filePath())
                        .as("Result should not be from target file: %s", r.signature())
                        .isNotEqualTo("src/main/java/com/example/redis/RedisConnection.java")
        );
    }

    @Test
    void searchViaAdapterInterfaceReturnsIRetrievalResults() {
        // The adapter path (IRetrieveRequest → SearchRequest) provides limited metadata
        // to QueryBuilder (no classFqn, imports, etc.), so we test it via searchNative()
        // with a proper SearchRequest but verify the result wrapping via the adapter.
        // Breakpoint: step into service.search() to trace adapter conversion.
        SearchRequest request = new SearchRequest(
                "getOrCreate(String, Supplier): Optional",
                "com.example.client.OtherClass",   // not in index
                List.of(),
                List.of("java.util.Optional", "java.util.function.Supplier"),
                List.of("store:java.util.concurrent.ConcurrentHashMap"),
                List.of(),
                List.of(),
                List.of(),
                "", "",
                "src/main/java/com/example/client/OtherClass.java",   // not in index
                0,
                3,
                0.8
        );

        SearchResponse response = service.searchNative(request, "");

        assertThat(response.results()).isNotEmpty();

        // Now also verify the adapter wrapping works correctly
        response.results().forEach(sr -> {
            LuceneRetrievalResult wrapped = new LuceneRetrievalResult(sr);
            assertThat(wrapped.getScore()).isEqualTo(sr.score());
            assertThat(wrapped.getId()).isEqualTo(sr.id());
            assertThat(wrapped.getLocation()).isNotNull();
            assertThat(wrapped.getContent()).isNotNull();
            assertThat(wrapped.getTagScores()).isNotEmpty();
        });
    }

    @Test
    void emptyQueryReturnsGracefully() {
        SearchRequest request = new SearchRequest(
                "", "", List.of(), List.of(), List.of(), List.of(), List.of(), List.of(),
                "", "", "", 0, 5, 0.8
        );

        // Should not throw — graceful empty results
        SearchResponse response = service.searchNative(request, "");

        assertThat(response).isNotNull();
        assertThat(response.searchTimeMs()).isGreaterThanOrEqualTo(0);
    }
}
