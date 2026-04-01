package com.experiment.retriever.api;

import java.nio.file.Path;
import java.util.List;

/**
 * Represents a Java project for retriever construction.
 * Provides classpath, source roots, and project metadata needed
 * by retrieval services to resolve types and locate source files.
 */
public interface IProject {

    String getProjectName();

    Path getProjectPath();

    List<String> getClasspath();

    List<Path> getSourceRoots();
}
