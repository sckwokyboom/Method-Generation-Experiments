package com.experiment.retriever.api;

import java.nio.file.Path;

/**
 * Describes what to retrieve: a source file with a cursor offset
 * indicating the completion point, plus an optional user prompt.
 */
public interface IRetrieveRequest {

    String getSourceCode();

    int getOffset();

    String getUserPrompt();

    Path getLocation();
}
