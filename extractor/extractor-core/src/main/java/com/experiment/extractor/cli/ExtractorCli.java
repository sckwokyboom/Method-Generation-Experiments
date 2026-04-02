package com.experiment.extractor.cli;

import com.experiment.extractor.analysis.MethodExtractor;
import com.experiment.shared.model.ExtractionResult;
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
import java.nio.file.Path;
import java.util.List;
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
        boolean isWindows = System.getProperty("os.name", "").toLowerCase().contains("win");
        Path gradlew = projectPath.resolve(isWindows ? "gradlew.bat" : "gradlew");
        List<String> command;
        if (Files.exists(gradlew)) {
            command = List.of(gradlew.toAbsolutePath().toString(), "compileJava");
        } else if (Files.exists(projectPath.resolve("pom.xml"))) {
            command = List.of("mvn", "compile", "-DskipTests");
        } else {
            command = List.of("gradle", "compileJava");
        }

        log.info("Build command: {}", command);
        try {
            ProcessBuilder pb = new ProcessBuilder(command)
                    .directory(projectPath.toFile())
                    .redirectErrorStream(true);
            Process process = pb.start();
            String output = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
            int exitCode = process.waitFor();
            if (exitCode != 0) {
                log.error("Build failed (exit code {}). Output:\n{}", exitCode,
                        output.length() > 3000 ? output.substring(output.length() - 3000) : output);
            } else {
                log.info("Build completed successfully");
            }
            return exitCode == 0;
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
