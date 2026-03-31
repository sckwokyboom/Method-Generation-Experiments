package com.experiment.retriever.model;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

public record SearchResponse(
        @JsonProperty("results") List<SearchResult> results,
        @JsonProperty("totalHits") int totalHits,
        @JsonProperty("searchTimeMs") long searchTimeMs,
        @JsonProperty("queryDebug") String queryDebug
) {}
