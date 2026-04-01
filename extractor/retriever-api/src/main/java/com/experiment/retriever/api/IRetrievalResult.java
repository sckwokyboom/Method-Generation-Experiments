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
}
