package com.experiment.retriever.model;

import com.fasterxml.jackson.annotation.JsonProperty;

public record SearchResult(
        @JsonProperty("id") String id,
        @JsonProperty("filePath") String filePath,
        @JsonProperty("classFqn") String classFqn,
        @JsonProperty("signature") String signature,
        @JsonProperty("methodBody") String methodBody,
        @JsonProperty("methodCard") String methodCard,
        @JsonProperty("invocationProfile") String invocationProfile,
        @JsonProperty("typeProfile") String typeProfile,
        @JsonProperty("score") float score,
        @JsonProperty("rank") int rank,
        @JsonProperty("explain") String explain,
        @JsonProperty("content") String content
) {}
