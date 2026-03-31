package com.experiment.retriever.cli;

import com.experiment.retriever.index.IndexBuilder;
import com.experiment.retriever.model.SearchRequest;
import com.experiment.retriever.model.SearchResponse;
import com.experiment.retriever.search.SearchExecutor;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import picocli.CommandLine;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;

@Command(
        name = "method-retriever",
        description = "Indexes and searches Java methods for retrieval-augmented generation",
        mixinStandardHelpOptions = true,
        version = "0.1.0",
        subcommands = {RetrieverCli.IndexCommand.class, RetrieverCli.SearchBatchCommand.class}
)
public class RetrieverCli {
    private static final Logger log = LoggerFactory.getLogger(RetrieverCli.class);
    private static final ObjectMapper MAPPER = new ObjectMapper()
            .enable(SerializationFeature.INDENT_OUTPUT);

    @Command(name = "index", description = "Build Lucene index from extracted methods JSON")
    static class IndexCommand implements Callable<Integer> {
        @Option(names = {"--input", "-i"}, required = true,
                description = "Path to extracted_methods.json")
        private Path inputPath;

        @Option(names = {"--index-dir", "-d"}, required = true,
                description = "Output directory for Lucene index")
        private Path indexDir;

        @Override
        public Integer call() {
            try {
                new IndexBuilder().buildIndex(inputPath, indexDir);
                return 0;
            } catch (Exception e) {
                log.error("Indexing failed", e);
                return 1;
            }
        }
    }

    @Command(name = "search-batch", description = "Run batch search against Lucene index")
    static class SearchBatchCommand implements Callable<Integer> {
        @Option(names = {"--index-dir", "-d"}, required = true,
                description = "Path to Lucene index directory")
        private Path indexDir;

        @Option(names = {"--requests", "-r"}, required = true,
                description = "Path to batch requests JSON file (array of SearchRequest)")
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
                String requestsJson = Files.readString(requestsPath, StandardCharsets.UTF_8);
                List<BatchEntry> entries = MAPPER.readValue(requestsJson,
                        new TypeReference<List<BatchEntry>>() {});

                log.info("Processing {} search requests against index at {}", entries.size(), indexDir);

                List<SearchResponse> responses = new ArrayList<>();

                try (SearchExecutor executor = new SearchExecutor(indexDir)) {
                    for (int i = 0; i < entries.size(); i++) {
                        BatchEntry entry = entries.get(i);
                        SearchRequest request = entry.request();

                        // Override topK from CLI if request doesn't specify
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

                        SearchResponse response = executor.search(effectiveRequest, entry.targetMethodBody());
                        responses.add(response);

                        if ((i + 1) % 10 == 0 || i == entries.size() - 1) {
                            log.info("Processed {}/{} queries", i + 1, entries.size());
                        }
                    }
                }

                Path parent = outputPath.getParent();
                if (parent != null) Files.createDirectories(parent);
                String outputJson = MAPPER.writeValueAsString(responses);
                Files.writeString(outputPath, outputJson, StandardCharsets.UTF_8);
                log.info("Wrote {} search responses to {}", responses.size(), outputPath);

                return 0;
            } catch (Exception e) {
                log.error("Batch search failed", e);
                return 1;
            }
        }
    }

    public record BatchEntry(
            @com.fasterxml.jackson.annotation.JsonProperty("request") SearchRequest request,
            @com.fasterxml.jackson.annotation.JsonProperty("targetMethodBody") String targetMethodBody
    ) {}

    public static void main(String[] args) {
        int exitCode = new CommandLine(new RetrieverCli()).execute(args);
        System.exit(exitCode);
    }
}
