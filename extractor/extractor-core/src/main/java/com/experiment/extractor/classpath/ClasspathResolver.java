package com.experiment.extractor.classpath;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Pattern;
import java.util.stream.Stream;

public class ClasspathResolver {
    private static final Logger log = LoggerFactory.getLogger(ClasspathResolver.class);

    public record Result(
            List<String> classpathEntries,
            List<String> sourceRoots,
            List<String> warnings
    ) {}

    public Result resolve(Path projectPath) {
        List<String> warnings = new ArrayList<>();
        Set<Path> classpath = new LinkedHashSet<>();
        Set<Path> sourceRoots = detectSourceRoots(projectPath);

        if (Files.exists(projectPath.resolve("build.gradle"))
                || Files.exists(projectPath.resolve("build.gradle.kts"))) {
            List<Path> gradleCp = resolveWithGradle(projectPath, warnings);
            classpath.addAll(gradleCp);
        }

        if (Files.exists(projectPath.resolve("pom.xml")) && classpath.isEmpty()) {
            List<Path> mavenCp = resolveWithMaven(projectPath, warnings);
            classpath.addAll(mavenCp);
        }

        addBuildOutputs(projectPath, classpath);

        List<String> cpStrings = classpath.stream()
                .filter(Files::exists)
                .map(p -> p.toAbsolutePath().toString())
                .toList();

        List<String> srStrings = sourceRoots.stream()
                .filter(Files::exists)
                .map(p -> p.toAbsolutePath().toString())
                .toList();

        log.info("Resolved {} classpath entries, {} source roots", cpStrings.size(), srStrings.size());
        return new Result(cpStrings, srStrings, warnings);
    }

    private Set<Path> detectSourceRoots(Path projectPath) {
        Set<Path> roots = new LinkedHashSet<>();
        try (Stream<Path> stream = Files.walk(projectPath, 6)) {
            stream.filter(Files::isDirectory)
                    .forEach(path -> {
                        String normalized = path.toString().replace('\\', '/').toLowerCase(Locale.ROOT);
                        if (normalized.endsWith("/src/main/java")) {
                            roots.add(path);
                        }
                    });
        } catch (IOException e) {
            log.warn("Failed to detect source roots for {}", projectPath, e);
        }
        if (roots.isEmpty()) {
            roots.add(projectPath);
        }
        return roots;
    }

    private List<Path> resolveWithGradle(Path projectPath, List<String> warnings) {
        Path initScript;
        try {
            initScript = Files.createTempFile("extractor-init", ".gradle");
            Files.writeString(initScript, """
                    gradle.projectsEvaluated {
                        gradle.rootProject.allprojects.each { proj ->
                            if (proj.plugins.hasPlugin('java') || proj.plugins.hasPlugin('java-library')) {
                                def sourceSets = proj.extensions.findByName('sourceSets')
                                if (sourceSets != null && sourceSets.findByName('main') != null) {
                                    println 'EXTRACTOR_CLASSPATH=' + sourceSets.main.compileClasspath.asPath
                                }
                            }
                        }
                    }
                    """);
        } catch (IOException e) {
            warnings.add("Failed to create gradle init script: " + e.getMessage());
            return List.of();
        }

        boolean isWindows = System.getProperty("os.name", "").toLowerCase().contains("win");
        Path gradlew = projectPath.resolve(isWindows ? "gradlew.bat" : "gradlew");
        List<String> command;
        if (Files.exists(gradlew)) {
            command = List.of(gradlew.toAbsolutePath().toString(), "-q", "help", "-I", initScript.toString());
        } else {
            command = List.of("gradle", "-q", "help", "-I", initScript.toString());
        }

        ProcessBuilder pb = new ProcessBuilder(command)
                .directory(projectPath.toFile())
                .redirectErrorStream(true);
        try {
            Process process = pb.start();
            String stdout = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
            int exit = process.waitFor();
            if (exit != 0) {
                warnings.add("Gradle classpath resolution failed (exit " + exit + "): " + stdout);
                return List.of();
            }

            Set<Path> entries = new LinkedHashSet<>();
            int classpathLineCount = 0;
            for (String line : stdout.lines().toList()) {
                if (line.startsWith("EXTRACTOR_CLASSPATH=")) {
                    classpathLineCount++;
                    entries.addAll(splitClasspath(line.substring("EXTRACTOR_CLASSPATH=".length())));
                }
            }
            log.info("Found {} EXTRACTOR_CLASSPATH lines from {} subprojects, {} unique entries",
                    classpathLineCount, classpathLineCount, entries.size());
            if (classpathLineCount == 0) {
                warnings.add("No EXTRACTOR_CLASSPATH output from Gradle. Is the 'java' plugin applied?");
            }
            return new ArrayList<>(entries);
        } catch (IOException | InterruptedException e) {
            Thread.currentThread().interrupt();
            warnings.add("Gradle classpath resolution failed: " + e.getMessage());
            return List.of();
        } finally {
            try { Files.deleteIfExists(initScript); } catch (IOException ignored) {}
        }
    }

    private List<Path> resolveWithMaven(Path projectPath, List<String> warnings) {
        Path outputFile;
        try {
            outputFile = Files.createTempFile("extractor-maven-cp", ".txt");
        } catch (IOException e) {
            warnings.add("Failed to create temp file for maven classpath: " + e.getMessage());
            return List.of();
        }

        List<String> command = List.of(
                "mvn", "-q", "-DskipTests", "-DincludeScope=compile",
                "dependency:build-classpath", "-Dmdep.outputFile=" + outputFile
        );

        try {
            Process process = new ProcessBuilder(command)
                    .directory(projectPath.toFile())
                    .inheritIO()
                    .start();
            int exit = process.waitFor();
            if (exit != 0) {
                warnings.add("Maven classpath resolution failed with exit code " + exit);
                return List.of();
            }
            String content = Files.readString(outputFile, StandardCharsets.UTF_8).trim();
            return content.isBlank() ? List.of() : splitClasspath(content);
        } catch (IOException | InterruptedException e) {
            Thread.currentThread().interrupt();
            warnings.add("Maven classpath resolution failed: " + e.getMessage());
            return List.of();
        } finally {
            try { Files.deleteIfExists(outputFile); } catch (IOException ignored) {}
        }
    }

    private void addBuildOutputs(Path projectPath, Set<Path> classpath) {
        try (Stream<Path> stream = Files.walk(projectPath, 5)) {
            stream.filter(Files::isDirectory)
                    .forEach(path -> {
                        String normalized = path.toString().replace('\\', '/');
                        if (normalized.endsWith("/build/classes/java/main")
                                || normalized.endsWith("/build/resources/main")
                                || normalized.endsWith("/target/classes")) {
                            classpath.add(path);
                        }
                    });
        } catch (IOException e) {
            log.debug("Skipping build outputs scan for {}", projectPath, e);
        }
    }

    private List<Path> splitClasspath(String classpathLine) {
        if (classpathLine == null || classpathLine.isBlank()) return List.of();
        String separator = System.getProperty("path.separator", ":");
        String[] parts = classpathLine.split(Pattern.quote(separator));
        List<Path> result = new ArrayList<>();
        for (String part : parts) {
            if (!part.isBlank()) result.add(Path.of(part.trim()));
        }
        return result;
    }
}
