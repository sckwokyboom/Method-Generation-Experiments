package com.experiment.extractor.cli;

import com.experiment.extractor.analysis.MethodExtractor;
import com.experiment.extractor.model.ExtractionResult;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import picocli.CommandLine;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.List;
import java.nio.file.Path;
import java.util.concurrent.Callable;

@Command(
        name = "method-extractor",
        description = "Extracts Java method bodies and invocation signatures for LLM experiments",
        mixinStandardHelpOptions = true,
        version = "0.1.0"
)
public class ExtractorCli implements Callable<Integer> {
    private static final Logger log = LoggerFactory.getLogger(ExtractorCli.class);

    @Option(names = {"--project-path"}, required = true,
            description = "Path to the target Java project")
    private Path projectPath;

    @Option(names = {"--output", "-o"}, required = true,
            description = "Output JSON file path")
    private Path outputPath;

    @Option(names = {"--min-statements"}, defaultValue = "3",
            description = "Minimum statement count to include a method (default: ${DEFAULT-VALUE})")
    private int minStatements;

    @Option(names = {"--include-tests"},
            description = "Include test source files")
    private boolean includeTests;

    @Option(names = {"--build-first"},
            description = "Build the project before extraction")
    private boolean buildFirst;

    @Override
    public Integer call() {
        if (!Files.exists(projectPath) || !Files.isDirectory(projectPath)) {
            log.error("Project path does not exist or is not a directory: {}", projectPath);
            return 1;
        }

        if (buildFirst) {
            log.info("Building project at {} ...", projectPath);
            if (!buildProject()) {
                log.error("Project build failed");
                return 1;
            }
            log.info("Project build succeeded");
        }

        try {
            MethodExtractor extractor = new MethodExtractor(projectPath, minStatements, includeTests);
            ExtractionResult result = extractor.extract();

            Path parent = outputPath.getParent();
            if (parent != null) Files.createDirectories(parent);

            ObjectMapper mapper = new ObjectMapper();
            mapper.enable(SerializationFeature.INDENT_OUTPUT);
            String json = mapper.writeValueAsString(result);
            Files.writeString(outputPath, json, StandardCharsets.UTF_8);

            log.info("Wrote {} extracted methods to {}", result.methods().size(), outputPath);
            return 0;
        } catch (Exception e) {
            log.error("Extraction failed", e);
            return 1;
        }
    }

    private boolean buildProject() {
        Path gradlew = projectPath.resolve("gradlew");
        List<String> command;
        if (Files.isExecutable(gradlew)) {
            command = java.util.List.of("./gradlew", "build", "-x", "test");
        } else if (Files.exists(projectPath.resolve("pom.xml"))) {
            command = java.util.List.of("mvn", "compile", "-DskipTests");
        } else {
            command = java.util.List.of("gradle", "build", "-x", "test");
        }

        try {
            Process process = new ProcessBuilder(command)
                    .directory(projectPath.toFile())
                    .inheritIO()
                    .start();
            return process.waitFor() == 0;
        } catch (IOException | InterruptedException e) {
            Thread.currentThread().interrupt();
            log.error("Build execution failed", e);
            return false;
        }
    }

    public static void main(String[] args) {
        int exitCode = new CommandLine(new ExtractorCli()).execute(args);
        System.exit(exitCode);
    }
}
