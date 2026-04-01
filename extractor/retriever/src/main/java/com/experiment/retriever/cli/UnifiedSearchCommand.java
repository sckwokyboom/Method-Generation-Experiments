package com.experiment.retriever.cli;

import com.experiment.retriever.adapter.LuceneRetrievalService;
import com.experiment.retriever.adapter.SearchResponseAdapter;
import com.experiment.retriever.api.*;
import com.experiment.retriever.external.ExternalRetrieverLoader;
import com.experiment.retriever.model.SearchRequest;
import com.experiment.retriever.model.SearchResponse;
import com.experiment.retriever.search.SearchExecutor;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.Callable;

/**
 * Unified search command supporting both the built-in Lucene retriever
 * and external retrievers implementing {@link IRetrievalService}.
 *
 * <p>For {@code --retriever-type lucene}: reads the same batch entry format
 * as the existing {@code search-batch} command, delegates to {@link SearchExecutor}.
 *
 * <p>For {@code --retriever-type external}: reads external request format,
 * constructs {@link RetrieveRequestWithMaskingVariant} objects, loads the
 * external retriever, and converts results back to the standard JSON format.
 */
@Command(name = "unified-search", description = "Unified batch search supporting multiple retriever backends")
public class UnifiedSearchCommand implements Callable<Integer> {

    private static final Logger log = LoggerFactory.getLogger(UnifiedSearchCommand.class);
    private static final ObjectMapper MAPPER = new ObjectMapper()
            .enable(SerializationFeature.INDENT_OUTPUT);

    @Option(names = "--retriever-type", defaultValue = "lucene",
            description = "Retriever backend: 'lucene' or 'external' (default: ${DEFAULT-VALUE})")
    private String retrieverType;

    @Option(names = "--retriever-class",
            description = "FQN of IRetrievalService.Factory (required for external type)")
    private String retrieverClass;

    @Option(names = "--retriever-jars",
            description = "Comma-separated paths to external retriever JARs")
    private String retrieverJars;

    @Option(names = "--project-config",
            description = "Path to project config JSON (required for external type)")
    private Path projectConfigPath;

    @Option(names = {"--index-dir", "-d"},
            description = "Lucene index directory (required for lucene type)")
    private Path indexDir;

    @Option(names = {"--requests", "-r"}, required = true,
            description = "Path to batch requests JSON file")
    private Path requestsPath;

    @Option(names = {"--output", "-o"}, required = true,
            description = "Output path for batch results JSON")
    private Path outputPath;

    @Option(names = {"--top-k", "-k"}, defaultValue = "5",
            description = "Number of results per query (default: ${DEFAULT-VALUE})")
    private int topK;

    @Override
    public Integer call() {
        try {
            return switch (retrieverType) {
                case "lucene" -> runLucene();
                case "external" -> runExternal();
                default -> {
                    log.error("Unknown retriever type: {}", retrieverType);
                    yield 1;
                }
            };
        } catch (Exception e) {
            log.error("Unified search failed", e);
            return 1;
        }
    }

    private Integer runLucene() throws Exception {
        if (indexDir == null) {
            log.error("--index-dir is required for lucene retriever type");
            return 1;
        }

        String requestsJson = Files.readString(requestsPath, StandardCharsets.UTF_8);
        List<RetrieverCli.BatchEntry> entries = MAPPER.readValue(requestsJson,
                new TypeReference<>() {});

        log.info("Processing {} search requests via Lucene at {}", entries.size(), indexDir);

        List<SearchResponse> responses = new ArrayList<>();
        try (SearchExecutor executor = new SearchExecutor(indexDir)) {
            for (int i = 0; i < entries.size(); i++) {
                RetrieverCli.BatchEntry entry = entries.get(i);
                SearchRequest request = entry.request();

                SearchRequest effectiveRequest = request.topK() > 0 ? request :
                        new SearchRequest(
                                request.methodSignature(), request.classFqn(),
                                request.supertypes(), request.imports(),
                                request.classFields(), request.siblingSignatures(),
                                request.siblingOwnerTypes(), request.siblingUsedTypes(),
                                request.fimPrefix(), request.fimSuffix(),
                                request.targetFilePath(), request.targetBodyStartOffset(),
                                topK, request.nearDuplicateThreshold()
                        );

                responses.add(executor.search(effectiveRequest, entry.targetMethodBody()));

                if ((i + 1) % 10 == 0 || i == entries.size() - 1) {
                    log.info("Processed {}/{} queries", i + 1, entries.size());
                }
            }
        }

        writeOutput(responses);
        return 0;
    }

    private Integer runExternal() throws Exception {
        if (retrieverClass == null || retrieverClass.isEmpty()) {
            log.error("--retriever-class is required for external retriever type");
            return 1;
        }
        if (projectConfigPath == null) {
            log.error("--project-config is required for external retriever type");
            return 1;
        }

        // Load project config
        SimpleProject project = MAPPER.readValue(
                Files.readString(projectConfigPath, StandardCharsets.UTF_8),
                SimpleProject.class);
        log.info("Project: {} at {}", project.getProjectName(), project.getProjectPath());

        // Parse external JAR paths
        List<Path> jarPaths = new ArrayList<>();
        if (retrieverJars != null && !retrieverJars.isEmpty()) {
            for (String jar : retrieverJars.split(",")) {
                jarPaths.add(Path.of(jar.trim()));
            }
        }

        // Load external retriever
        IRetrievalService retriever = ExternalRetrieverLoader.load(
                retrieverClass, jarPaths, project);
        log.info("External retriever loaded: {}", retriever.getId());

        // Read requests
        String requestsJson = Files.readString(requestsPath, StandardCharsets.UTF_8);
        List<ExternalBatchEntry> entries = MAPPER.readValue(requestsJson,
                new TypeReference<>() {});

        log.info("Processing {} search requests via external retriever", entries.size());

        List<SearchResponse> responses = new ArrayList<>();
        for (int i = 0; i < entries.size(); i++) {
            ExternalBatchEntry entry = entries.get(i);
            long startMs = System.currentTimeMillis();

            try {
                IRetrieveRequest request = entry.toRetrieveRequest();
                List<IRetrievalResult> results = retriever.search(request);

                // Limit to topK
                if (results.size() > topK) {
                    results = results.subList(0, topK);
                }

                long elapsed = System.currentTimeMillis() - startMs;
                responses.add(SearchResponseAdapter.toSearchResponse(results, elapsed));
            } catch (Exception e) {
                // Fail fast with detailed context so the user can diagnose the issue
                log.error("External retriever failed on request {}/{} (file: {}): {}",
                        i + 1, entries.size(), entry.filePath(), e.getMessage());
                throw new RuntimeException(
                        "External retriever failed on request " + (i + 1) + "/" + entries.size()
                        + " (file: " + entry.filePath()
                        + ", bodyStartOffset: " + entry.bodyStartOffset()
                        + ", bodyEndOffset: " + entry.bodyEndOffset() + ")", e);
            }

            if ((i + 1) % 10 == 0 || i == entries.size() - 1) {
                log.info("Processed {}/{} queries", i + 1, entries.size());
            }
        }

        writeOutput(responses);
        return 0;
    }

    private void writeOutput(List<SearchResponse> responses) throws Exception {
        Path parent = outputPath.getParent();
        if (parent != null) Files.createDirectories(parent);
        String outputJson = MAPPER.writeValueAsString(responses);
        Files.writeString(outputPath, outputJson, StandardCharsets.UTF_8);
        log.info("Wrote {} search responses to {}", responses.size(), outputPath);
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record ExternalBatchEntry(
            @JsonProperty("sourceCode") String sourceCode,
            @JsonProperty("bodyStartOffset") int bodyStartOffset,
            @JsonProperty("bodyEndOffset") int bodyEndOffset,
            @JsonProperty("filePath") String filePath,
            @JsonProperty("userPrompt") String userPrompt,
            @JsonProperty("targetMethodBody") String targetMethodBody
    ) {
        IRetrieveRequest toRetrieveRequest() {
            IMaskingVariant masking = SimpleMaskingVariant.fromBodyOffsets(
                    sourceCode, bodyStartOffset, bodyEndOffset);
            return new RetrieveRequestWithMaskingVariant(
                    sourceCode, masking,
                    userPrompt != null ? userPrompt : "",
                    Path.of(filePath));
        }
    }
}
