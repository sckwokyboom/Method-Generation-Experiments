package com.experiment.retriever.api;

import java.nio.file.Path;
import java.util.Map;

/**
 * A single retrieval result: a scored reference to a source location
 * with optional per-tag score breakdown.
 */
public interface IRetrievalResult {

    String getId();

    Path getLocation();

    float getScore();

    Map<TypeSourceRetrieveTag, Float> getTagScores();

    /**
     * Optional pre-formatted content for augmentation.
     * When non-null, this text is used as-is in the prompt without
     * reconstruction from structured fields (signature, body, class, etc.).
     *
     * @return the content string, or {@code null} if not available
     */
    default String getContent() {
        return null;
    }
}
