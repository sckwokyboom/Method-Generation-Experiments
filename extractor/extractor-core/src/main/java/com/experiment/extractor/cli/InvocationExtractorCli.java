package com.experiment.extractor.cli;

import com.experiment.extractor.analysis.MethodExtractor;
import com.experiment.shared.model.ResolvedInvocation;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;

/**
 * Batch-extract invocations from generated method bodies using JDT AST with type binding.
 *
 * <p>Input JSON format:
 * <pre>{
 *   "classpath": ["path/to/dep.jar", ...],
 *   "sourceRoots": ["src/main/java", ...],
 *   "samples": [
 *     {
 *       "index": 0,
 *       "fileContent": "...",
 *       "bodyStartOffset": 1234,
 *       "bodyEndOffset": 1456,
 *       "generatedBody": "...",
 *       "filePath": "src/main/java/com/example/Foo.java"
 *     }
 *   ]
 * }</pre>
 *
 * <p>Output JSON: array of {@code {"index": N, "invocations": [...]}}
 */
@Command(
        name = "extract-invocations",
        description = "Extract invocations from generated method bodies (batch mode)",
        mixinStandardHelpOptions = true
)
public class InvocationExtractorCli implements Callable<Integer> {
    private static final Logger log = LoggerFactory.getLogger(InvocationExtractorCli.class);

    @Option(names = {"--project-path"}, required = true,
            description = "Path to the target Java project (used as base for relative file paths)")
    private Path projectPath;

    @Option(names = {"--input", "-i"}, required = true,
            description = "Input JSON file with batch extraction requests")
    private Path inputPath;

    @Option(names = {"--output", "-o"}, required = true,
            description = "Output JSON file for extraction results")
    private Path outputPath;

    @Override
    public Integer call() {
        try {
            ObjectMapper mapper = new ObjectMapper();
            JsonNode root = mapper.readTree(Files.readString(inputPath, StandardCharsets.UTF_8));

            // Read classpath and source roots
            List<String> classpathList = new ArrayList<>();
            if (root.has("classpath")) {
                for (JsonNode cp : root.get("classpath")) {
                    classpathList.add(cp.asText());
                }
            }
            String[] classpath = classpathList.toArray(String[]::new);

            List<String> sourceRootsList = new ArrayList<>();
            if (root.has("sourceRoots")) {
                for (JsonNode sr : root.get("sourceRoots")) {
                    sourceRootsList.add(sr.asText());
                }
            }
            String[] sourceRoots = sourceRootsList.toArray(String[]::new);

            JsonNode samples = root.get("samples");
            if (samples == null || !samples.isArray()) {
                log.error("Input JSON must have a 'samples' array");
                return 1;
            }

            log.info("Processing {} samples with {} classpath entries and {} source roots",
                    samples.size(), classpath.length, sourceRoots.length);

            MethodExtractor extractor = new MethodExtractor(projectPath, 0, false);

            ArrayNode results = mapper.createArrayNode();

            for (JsonNode sample : samples) {
                int index = sample.get("index").asInt();
                String fileContent = sample.get("fileContent").asText();
                int bodyStartOffset = sample.get("bodyStartOffset").asInt();
                int bodyEndOffset = sample.get("bodyEndOffset").asInt();
                String generatedBody = sample.get("generatedBody").asText();
                String filePath = sample.get("filePath").asText();

                try {
                    List<ResolvedInvocation> invocations = extractor.extractInvocationsFromBody(
                            fileContent, filePath, bodyStartOffset, bodyEndOffset,
                            generatedBody, classpath, sourceRoots
                    );

                    ObjectNode entry = mapper.createObjectNode();
                    entry.put("index", index);
                    ArrayNode invArray = entry.putArray("invocations");
                    for (ResolvedInvocation inv : invocations) {
                        ObjectNode invNode = mapper.createObjectNode();
                        invNode.put("signature", inv.signature());
                        invNode.put("resolutionMode", inv.resolutionMode());
                        invNode.put("orderIndex", inv.orderIndex());
                        invArray.add(invNode);
                    }
                    results.add(entry);

                    log.info("Sample {}: {} invocations ({} EXACT)",
                            index, invocations.size(),
                            invocations.stream().filter(i -> "EXACT".equals(i.resolutionMode())).count());
                } catch (Exception e) {
                    log.warn("Failed to extract invocations for sample {}: {}", index, e.getMessage());
                    ObjectNode entry = mapper.createObjectNode();
                    entry.put("index", index);
                    entry.putArray("invocations");
                    entry.put("error", e.getMessage());
                    results.add(entry);
                }
            }

            Path parent = outputPath.getParent();
            if (parent != null) Files.createDirectories(parent);

            mapper.enable(SerializationFeature.INDENT_OUTPUT);
            Files.writeString(outputPath, mapper.writeValueAsString(results), StandardCharsets.UTF_8);

            log.info("Wrote results to {}", outputPath);
            return 0;
        } catch (Exception e) {
            log.error("Invocation extraction failed", e);
            return 1;
        }
    }
}
