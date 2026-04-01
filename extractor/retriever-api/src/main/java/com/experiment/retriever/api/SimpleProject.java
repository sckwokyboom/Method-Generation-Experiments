package com.experiment.retriever.api;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.nio.file.Path;
import java.util.List;

/**
 * Jackson-deserializable implementation of {@link IProject}.
 * Used to pass project metadata from the Python pipeline to Java retrievers via JSON.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record SimpleProject(
        @JsonProperty("projectName") String projectName,
        @JsonProperty("projectPath") String projectPathStr,
        @JsonProperty("classpath") List<String> classpath,
        @JsonProperty("sourceRoots") List<String> sourceRootStrs
) implements IProject {

    @Override
    public String getProjectName() {
        return projectName;
    }

    @Override
    public Path getProjectPath() {
        return Path.of(projectPathStr);
    }

    @Override
    public List<String> getClasspath() {
        return classpath != null ? classpath : List.of();
    }

    @Override
    public List<Path> getSourceRoots() {
        if (sourceRootStrs == null) return List.of();
        return sourceRootStrs.stream().map(Path::of).toList();
    }
}
