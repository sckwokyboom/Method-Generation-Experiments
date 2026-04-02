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

    /**
     * Desired number of results. Implementations may use this as a hint.
     * Returns 0 when unset (retriever decides).
     */
    default int getTopK() {
        return 0;
    }
}
