package com.experiment.retriever.api;

import java.nio.file.Path;
import java.util.Map;

/**
 * A single retrieval result: a scored reference to a source location
 * with content for augmentation and optional per-tag score breakdown.
 */
public interface IRetrievalResult {

    String getId();

    Path getLocation();

    float getScore();

    Map<TypeSourceRetrieveTag, Float> getTagScores();

    /**
     * Content to be used for augmentation. Every retrieval result must
     * provide this — it is the canonical source for what gets added to
     * the model prompt.
     *
     * <p>For method-level retrieval this is typically the full method body.
     * External retrievers may return arbitrary pre-formatted text.
     *
     * @return the content string, never {@code null}
     */
    String getContent();
}
